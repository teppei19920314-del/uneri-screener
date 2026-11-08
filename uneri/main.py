"""取得 → 計算 → 画面生成(暗号化して公開用フォルダへ)。

  python -m uneri.main            本番(環境変数 JQUANTS_API_KEY, SITE_PASSWORD)
  python -m uneri.main --demo     模擬データ(APIキー不要)

DATA_SOURCE=free(既定): 株価=Yahoo、信用残・空売り=JPX、財務・銘柄一覧=J-Quants無料
DATA_SOURCE=jquants    : スタンダードプラン。株価・信用残・空売りも J-Quants から取る
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from .compute import Inputs, build
from .packer import write_encrypted
from .util import log, read_json_gz, today_jst, write_json_gz

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
MIN_STOCKS = 500


def collect(cache: Path, source: str) -> tuple[Inputs, dict]:
    from . import fins_store, fred, jpx
    from .jquants import JQuants, JQuantsError

    cache.mkdir(parents=True, exist_ok=True)
    today = today_jst()
    status: dict[str, str] = {}
    client = JQuants(os.environ.get("JQUANTS_API_KEY", ""), plan="free" if source == "free" else "standard")

    # 銘柄一覧(これがないと始まらないが、前回分があればそれで動く)
    mpath = cache / "master.json.gz"
    try:
        master = client.master(client.latest_date(today))
        if not master:
            raise JQuantsError("銘柄一覧が空です")
        write_json_gz(mpath, master)
        status["銘柄一覧"] = f"OK {len(master)}件"
    except JQuantsError as e:
        master = read_json_gz(mpath)
        status["銘柄一覧"] = f"失敗→前回分: {e}"
        log(status["銘柄一覧"])
        if not master:
            raise SystemExit(f"銘柄一覧を取得できず、前回分もありません: {e}")

    codes = sorted({r["Code"][:4] for r in master if r.get("Code", "")[4:] in ("0", "")
                    and any(m in (r.get("MktNm") or "") for m in ("プライム", "スタンダード", "グロース"))})
    log(f"対象 {len(codes)}銘柄")

    try:
        fins = fins_store.update_fins(client, cache, today)
        status["財務"] = f"OK {len(fins)}件"
    except Exception as e:
        fins = []
        status["財務"] = f"失敗: {e}"
        log(status["財務"])

    if source == "jquants":
        from . import jq_prices

        daily_bars = jq_prices.update_bars(client, cache, today)
        pnl, daily = jq_prices.bars_to_panels(daily_bars)
        status["株価"] = f"J-Quants {len(daily)}銘柄"
        margin = _safe(status, "信用残", lambda: jq_prices.update_margin(client, cache, today))
        shorts = _safe(status, "空売り", lambda: jq_prices.update_shorts(client, cache, today))
    else:
        from . import yahoo

        pnl, daily, reused = yahoo.fetch_prices(codes, cache)
        status["株価"] = f"Yahoo {pnl['c'].shape[1]}銘柄(前回分 {reused})"
        j = jpx.JpxClient()
        margin = _safe(status, "信用残", lambda: jpx.update_margin(j, cache, today))
        if margin is None:
            margin = read_json_gz(cache / "margin_by_date.json.gz")
        shorts = _safe(status, "空売り", lambda: jpx.update_shorts(j, cache, today))
        if shorts is None:
            prev = read_json_gz(cache / "shorts.json.gz")
            shorts = prev["rows"] if prev else None

    fred_data = _safe(status, "金利・景気", lambda: fred.fetch_all(cache))
    return Inputs(master=master, panels=pnl, daily=daily, fins=fins, margin=margin, shorts=shorts,
                  fred=fred_data, source="free" if source == "free" else "jquants", today=today), status


def _safe(status: dict, name: str, fn):
    """データ源ごとに失敗しても他は動くようにする。"""
    try:
        res = fn()
        status[name] = "OK"
        return res
    except Exception as e:
        status[name] = f"失敗: {type(e).__name__}: {e}"
        log(f"{name}: {status[name]}")
        return None


def publish(data: dict, out: Path, password: str | None) -> None:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(SITE, out)
    (out / ".nojekyll").write_text("")
    if password:
        size = write_encrypted(out, data, password)
        log(f"公開用データ: {len(data['stocks'])}銘柄、暗号文 {size / 1e6:.2f}MB")
    else:
        # 平文で書くのは架空のデモだけ
        if not data.get("demo"):
            raise SystemExit("パスワードなしで本番データは公開しません")
        (out / "data.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
                                       encoding="utf-8")


def summary(data: dict, status: dict) -> None:
    lines = [f"- 基準日 {data['asof']}、{len(data['stocks'])}銘柄"] + [f"- {k}: {v}" for k, v in status.items()]
    text = "\n".join(lines)
    log("結果\n" + text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("### データ更新の結果\n" + text + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--demo", action="store_true", help="架空のデータで動かす(APIキー不要)")
    ap.add_argument("--demo-n", type=int, default=260)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--cache", type=Path, default=ROOT / "cache")
    args = ap.parse_args(argv)
    password = os.environ.get("SITE_PASSWORD") or None

    if args.demo:
        from . import demo

        inp, status = demo.make(n=args.demo_n, today=today_jst()), {"模擬データ": "OK"}
        data = build(inp)
        publish(data, args.out or ROOT / "public-demo", password)
    else:
        # 平文で公開しないよう、何より先に確認して止める
        if not password:
            print("SITE_PASSWORD が設定されていないため停止します(平文では公開しません)。", file=sys.stderr)
            return 2
        source = os.environ.get("DATA_SOURCE", "free").strip().lower() or "free"
        if source not in ("free", "jquants"):
            print(f"DATA_SOURCE の値が不正です: {source}", file=sys.stderr)
            return 2
        if not os.environ.get("JQUANTS_API_KEY"):
            print("JQUANTS_API_KEY が設定されていないため停止します。", file=sys.stderr)
            return 2
        inp, status = collect(args.cache, source)
        data = build(inp)
        if len(data["stocks"]) < MIN_STOCKS:
            # 前回の公開を薄いデータで上書きしない
            print(f"銘柄数が少なすぎるため公開しません: {len(data['stocks'])}", file=sys.stderr)
            summary(data, status)
            return 1
        publish(data, args.out or ROOT / "public", password)
    summary(data, status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
