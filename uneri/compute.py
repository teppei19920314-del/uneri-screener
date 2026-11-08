"""取得したデータから、画面に送る全銘柄のJSONを組み立てる。"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import indicators as ind
from .financials import fin_metrics
from .rotation import macro_view, market_view
from .util import chart_price, code4, log, now_jst, rnd

MARKETS = ("プライム", "スタンダード", "グロース")
SCALE = {"TOPIX Core30": 1, "TOPIX Large70": 2, "TOPIX Mid400": 3}
SPANS = {"y5": (261, 235), "y2": (105, 95), "y1": (53, 48)}  # 週数, 必要な最低週数
TEN_YEARS = 522
TEN_MIN = 470
MIN_WEEKS = 8
RET_WEEKS = (13, 26, 52)
ALL_WEEKS = (4, 13, 26, 52)


@dataclass
class Inputs:
    master: list[dict]
    panels: dict[str, pd.DataFrame]  # c,h,l,v : 週×銘柄
    daily: dict[str, dict]
    fins: list[dict] = field(default_factory=list)
    margin: dict | None = None  # {日付: {コード: [売り残, 買い残]}}
    shorts: list | None = None
    fred: dict | None = None
    source: str = "free"
    demo: bool = False
    today: dt.date | None = None


def market_short(name: str) -> str | None:
    for m in MARKETS:
        if m in str(name or ""):
            return m
    return None


def universe(master: list[dict]) -> dict[str, dict]:
    out = {}
    for r in master:
        code = code4(r.get("Code"))
        m = market_short(r.get("MktNm"))
        if code and m:
            out[code] = {"n": r.get("CoName") or code, "m": m, "s": r.get("S33Nm") or "",
                         "mg": r.get("MrgnNm") or "", "sc": SCALE.get(r.get("ScaleCat"))}
    return out


# ---------------------------------------------------------------- 信用残
def weekly_margin(store: dict) -> tuple[list[str], list[dict]]:
    """週ごとの最終日にそろえる。"""
    last: dict[tuple, str] = {}
    for d in sorted(store):
        last[dt.date.fromisoformat(d).isocalendar()[:2]] = d
    dates = sorted(last.values())
    return dates, [store[d] for d in dates]


def margin_metrics(code: str, dates, tables, adv, shares) -> dict:
    pts = [(d, t[code]) for d, t in zip(dates, tables) if code in t]
    if not pts:
        return {}
    pts = pts[-53:]
    longs = [p[1][1] for p in pts]
    shorts = [p[1][0] for p in pts]
    ml, ms = longs[-1], shorts[-1]
    out = {"mw": len(pts), "ml": rnd(ml, 0), "ms": rnd(ms, 0), "mdt": pts[-1][0],
           "mgs": {"d0": pts[0][0], "l": [int(v) for v in longs], "s": [int(v) for v in shorts]}}
    if ms > 0:
        out["mr"] = rnd(ml / ms, 2)
    if len(pts) >= 3:  # 3週未満は出さない。9週分たまるまでは手元の最古と比較
        back = pts[-9] if len(pts) >= 9 else pts[0]
        if back[1][1] > 0:
            out["mlc"] = rnd(ml / back[1][1] - 1, 4)
        if back[1][0] > 0:
            out["msc"] = rnd(ms / back[1][0] - 1, 4)
    if adv and adv > 0:
        out["md"] = rnd(ml / adv, 1)
    if shares:
        out["mf"] = rnd(ml / shares, 5)
    return out


# ---------------------------------------------------------------- 機関の空売り
def short_index(rows: list | None) -> dict:
    """備考つきの行を除き、{コード: {機関名: {計算日: [割合合計, 直近計算日, 直近割合]}}}"""
    idx: dict = defaultdict(lambda: defaultdict(dict))
    seen = set()
    for r in rows or []:
        date, code, name, ratio, prev_d, prev_r, note, extra = r[:8]
        if note or ratio is None:
            continue
        key = (date, code, name, extra)
        if key in seen:
            continue
        seen.add(key)
        cur = idx[code][name].get(date)
        if cur:
            cur[0] += ratio
        else:
            idx[code][name][date] = [ratio, prev_d, prev_r]
    return idx


def _short_total(inst: dict, on: str, keep: bool = False):
    """基準日時点の、機関ごとの最新報告(0.5%以上・1年以内)の合計。"""
    limit = (dt.date.fromisoformat(on) - dt.timedelta(days=365)).isoformat()
    total, holders = 0.0, []
    for name, reports in inst.items():
        ds = [d for d in reports if d <= on]
        if not ds:
            continue
        d = max(ds)
        if d < limit or reports[d][0] < 0.005:
            continue
        total += reports[d][0]
        if keep:
            holders.append([name, rnd(reports[d][0], 4), d, rnd(reports[d][2], 4)])
    return total, holders


def short_metrics(inst: dict | None, week_dates: list[str]) -> dict:
    if not inst:
        return {}
    series = [_short_total(inst, d)[0] for d in week_dates[-26:]]
    total, holders = _short_total(inst, week_dates[-1], keep=True)
    if not holders and not any(series):
        return {}
    holders.sort(key=lambda h: -h[1])
    out = {"st": rnd(total, 4), "sn": len(holders), "sh": holders[:6], "sts": [rnd(v, 4) for v in series]}
    if len(series) >= 5:
        out["stc"] = rnd(series[-1] - series[-5], 4)
    return out


# ---------------------------------------------------------------- チャート
def month_chart(dates: pd.DatetimeIndex, c: np.ndarray):
    """週足終値から月足(各月の最後の週)を作る。"""
    per = dates.to_period("M")
    s = pd.Series(c, index=per).groupby(level=0).last()
    s = s.reindex(pd.period_range(s.index[0], s.index[-1], freq="M")).ffill()
    return s.tail(121)


def month_pivots(pivots, dates, first: pd.Period):
    out = []
    for i, t, p in pivots:
        idx = (dates[i].to_period("M") - first).n
        if idx >= 0:
            out.append([idx, t, chart_price(p)])
    return out


def span_json(m: dict, piv) -> dict:
    return {"pos": rnd(m["pos"], 4), "hi": rnd(m["hi"], 1), "lo": rnd(m["lo"], 1), "tr": rnd(m["tr"], 4),
            "sw": m["sw"], "amp": rnd(m["amp"], 3), "ph": m["ph"], "leg": rnd(m["leg"], 4),
            "sup": rnd(m["sup"], 4), "piv": piv}


def chart_fields(dates: pd.DatetimeIndex, c, h, l) -> dict | None:
    """1銘柄のうねりの指標とチャート。c,h,l は上場後の週足(欠損なし)。"""
    n = len(c)
    if n < MIN_WEEKS:
        return None
    p = float(c[-1])
    m10 = ind.span_metrics(c[-TEN_YEARS:], h[-TEN_YEARS:], l[-TEN_YEARS:])
    d10 = dates[-TEN_YEARS:]
    ms = month_chart(dates, c)
    first = ms.index[0]
    out = {k: v for k, v in span_json(m10, None).items() if k != "piv"}
    out["p"] = rnd(p, 1)
    if n < TEN_MIN:
        out["x10"] = True  # 10年分のデータがない
    out["ch"] = {"m0": str(first), "c": [chart_price(v) for v in ms.values],
                 "piv": month_pivots(m10["pivots"], d10, first)}
    wk_tail = c[-105:]
    out["wk"] = {"d0": dates[-len(wk_tail)].date().isoformat(), "c": [chart_price(v) for v in wk_tail]}
    # 期間ごと
    for key, (weeks, need) in SPANS.items():
        if n < need:
            continue
        m = ind.span_metrics(c[-weeks:], h[-weeks:], l[-weeks:])
        if key == "y5":
            off = max(0, len(ms) - 61)
            d = dates[-weeks:]
            piv = month_pivots(m["pivots"], d, first + off)
        else:
            piv = [[i, t, chart_price(v)] for i, t, v in m["pivots"]]
        out[key] = span_json(m, piv)
    # 週足の反転サイン
    out["rsi"] = rnd(ind.rsi(c), 1)
    out["ma"] = bool(p > float(np.mean(c[-13:])))
    low52 = l[-52:]
    i = int(np.nanargmin(low52))
    lo = min(float(low52[i]), p)
    out["rb"] = rnd(p / lo - 1, 4)
    out["wl"] = len(low52) - 1 - i
    out["dh"] = rnd(p / max(float(np.nanmax(h[-53:])), p) - 1, 4)
    return out


# ---------------------------------------------------------------- 全体
def build(inp: Inputs) -> dict:
    today = inp.today or now_jst().date()
    uni = universe(inp.master)
    close = inp.panels["c"]
    cols = [c for c in close.columns if c in uni]
    close = close[cols]
    high, low, volume = (inp.panels[k][cols] for k in "hlv")
    log(f"対象 {len(uni)}銘柄、株価あり {len(cols)}銘柄")

    # 騰落率
    returns = {}
    for w in ALL_WEEKS:
        returns[w] = (close.iloc[-1] / close.iloc[-1 - w] - 1) if len(close) > w else pd.Series(dtype=float)
    sectors = pd.Series({c: uni[c]["s"] for c in cols})
    sec_med = {w: returns[w].groupby(sectors).median() for w in RET_WEEKS}
    mkt_med = {w: returns[w].median() for w in RET_WEEKS}
    sec_n = sectors.groupby(sectors).size()
    # 業種との連動度
    wret = close.iloc[-53:].pct_change(fill_method=None).iloc[1:]
    sec_wret = wret.T.groupby(sectors).median().T

    fins_by = defaultdict(list)
    for r in inp.fins or []:
        c = code4(r.get("Code"))
        if c:
            fins_by[c].append(r)
    m_dates, m_tables = weekly_margin(inp.margin) if inp.margin else ([], [])
    s_idx = short_index(inp.shorts)
    asof = max((d["date"] for d in inp.daily.values()), default=close.index[-1].date().isoformat())
    wk_dates = [min(x.date().isoformat(), asof) for x in close.index[-30:]]

    stocks, mc_map = [], {}
    index = close.index
    for code in cols:
        c_all = close[code].to_numpy(dtype=float)
        ok = np.isfinite(c_all)
        if not ok.any():
            continue
        start = int(np.argmax(ok))
        c = c_all[start:]
        h = high[code].to_numpy(dtype=float)[start:]
        l = low[code].to_numpy(dtype=float)[start:]
        ch = chart_fields(index[start:], c, h, l)
        if ch is None:
            continue
        u = uni[code]
        s = {"c": code, "n": u["n"], "m": u["m"], "s": u["s"], "mg": u["mg"]}
        if u["sc"]:
            s["sc"] = u["sc"]
        s.update(ch)
        d = inp.daily.get(code) or {}
        if d.get("tv") is not None:
            s["tv"] = rnd(d["tv"], 1)
        if d.get("vo") is not None:
            s["vo"] = int(d["vo"])
            s["vd"] = d["date"]
        price = float(c[-1])
        fin = fin_metrics(fins_by.get(code, []), price)
        shares = fin.pop("shares", None)
        dist = fin.pop("dist", {})
        s.update({k: v for k, v in fin.items() if v is not None})
        # 市場の歪み
        rel = {}
        for w in RET_WEEKS:
            st = returns[w].get(code)
            rel[str(w)] = [rnd(st, 4), rnd(sec_med[w].get(u["s"]), 4), rnd(mkt_med[w], 4)]
        dist["rel"] = rel
        x, y = wret[code].to_numpy(dtype=float), sec_wret[u["s"]].to_numpy(dtype=float)
        mask = np.isfinite(x) & np.isfinite(y)
        if mask.sum() >= 26 and x[mask].std() > 0 and y[mask].std() > 0:
            dist["r2"] = rnd(np.corrcoef(x[mask], y[mask])[0, 1] ** 2, 3)
        dist["secn"] = int(sec_n.get(u["s"], 0))
        s["dist"] = dist
        if m_tables:
            s.update(margin_metrics(code, m_dates, m_tables, d.get("adv"), shares))
        if inp.shorts is not None:
            s.update(short_metrics(s_idx.get(code), wk_dates))
        if s.get("mc") is not None:
            mc_map[code] = s["mc"]
        stocks.append(s)

    valid = [s["c"] for s in stocks]
    mc = pd.Series(mc_map, dtype=float)
    rot = {"market": market_view(sectors.reindex(valid), returns, mc, close)}
    macro = macro_view(inp.fred) if inp.fred else None
    if macro:
        rot["macro"] = macro
    stocks.sort(key=lambda s: s["c"])
    return {
        "asof": asof, "generated": now_jst().strftime("%Y-%m-%d %H:%M"), "universe": len(stocks),
        "features": {"margin": bool(m_tables), "shorts": inp.shorts is not None},
        "rot": rot, "demo": inp.demo, "source": inp.source, "stocks": stocks,
    }
