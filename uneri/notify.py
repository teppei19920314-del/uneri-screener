"""プッシュ通知: 毎日の更新のあとに、★銘柄と購入予定を判定してiPhoneへ知らせる。

  python -m uneri.notify

必要な設定(GitHub)
  Secrets   VAPID_PRIVATE_KEY … アプリの「お知らせ」→「通知用の鍵を作る」で作った鍵
            NOTIFY_EMAIL / NOTIFY_PASSWORD … ロット管理(Firebase)にログインするメールアドレスとパスワード
  Variables FIREBASE_CONFIG … ロット管理の設定

前日から続いている状態では送らず、新しく当てはまったときだけ送る。
どれかが足りなければ何もしない(更新そのものは止めない)。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path

import requests

from .util import log, read_json_gz, write_json_gz

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
INPUT = WORK / "notify-input.json.gz"
TTL = 12 * 3600


# ---------------------------------------------------------------- 判定
def stock_fields(s: dict) -> dict:
    """通知の判定に使う項目だけ(公開用のデータから取り出して保存する)。"""
    return {k: s.get(k) for k in ("c", "n", "p", "pos", "sup", "ma", "x10")}


def yen(v) -> str:
    if v is None:
        return "–"
    return f"{v:,.1f}円" if v < 100 else f"{round(v):,}円"


def evaluate(stocks: dict[str, dict], watch: list[str], lots: list[dict],
             targets: dict | None = None) -> list[dict]:
    """いま当てはまっている条件。key が同じものは、続いている間は1回だけ通知する。
    targets は ★銘柄ごとの目標価格({コード: 円})。"""
    out = []
    targets = targets or {}
    for code in dict.fromkeys(watch):
        s = stocks.get(code)
        if not s or s.get("p") is None:
            continue
        span = "上場来" if s.get("x10") else "10年"
        base = {"code": code, "name": s.get("n") or code}
        pos, sup, p = s.get("pos"), s.get("sup"), s["p"]
        if sup is not None and abs(sup) <= 0.15:
            valley = p / (1 + sup)
            out.append({**base, "key": f"{code}:valley", "kind": "watch",
                        "msg": f"過去の谷の±15%に入りました(谷 {yen(valley)}・現在 {yen(p)})"})
        if pos is not None and pos <= 0.15:
            out.append({**base, "key": f"{code}:pos15", "kind": "watch",
                        "msg": f"{span}レンジの位置が{pos * 100:.0f}%になりました(安値圏)"})
        if s.get("ma") and pos is not None and pos <= 0.30:
            out.append({**base, "key": f"{code}:ma", "kind": "watch",
                        "msg": f"13週線を上回りました(反転サイン・{span}位置 {pos * 100:.0f}%)"})
        t = targets.get(code)
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0 and p <= t:
            out.append({**base, "key": f"{code}:wtarget:{t:g}", "kind": "wtarget",
                        "msg": f"★の目標 {yen(t)}に届きました(現在 {yen(p)})"})
    for lot in lots:
        code, target = lot.get("code"), lot.get("target")
        if lot.get("done") or not code or not target or target <= 0:
            continue
        s = stocks.get(code)
        if not s or s.get("p") is None or s["p"] > target:
            continue
        out.append({"code": code, "name": s.get("n") or code, "kind": "target",
                    "key": f"{lot.get('id')}:target:{target:g}",
                    "msg": f"目標の{yen(target)}に届きました(現在 {yen(s['p'])})"})
    return out


def message(new: list[dict], asof: str) -> dict:
    watch = {i["code"] for i in new if i["kind"] == "watch"}
    target = [i for i in new if i["kind"] == "target"]
    wtarget = {i["code"] for i in new if i["kind"] == "wtarget"}
    parts = []
    if wtarget:
        parts.append(f"★{len(wtarget)}銘柄が目標価格に到達")
    if watch:
        parts.append(f"★{len(watch)}銘柄が買い場に")
    if target:
        parts.append(f"予定{len(target)}件が目標価格に到達")
    names = list(dict.fromkeys(i["name"] for i in new))
    body = "・".join(parts) + "\n" + "、".join(names[:4]) + (f" ほか{len(names) - 4}銘柄" if len(names) > 4 else "")
    m, d = asof[5:7].lstrip("0"), asof[8:10].lstrip("0")
    return {"title": f"うねり底 {m}/{d}", "body": body, "url": "index.html#alerts", "tag": "uneri-daily"}


# ---------------------------------------------------------------- Firestore(REST)
def decode(v: dict):
    if "nullValue" in v:
        return None
    for k in ("stringValue", "booleanValue", "timestampValue"):
        if k in v:
            return v[k]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "mapValue" in v:
        return {k: decode(x) for k, x in (v["mapValue"].get("fields") or {}).items()}
    if "arrayValue" in v:
        return [decode(x) for x in v["arrayValue"].get("values") or []]
    return None


def encode(x) -> dict:
    if x is None:
        return {"nullValue": None}
    if isinstance(x, bool):
        return {"booleanValue": x}
    if isinstance(x, int):
        return {"integerValue": str(x)}
    if isinstance(x, float):
        return {"doubleValue": x}
    if isinstance(x, str):
        return {"stringValue": x}
    if isinstance(x, dict):
        return {"mapValue": {"fields": {k: encode(v) for k, v in x.items()}}}
    if isinstance(x, (list, tuple)):
        return {"arrayValue": {"values": [encode(v) for v in x]}}
    raise TypeError(type(x))


class Firestore:
    """ログインした本人として読み書きする(セキュリティルールがそのまま効く)。"""

    def __init__(self, project: str, token: str, session=None):
        self.base = f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents"
        self.s = session or requests.Session()
        self.h = {"Authorization": f"Bearer {token}"}

    def get(self, path: str) -> dict | None:
        r = self.s.get(f"{self.base}/{path}", headers=self.h, timeout=30)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return {k: decode(v) for k, v in (r.json().get("fields") or {}).items()}

    def list(self, path: str) -> list[tuple[str, dict]]:
        out, token = [], None
        while True:
            params = {"pageSize": 300, **({"pageToken": token} if token else {})}
            r = self.s.get(f"{self.base}/{path}", headers=self.h, params=params, timeout=30)
            if r.status_code == 404:
                return out
            r.raise_for_status()
            body = r.json()
            for d in body.get("documents") or []:
                out.append((d["name"].rsplit("/", 1)[-1], {k: decode(v) for k, v in (d.get("fields") or {}).items()}))
            token = body.get("nextPageToken")
            if not token:
                return out

    def set(self, path: str, data: dict) -> None:
        r = self.s.patch(f"{self.base}/{path}", headers=self.h, json={"fields": {k: encode(v) for k, v in data.items()}},
                         timeout=30)
        r.raise_for_status()

    def delete(self, path: str) -> None:
        self.s.delete(f"{self.base}/{path}", headers=self.h, timeout=30)


def sign_in(api_key: str, email: str, password: str, session=None) -> tuple[str, str]:
    import unicodedata

    # Secrets に貼り付けたときに入りやすい、前後の空白・改行・全角の英数字を取り除く
    email = unicodedata.normalize("NFKC", email or "").strip()
    password = (password or "").strip("\r\n")
    s = session or requests.Session()
    r = s.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={api_key}",
               json={"email": email, "password": password, "returnSecureToken": True}, timeout=30)
    if r.status_code != 200:
        msg = (r.json().get("error") or {}).get("message", "") if r.headers.get("content-type", "").startswith("application/json") else ""
        raise RuntimeError(f"Firebaseにログインできません({msg or r.status_code})。NOTIFY_EMAIL と NOTIFY_PASSWORD を確認してください")
    body = r.json()
    return body["idToken"], body["localId"]


# ---------------------------------------------------------------- 鍵
def b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def public_key(private_raw_b64: str) -> str:
    """VAPIDの秘密鍵(P-256の32バイト、base64url)から、ブラウザに渡す公開鍵を作る。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    raw = base64.urlsafe_b64decode(private_raw_b64.strip() + "=" * (-len(private_raw_b64.strip()) % 4))
    if len(raw) != 32:
        raise ValueError("VAPID_PRIVATE_KEY の形式が違います")
    key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    return b64url(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def sub_id(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()[:24]


# ---------------------------------------------------------------- 送信
def send(subs: list[tuple[str, dict]], payload: dict, vapid_key: str, contact: str, webpush=None) -> tuple[int, list[str]]:
    """(送れた数, 期限切れで消すべき受け取り先のID)"""
    if webpush is None:
        from pywebpush import webpush
    from py_vapid import Vapid02
    from pywebpush import WebPushException

    vapid = Vapid02.from_raw(vapid_key.strip().encode())
    ok, gone = 0, []
    for sid, sub in subs:
        info = {"endpoint": sub.get("endpoint"), "keys": sub.get("keys") or {}}
        if not info["endpoint"]:
            continue
        try:
            webpush(info, data=json.dumps(payload, ensure_ascii=False), vapid_private_key=vapid,
                    vapid_claims={"sub": contact}, ttl=TTL, timeout=30)
            ok += 1
        except WebPushException as e:
            code = getattr(e.response, "status_code", None)
            if code in (404, 410):
                gone.append(sid)
            else:
                log(f"通知を送れませんでした({code}): {str(e)[:120]}")
    return ok, gone


def run(env=None, session=None, webpush=None) -> str:
    env = os.environ if env is None else env
    from .main import parse_firebase_config

    cfg = parse_firebase_config(env.get("FIREBASE_CONFIG"))
    need = {"VAPID_PRIVATE_KEY": env.get("VAPID_PRIVATE_KEY"), "NOTIFY_EMAIL": env.get("NOTIFY_EMAIL"),
            "NOTIFY_PASSWORD": env.get("NOTIFY_PASSWORD"), "FIREBASE_CONFIG": cfg}
    missing = [k for k, v in need.items() if not v]
    if missing:
        return "未設定のため送りません(" + "・".join(missing) + ")"
    data = read_json_gz(INPUT)
    if not data:
        return "判定用のデータがないため送りません"
    stocks = {s["c"]: s for s in data["stocks"]}
    asof = data["asof"]

    token, uid = sign_in(cfg["apiKey"], env["NOTIFY_EMAIL"], env["NOTIFY_PASSWORD"], session)
    fs = Firestore(cfg["projectId"], token, session)
    base = f"users/{uid}"
    watch = (fs.get(f"{base}/settings/watch") or {}).get("codes") or []
    targets = (fs.get(f"{base}/settings/watchTargets") or {}).get("t") or {}
    lots = [{**d, "id": i} for i, d in fs.list(f"{base}/lots")]
    state = (fs.get(f"{base}/settings/alertState") or {}).get("keys") or {}
    subs = fs.list(f"{base}/push")
    if not subs:
        # 端末がないうちは「通知済み」にしない(オンにした後で、いま当てはまっているものも届くように)
        return "通知をオンにした端末がないため、判定だけで終わりました"

    now = evaluate(stocks, watch, lots, targets)
    new = [i for i in now if i["key"] not in state]
    fs.set(f"{base}/settings/alertState", {"keys": {i["key"]: state.get(i["key"], asof) for i in now}})
    if not new:
        return f"新しく当てはまったものはありません(★{len(watch)}・予定{sum(1 for l in lots if not l.get('done'))}件を確認)"

    day = fs.get(f"{base}/alerts/{asof}") or {}
    items = {i["key"]: i for i in (day.get("items") or [])}
    items.update({i["key"]: i for i in new})
    fs.set(f"{base}/alerts/{asof}", {"date": asof, "items": list(items.values())})

    # 送信元の連絡先(通知サービスの決まりで「https://ドメイン」だけの形にする)
    owner = (env.get("GITHUB_REPOSITORY_OWNER") or "").lower()
    contact = f"https://{owner}.github.io" if owner else "https://github.com"
    ok, gone = send(subs, message(new, asof), env["VAPID_PRIVATE_KEY"], contact, webpush)
    for sid in gone:
        fs.delete(f"{base}/push/{sid}")
    return f"{len(new)}件を{ok}台の端末に通知しました" + (f"(使われなくなった端末 {len(gone)} を削除)" if gone else "")


def save_input(data: dict) -> None:
    write_json_gz(INPUT, {"asof": data["asof"], "stocks": [stock_fields(s) for s in data["stocks"]]})


def main() -> int:
    try:
        result = run()
    except Exception as e:  # 通知の失敗で更新全体を失敗にしない
        result = f"失敗: {e}"
    log("プッシュ通知: " + result)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"- プッシュ通知: {result}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
