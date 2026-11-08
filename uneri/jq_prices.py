"""有料プラン(DATA_SOURCE=jquants)用: 株価・信用残・空売りを J-Quants から取る。

株価の分割調整は AdjFactor から自前で計算する(権利落ち日に 1:2 分割なら 0.5)。
過去日の価格には、その日より新しい日付の係数をすべて掛ける。
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from .jquants import JQuants, JQuantsError
from .util import code4, log, read_json_gz, write_json_gz
from .yahoo import daily_stats, panels


def adjust(df: pd.DataFrame) -> pd.DataFrame:
    """日足(C,H,L,Vo,AdjFactor)を分割調整して Close/High/Low/Volume にする。"""
    df = df.sort_index()
    f = df["AdjFactor"].fillna(1.0).to_numpy(dtype=float)
    # その日より後の係数の累積積
    cum = np.cumprod(f[::-1])[::-1]
    later = np.append(cum[1:], 1.0)
    later = np.where(later > 0, later, 1.0)
    return pd.DataFrame(
        {"Close": df["C"] * later, "High": df["H"] * later, "Low": df["L"] * later, "Volume": df["Vo"] / later},
        index=df.index,
    )


def _year_path(cache_dir: Path, year: int) -> Path:
    return cache_dir / "jq_bars" / f"{year}.json.gz"


def update_bars(client: JQuants, cache_dir: Path, today: dt.date, years: int = 10) -> dict[str, pd.DataFrame]:
    """日付ごとの全銘柄の日足を年別に保存する。初回だけ時間がかかる。"""
    start = today - dt.timedelta(days=int(365.25 * years) + 10)
    frames: dict[str, list] = {}
    for year in range(start.year, today.year + 1):
        path = _year_path(cache_dir, year)
        store = read_json_gz(path, {}) or {}
        first = max(dt.date(year, 1, 1), start)
        last = min(dt.date(year, 12, 31), today)
        changed = 0
        for i in range((last - first).days + 1):
            d = first + dt.timedelta(days=i)
            key = d.isoformat()
            if d.weekday() >= 5 or (key in store and d < today):
                continue
            try:
                rows = client.bars_on(d)
            except JQuantsError as e:
                log(f"J-Quants 株価 {d}: {e}")
                continue
            if rows or d < today:
                store[key] = {
                    code4(r["Code"]): [r.get("H"), r.get("L"), r.get("C"), r.get("Vo"), r.get("AdjFactor")]
                    for r in rows if code4(r["Code"])
                }
                changed += 1
            if changed and changed % 100 == 0:
                write_json_gz(path, store)
        if changed:
            write_json_gz(path, store)
        for key, day in store.items():
            if key < start.isoformat():
                continue
            for code, v in day.items():
                frames.setdefault(code, []).append((key, *v))
    out = {}
    for code, rows in frames.items():
        df = pd.DataFrame(rows, columns=["d", "H", "L", "C", "Vo", "AdjFactor"]).set_index("d")
        df.index = pd.to_datetime(df.index)
        df = df.apply(pd.to_numeric, errors="coerce").dropna(subset=["C"])
        if not df.empty:
            out[code] = adjust(df)
    return out


def bars_to_panels(daily: dict[str, pd.DataFrame]):
    weekly = {}
    for code, df in daily.items():
        wk = df.index.to_period("W-FRI").to_timestamp(how="end").normalize()
        g = df.groupby(wk)
        weekly[code] = pd.DataFrame({"c": g["Close"].last(), "h": g["High"].max(), "l": g["Low"].min(),
                                     "v": g["Volume"].sum()})
    return panels(weekly), daily_stats(daily)


def update_margin(client: JQuants, cache_dir: Path, today: dt.date, weeks: int = 60) -> dict:
    path = cache_dir / "jq_margin.json.gz"
    store = read_json_gz(path, {}) or {}
    for i in range(weeks * 7):
        d = today - dt.timedelta(days=i)
        key = d.isoformat()
        if d.weekday() >= 5 or key in store:
            continue
        try:
            rows = client.margin_on(d)
        except JQuantsError as e:
            log(f"J-Quants 信用残 {d}: {e}")
            continue
        if rows:
            store[key] = {code4(r["Code"]): [float(r["ShrtVol"] or 0), float(r["LongVol"] or 0)]
                          for r in rows if code4(r["Code"])}
    cutoff = (today - dt.timedelta(days=weeks * 7)).isoformat()
    store = {k: v for k, v in store.items() if k >= cutoff}
    write_json_gz(path, store)
    return store


def update_shorts(client: JQuants, cache_dir: Path, today: dt.date, days: int = 400) -> list[list]:
    path = cache_dir / "jq_shorts.json.gz"
    store = read_json_gz(path, {}) or {}
    for i in range(days):
        d = today - dt.timedelta(days=i)
        key = d.isoformat()
        if d.weekday() >= 5 or (key in store and i > 1):
            continue
        try:
            store[key] = [
                [r["CalcDate"], code4(r["Code"]), r["SSName"], r["ShrtPosToSO"], r.get("PrevRptDate") or None,
                 r.get("PrevRptRatio"), 1 if (r.get("Notes") or "").strip() else 0,
                 f'{r.get("DICName") or ""}|{r.get("FundName") or ""}'.strip("|")]
                for r in client.shorts_on(d) if code4(r["Code"])
            ]
        except JQuantsError as e:
            log(f"J-Quants 空売り {d}: {e}")
    cutoff = (today - dt.timedelta(days=days)).isoformat()
    store = {k: v for k, v in store.items() if k >= cutoff}
    write_json_gz(path, store)
    return [r for k in sorted(store) for r in store[k]]
