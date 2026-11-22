"""プッシュ通知: 判定・鍵・暗号化した通知の中身・Firestoreとのやりとり。"""
import base64
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from uneri import main, notify

STOCKS = {
    "1111": {"c": "1111", "n": "谷の会社", "p": 1050.0, "pos": 0.4, "sup": 0.05, "ma": False},
    "2222": {"c": "2222", "n": "安値の会社", "p": 80.0, "pos": 0.1, "sup": 0.6, "ma": True, "x10": True},
    "3333": {"c": "3333", "n": "高い会社", "p": 3000.0, "pos": 0.9, "sup": None, "ma": True},
}


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def new_vapid():
    k = ec.generate_private_key(ec.SECP256R1())
    return b64(k.private_numbers().private_value.to_bytes(32, "big")), k


class Evaluate(unittest.TestCase):
    def test_conditions(self):
        lots = [{"id": "a", "code": "3333", "target": 3100, "done": False},
                {"id": "b", "code": "3333", "target": 2000, "done": False},
                {"id": "c", "code": "1111", "target": 2000, "done": True},
                {"id": "d", "code": "1111", "target": None, "done": False}]
        got = {i["key"]: i for i in notify.evaluate(STOCKS, ["1111", "2222", "3333", "9999", "1111"], lots)}
        self.assertEqual(sorted(got), ["1111:valley", "2222:ma", "2222:pos15", "a:target:3100"])
        self.assertIn("谷 1,000円", got["1111:valley"]["msg"])
        self.assertIn("上場来", got["2222:pos15"]["msg"])
        self.assertIn("目標の3,100円", got["a:target:3100"]["msg"])

    def test_watch_targets(self):
        # ★の目標価格: 現在値が目標以下で通知。★を外した銘柄・0・文字は無視する
        targets = {"3333": 3000, "1111": 1000, "2222": 0, "9999": 50, "4444": "x"}
        got = {i["key"]: i for i in notify.evaluate(STOCKS, ["3333", "1111"], [], targets)}
        self.assertIn("3333:wtarget:3000", got)
        self.assertNotIn("1111:wtarget:1000", got)
        self.assertEqual(got["3333:wtarget:3000"]["kind"], "wtarget")
        self.assertIn("★の目標 3,000円", got["3333:wtarget:3000"]["msg"])
        m = notify.message(list(got.values()), "2026-11-21")
        self.assertIn("★1銘柄が目標価格に到達", m["body"])

    def test_message(self):
        items = notify.evaluate(STOCKS, ["1111", "2222"], [{"id": "a", "code": "3333", "target": 3100}])
        m = notify.message(items, "2026-11-21")
        self.assertEqual(m["title"], "うねり底 11/21")
        self.assertIn("★2銘柄が買い場に", m["body"])
        self.assertIn("予定1件が目標価格に到達", m["body"])
        self.assertEqual(m["url"], "index.html#alerts")
        self.assertLess(len(json.dumps(m, ensure_ascii=False).encode()), 3000)


