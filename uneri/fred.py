"""FRED の CSV(登録不要)。失敗したら前回の値を使う。"""
from __future__ import annotations

import csv
import io
import time
from pathlib import Path

import requests

from .util import log, read_json_gz, write_json_gz

URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={}"
SERIES = ("DGS10", "FEDFUNDS", "CFNAIMA3", "UNRATE", "IRLTLT01JPM156N")


def parse_csv(text: str) -> list[list]:
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2 or len(rows[0]) < 2:
        raise ValueError("FRED: CSVの形式が想定と違います")
    out = []
    for r in rows[1:]:
        if len(r) < 2:
            continue
        try:
            out.append([r[0], float(r[1])])
        except ValueError:
            continue  # 祝日などの "." や空欄
    return out


def fetch_all(cache_dir: Path, session=None, sleep=time.sleep) -> dict:
    s = session or requests.Session()
    path = cache_dir / "fred.json.gz"
    store = read_json_gz(path, {}) or {}
    for sid in SERIES:
        for attempt in range(3):
            try:
                r = s.get(URL.format(sid), timeout=60)
                r.raise_for_status()
                vals = parse_csv(r.text)
                if vals:
                    store[sid] = vals
                    break
                raise ValueError("データが空です")
            except (requests.RequestException, ValueError) as e:
                log(f"FRED {sid}: {e}")
                sleep(5 * (attempt + 1))
        sleep(1)
    write_json_gz(path, store)
    return store
