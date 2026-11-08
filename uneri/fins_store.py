"""財務情報を日付ごとに取得してキャッシュする(15か月分)。"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .jquants import JQuants, JQuantsError
from .util import log, read_json_gz, write_json_gz

# 15か月。前年同期との比較に足りるよう、少し余裕を持たせる
FINS_DAYS = 470
SAVE_EVERY = 50


def update_fins(client: JQuants, cache_dir: Path, today: dt.date) -> list[dict]:
    path = cache_dir / "fins_by_date.json.gz"
    store: dict[str, list] = read_json_gz(path, {}) or {}
    end = client.latest_date(today)
    start = end - dt.timedelta(days=FINS_DAYS)
    # 最新2日分は後から増えることがあるので取り直す
    dates = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    todo = [d for d in dates if d.isoformat() not in store or d >= end - dt.timedelta(days=1)]
    log(f"財務: {len(dates)}日のうち {len(todo)}日を取得します")
    errors = 0
    for i, d in enumerate(todo, 1):
        try:
            store[d.isoformat()] = client.fins_on(d)
        except JQuantsError as e:
            errors += 1
            log(f"財務 {d}: {e}")
            if errors >= 10:
                log("財務: エラーが続くので打ち切り、取れた分だけ使います")
                break
        if i % SAVE_EVERY == 0:
            write_json_gz(path, store)
            log(f"財務: {i}/{len(todo)}日")
    keep = {d.isoformat() for d in dates}
    store = {k: v for k, v in store.items() if k in keep}
    write_json_gz(path, store)
    return [r for k in sorted(store) for r in store[k]]