class Keys(unittest.TestCase):
    def test_public_key_from_private(self):
        raw, k = new_vapid()
        expect = b64(k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        self.assertEqual(notify.public_key(raw), expect)
        self.assertEqual(len(base64.urlsafe_b64decode(expect + "==")), 65)
        with self.assertRaises(ValueError):
            notify.public_key(b64(b"short"))

    def test_push_config_written_without_secrets(self):
        raw, _ = new_vapid()
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            self.assertTrue(main.write_push_config(out, {"VAPID_PRIVATE_KEY": raw, "NOTIFY_EMAIL": "a@b", "NOTIFY_PASSWORD": "pw"}))
            js = (out / "push-config.js").read_text()
            self.assertIn(notify.public_key(raw), js)
            self.assertIn('"ready": true', js)
            self.assertNotIn(raw, js)
            self.assertNotIn("pw", js.replace("push-config", ""))
            self.assertFalse(main.write_push_config(out, {"VAPID_PRIVATE_KEY": "壊れた鍵"}))
            self.assertFalse(main.write_push_config(out, {}))


class Capture(BaseHTTPRequestHandler):
    got = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        Capture.got.append((dict(self.headers), body))
        self.send_response(201)
        self.end_headers()

    def log_message(self, *a):
        pass


class RealEncryption(unittest.TestCase):
    """本物の pywebpush で暗号化して送り、受け取り側の鍵で復号できることを確かめる。"""

    def test_payload_decrypts(self):
        import http_ece

        srv = HTTPServer(("127.0.0.1", 0), Capture)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            client = ec.generate_private_key(ec.SECP256R1())
            auth = os.urandom(16)
            sub = {"endpoint": f"http://127.0.0.1:{srv.server_port}/push/x",
                   "keys": {"p256dh": b64(client.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)),
                            "auth": b64(auth)}}
            raw, _ = new_vapid()
            payload = notify.message(notify.evaluate(STOCKS, ["1111"], []), "2026-11-21")
            ok, gone = notify.send([("s1", sub)], payload, raw, "https://someone.github.io")
            self.assertEqual((ok, gone), (1, []))
            headers, body = Capture.got[-1]
            headers = {k.lower(): v for k, v in headers.items()}
            plain = http_ece.decrypt(body, private_key=client, auth_secret=auth, version="aes128gcm")
            self.assertEqual(json.loads(plain), payload)
            self.assertIn(f"k={notify.public_key(raw)}", headers["authorization"])
            self.assertEqual(headers["ttl"], str(notify.TTL))
            self.assertEqual(headers["content-encoding"], "aes128gcm")
        finally:
            srv.shutdown()


class FakeResp:
    def __init__(self, code, body=None):
        self.status_code, self._b = code, body or {}
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._b

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeGoogle:
    """identitytoolkit と Firestore REST の代わり。"""

    def __init__(self, docs):
        self.docs = docs  # path -> dict(Python値)
        self.base = "https://firestore.googleapis.com/v1/projects/p/databases/(default)/documents/"

    def post(self, url, json=None, timeout=None):
        if json["email"] != "me@example.invalid":
            return FakeResp(400, {"error": {"message": "INVALID_EMAIL"}})
        if json["password"] != "good":
            return FakeResp(400, {"error": {"message": "INVALID_LOGIN_CREDENTIALS"}})
        return FakeResp(200, {"idToken": "tok", "localId": "U1"})

    def get(self, url, headers=None, params=None, timeout=None):
        assert headers["Authorization"] == "Bearer tok"
        path = url[len(self.base):]
        if path in self.docs:
            return FakeResp(200, {"fields": {k: notify.encode(v) for k, v in self.docs[path].items()}})
        kids = [p for p in self.docs if p.startswith(path + "/") and "/" not in p[len(path) + 1:]]
        if kids:
            return FakeResp(200, {"documents": [{"name": self.base + p, "fields": {k: notify.encode(v) for k, v in self.docs[p].items()}} for p in kids]})
        return FakeResp(404)

    def patch(self, url, headers=None, json=None, timeout=None):
        self.docs[url[len(self.base):]] = {k: notify.decode(v) for k, v in json["fields"].items()}
        return FakeResp(200)

    def delete(self, url, headers=None, timeout=None):
        self.docs.pop(url[len(self.base):], None)
        return FakeResp(200)


