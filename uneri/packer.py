"""JSONをgzipしてAES-256-GCMで暗号化する。鍵はPBKDF2-SHA256(250,000回)。"""
from __future__ import annotations

import base64
import gzip
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ITERATIONS = 250_000


def _key(password: str, salt: bytes, iterations: int = ITERATIONS) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return kdf.derive(password.encode("utf-8"))


def seal(data: dict, password: str) -> tuple[bytes, dict]:
    """(暗号文, data.enc の中身) を返す。"""
    if not password:
        raise ValueError("パスワードが空です")
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(password, salt)).encrypt(iv, gz, None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return ct, {"v": 2, "alg": "AES-256-GCM", "kdf": "PBKDF2-SHA256", "iter": ITERATIONS,
                "salt": b64(salt), "iv": b64(iv), "bin": "data.bin"}


def unseal(ct: bytes, env: dict, password: str) -> dict:
    key = _key(password, base64.b64decode(env["salt"]), env["iter"])
    gz = AESGCM(key).decrypt(base64.b64decode(env["iv"]), ct, None)
    return json.loads(gzip.decompress(gz))


def write_encrypted(out: Path, data: dict, password: str) -> int:
    ct, env = seal(data, password)
    (out / "data.bin").write_bytes(ct)
    (out / "data.enc").write_text(json.dumps(env), encoding="utf-8")
    return len(ct)
