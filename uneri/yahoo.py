"""Yahoo!ファイナンス(yfinance)。全銘柄の10年分の週足を毎回取り直す。

auto_adjust=False で分割調整済みの終値・高値・安値・出来高を使う(配当調整はしない)。
80銘柄ずつ取得し、レート制限で全滅したら待って再試行、取れなかった銘柄は10銘柄ずつ
取り直し、それでもだめなら前回の値を使う。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .util import log, read_json_gz, write_json_gz

BATCH = 80
SMALL = 10


def ticker(code: str) -> str:
    return f"{code}.T"


def split_download(df: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """yf.download の結果を銘柄ごとの表(Close, High, Low, Volume)に分ける。"""
    out: dict[str, pd.DataFrame] = {}
    if df is None or df.empty:
        return out
    multi = isinstance(df.columns, pd.MultiIndex)
    for t in tickers:
        try:
            if multi:
                lv0 = df.columns.get_level_values(0)
                sub = df[t] if t in lv0 else df.xs(t, axis=1, level=1)
            elif len(tickers) == 1:
                sub = df
            else:
                continue
            sub = sub[["Close", "High", "Low", "Volume"]].dropna(subset=["Close"])
        except KeyError:
            continue
        if not sub.empty:
            sub = sub.copy()
            sub.index = pd.to_datetime(sub.index).tz_localize(None).normalize()
            out[t] = sub
    return out


def _download(tickers, period, interval, downloader):
    return downloader(
        tickers, period=period, interval=interval, auto_adjust=False, group_by="ticker",
        progress=False, threads=True, actions=False, timeout=60,
    )


def fetch_batched(tickers: list[str], period: str, interval: str, downloader=None, sleep=time.sleep,
                  batch: int = BATCH, small: int = SMALL) -> dict[str, pd.DataFrame]:
    if downloader is None:
        import yfinance as yf

        downloader = yf.download
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    got: dict[str, pd.DataFrame] = {}

    def run(chunk, tries):
        for attempt in range(tries):
            try:
                res = split_download(_download(chunk, period, interval, downloader), chunk)
            except Exception as e:  # レート制限などは種類を問わず待って再試行
                log(f"Yahoo: {type(e).__name__}: {str(e)[:120]}")
                res = {}
            if res:
                got.update(res)
                return
            if attempt < tries - 1:
                wait = 60 * (attempt + 1)
                log(f"Yahoo: {len(chunk)}銘柄すべて取得できず、{wait}秒待ちます")
                sleep(wait)

    for i in range(0, len(tickers), batch):
        run(tickers[i:i + batch], 4)
        sleep(1.0)
        if (i // batch) % 10 == 0:
            log(f"Yahoo {interval}: {min(i + batch, len(tickers))}/{len(tickers)} 取得 {len(got)}")
    missing = [t for t in tickers if t not in got]
    if missing:
        log(f"Yahoo {interval}: {len(missing)}銘柄を小分けで再取得します")
        for i in range(0, len(missing), small):
            run(missing[i:i + small], 2)
            sleep(1.0)
    return got


def to_weekly(rows: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """週足の表を、金曜日付の週にそろえる(同じ週の重複はまとめる)。

    Yahoo の週足は月曜日付で、取得日によって最終行が別の曜日になることがある。
    """
    out = {}
    for t, df in rows.items():
        wk = df.index.to_period("W-FRI").to_timestamp(how="end").normalize()
        g = df.groupby(wk)
        out[t] = pd.DataFrame({"c": g["Close"].last(), "h": g["High"].max(), "l": g["Low"].min(),
                               "v": g["Volume"].sum()})
    return out


def panels(weekly: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """{'c','h','l','v'} ごとに 週×銘柄 の表にする。週は欠けなくそろえる。"""
    if not weekly:
        raise ValueError("株価が1銘柄も取れていません")
    start = min(df.index.min() for df in weekly.values())
    end = max(df.index.max() for df in weekly.values())
    idx = pd.date_range(start, end, freq="W-FRI")
    res = {}
    for k in "chlv":
        res[k] = pd.DataFrame({c: df[k] for c, df in weekly.items()}).reindex(idx)
    # 取引のなかった週は前の終値で埋める(高値・安値も終値で埋める)
    res["c"] = res["c"].ffill()
    res["h"] = res["h"].where(res["h"].notna(), res["c"])
    res["l"] = res["l"].where(res["l"].notna(), res["c"])
    res["v"] = res["v"].fillna(0)
    return res


def daily_stats(rows: dict[str, pd.DataFrame]) -> dict[str, dict]:
    out = {}
    for t, df in rows.items():
        code = t[:-2] if t.endswith(".T") else t
        df = df.dropna(subset=["Close"])
        if df.empty:
            continue
        tail = df.tail(20)
        vol = tail["Volume"].fillna(0)
        last = df.iloc[-1]
        out[code] = {
            "close": float(last["Close"]),
            "date": df.index[-1].date().isoformat(),
            "vo": float(last["Volume"]) if pd.notna(last["Volume"]) else None,
            "adv": float(vol.mean()),
            "tv": float((tail["Close"] * vol).mean() / 1e6),  # 百万円
        }
    return out


# ---- 前回分のキャッシュ ----
def _encode(df: pd.DataFrame) -> dict:
    def col(s, d):
        return [None if pd.isna(x) else round(float(x), d) for x in s.values]

    return {"d": [x.date().isoformat() for x in df.index], "c": col(df["c"], 2), "h": col(df["h"], 2),
            "l": col(df["l"], 2), "v": col(df["v"], 0)}


def _decode(o: dict) -> pd.DataFrame:
    return pd.DataFrame({k: np.array(o[k], dtype=float) for k in "chlv"}, index=pd.to_datetime(o["d"]))


def fetch_prices(codes: list[str], cache_dir: Path, downloader=None, sleep=time.sleep):
    """(週足パネル, 日足の集計, 前回分を使った銘柄数) を返す。"""
    tick = {ticker(c): c for c in codes}
    wk_raw = fetch_batched(list(tick), "10y", "1wk", downloader, sleep)
    weekly = {tick[t]: df for t, df in to_weekly(wk_raw).items()}
    path = cache_dir / "prices_weekly.json.gz"
    old = read_json_gz(path, {}) or {}
    reused = 0
    for c in codes:
        if c not in weekly and c in old:
            weekly[c] = _decode(old[c])
            reused += 1
    if reused:
        log(f"Yahoo: 取れなかった {reused}銘柄は前回分を使います")
    dly_raw = fetch_batched(list(tick), "1mo", "1d", downloader, sleep)
    daily = daily_stats(dly_raw)
    dpath = cache_dir / "prices_daily.json.gz"
    old_daily = read_json_gz(dpath, {}) or {}
    for c in codes:
        if c not in daily and c in old_daily:
            daily[c] = old_daily[c]
    # 週足の最終行が日足より古いことがあるので、日足の終値で最新週を更新する
    for c, df in weekly.items():
        d = daily.get(c)
        if d:
            wk_end = pd.Timestamp(d["date"]).to_period("W-FRI").to_timestamp(how="end").normalize()
            if wk_end not in df.index and wk_end > df.index.max():
                df.loc[wk_end, ["c", "h", "l", "v"]] = [d["close"], d["close"], d["close"], d["vo"] or 0]
            elif wk_end in df.index:
                df.loc[wk_end, "c"] = d["close"]
    if weekly:
        write_json_gz(path, {c: _encode(df) for c, df in weekly.items()})
        write_json_gz(dpath, daily)
    return panels(weekly), daily, reused