class Run(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(notify, "INPUT", Path(self.dir.name) / "in.json.gz")
        p.start()
        self.addCleanup(p.stop)
        notify.save_input({"asof": "2026-11-21", "stocks": [{**s, "extra": 1} for s in STOCKS.values()]})
        self.raw, _ = new_vapid()
        self.env = {"FIREBASE_CONFIG": '{apiKey: "k", projectId: "p", appId: "1:2:web:3"}', "VAPID_PRIVATE_KEY": self.raw,
                    "NOTIFY_EMAIL": "me@example.invalid", "NOTIFY_PASSWORD": "good", "GITHUB_REPOSITORY_OWNER": "Teppei-X"}
        self.docs = {
            "users/U1/settings/watch": {"codes": ["1111", "3333"]},
            "users/U1/lots/L1": {"code": "3333", "target": 3100.0, "done": False},
            "users/U1/push/S1": {"endpoint": "https://push.example/1", "keys": {"p256dh": "x", "auth": "y"}},
            "users/U1/push/S2": {"endpoint": "https://push.example/2", "keys": {"p256dh": "x", "auth": "y"}},
        }
        self.sent = []

    def fake_webpush(self, info, data=None, **kw):
        from pywebpush import WebPushException

        self.sent.append((info["endpoint"], json.loads(data), kw["vapid_claims"]))
        if info["endpoint"].endswith("/2"):
            raise WebPushException("gone", response=FakeResp(410))

    def run_once(self):
        return notify.run(self.env, session=FakeGoogle(self.docs), webpush=self.fake_webpush)

    def test_first_and_second_day(self):
        r = self.run_once()
        self.assertIn("2件を1台の端末に通知しました", r)
        self.assertIn("使われなくなった端末 1", r)
        self.assertNotIn("users/U1/push/S2", self.docs)  # 期限切れの端末を削除
        self.assertEqual(self.sent[0][2], {"sub": "https://teppei-x.github.io"})
        self.assertIn("予定1件", self.sent[0][1]["body"])
        day = self.docs["users/U1/alerts/2026-11-21"]
        self.assertEqual(sorted(i["key"] for i in day["items"]), ["1111:valley", "L1:target:3100"])
        self.assertEqual(set(self.docs["users/U1/settings/alertState"]["keys"]), {"1111:valley", "L1:target:3100"})
        # 2日目: 同じ状態が続いていれば送らない
        self.sent.clear()
        r = self.run_once()
        self.assertIn("新しく当てはまったものはありません", r)
        self.assertEqual(self.sent, [])
        # 条件から外れたら状態から消え、また当てはまれば再び通知する
        self.docs["users/U1/settings/watch"] = {"codes": []}
        self.run_once()
        self.assertEqual(set(self.docs["users/U1/settings/alertState"]["keys"]), {"L1:target:3100"})
        self.docs["users/U1/settings/watch"] = {"codes": ["1111"]}
        self.assertIn("1件を1台", self.run_once())

    def test_email_cleanup(self):
        self.env["NOTIFY_EMAIL"] = " ｍｅ@example.invalid\n"
        self.env["NOTIFY_PASSWORD"] = "good\n"
        self.assertIn("件を1台の端末に通知しました", self.run_once())

    def test_missing_settings_and_bad_login(self):
        self.assertIn("未設定", notify.run({**self.env, "VAPID_PRIVATE_KEY": ""}))
        with self.assertRaises(RuntimeError) as e:
            notify.run({**self.env, "NOTIFY_PASSWORD": "bad"}, session=FakeGoogle(self.docs))
        self.assertIn("INVALID_LOGIN_CREDENTIALS", str(e.exception))

    def test_no_devices_keeps_alerts_for_later(self):
        devices = {k: self.docs.pop(k) for k in ("users/U1/push/S1", "users/U1/push/S2")}
        self.assertIn("通知をオンにした端末がない", self.run_once())
        self.assertNotIn("users/U1/settings/alertState", self.docs)  # 通知済みにしない
        self.docs.update(devices)
        self.assertIn("2件を1台の端末に通知しました", self.run_once())

    def test_main_never_fails(self):
        with mock.patch.object(notify, "run", side_effect=RuntimeError("x")):
            self.assertEqual(notify.main(), 0)

    def test_encode_decode(self):
        v = {"a": 1, "b": 1.5, "c": "x", "d": [True, None], "e": {"f": "g"}}
        self.assertEqual(notify.decode(notify.encode(v)), v)


class Workflow(unittest.TestCase):
    def test_secrets_wired(self):
        y = (Path(__file__).resolve().parent.parent / ".github/workflows/update.yml").read_text(encoding="utf-8")
        for k in ("VAPID_PRIVATE_KEY", "NOTIFY_EMAIL", "NOTIFY_PASSWORD"):
            self.assertEqual(y.count(f"secrets.{k}"), 2, k)
        self.assertLess(y.index("deploy-pages"), y.index("python -m uneri.notify"))
        self.assertIn("pywebpush", (Path(__file__).resolve().parent.parent / "requirements.txt").read_text())


if __name__ == "__main__":
    unittest.main()
