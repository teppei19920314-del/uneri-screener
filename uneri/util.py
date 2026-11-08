"""共通の小道具: 日付(JST)、数値の丸め、キャッシュの読み書き、ログ。"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import math
import os
import tempfile
import time
from pathlib import Path

JST = dt.timezone(dt.timedelta(hours=9))

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
)


def now_jst() -> dt.datetime:
    return dt.datetime.now(JST)


def today_jst() -> dt.date:
    return now_jst().date()


def log(msg: str) -> None:
    print(f"[{now_jst():%H:%M:%S}] {msg}", flush=True)


def rnd(x, d: int = 4):
    """JSONに入れられる丸めた値。NaN・無限大・None は None にする。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return round(v, d)


def chart_price(x):
    """チャート用の株価: 100円以上は整数、未満は小数1桁。"""
    v = rnd(x, 1)
    if v is None:
        return None
    return int(round(v)) if abs(v) >= 100 else v


def num(x):
    """J-Quants の文字列の数値を float に。空文字や '-' は None。"""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return None if isinstance(x, float) and math.isnan(x) else float(x)
    s = str(x).strip().replace(",", "")
    if s in ("", "-", "－", "−", "―", "None", "null"):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return None if math.isnan(v) or math.isinf(v) else v


def code4(code) -> str | None:
    """5桁の銘柄コード(末尾0)を4桁に。優先株など末尾が0でないものは None。"""
    c = str(code).strip()
    if c.endswith(".0"):
        c = c[:-2]
    if len(c) == 4:
        return c
    if len(c) == 5 and c.endswith("0"):
        return c[:4]
    return None


def read_json_gz(path: Path, default=None):
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (OSError, EOFError, ValueError) as e:
        log(f"キャッシュが壊れているので作り直します: {path.name} ({e})")
        return default


def write_json_gz(path: Path, obj) -> None:
    """途中で止まっても壊れないよう、一時ファイルに書いてから置き換える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    os.close(fd)
    try:
        with gzip.GzipFile(tmp, "wb", compresslevel=6, mtime=0) as gz:
            gz.write(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class Throttle:
    """最低間隔を守って呼び出すための待ち合わせ。"""

    def __init__(self, min_interval: float, sleep=time.sleep, clock=time.monotonic):
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last = None

    def wait(self) -> None:
        if self._last is not None:
            rest = self.min_interval - (self._clock() - self._last)
            if rest > 0:
                self._sleep(rest)
        self._last = self._clock()
