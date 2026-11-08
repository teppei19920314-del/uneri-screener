"""セクターローテーション: マクロ判定(米国)と相場判定(日本株)。"""
from __future__ import annotations

import pandas as pd

from .util import rnd

PERIODS = (4, 13, 26, 52)
PHASES = ("回復期", "好況期", "後退期", "不況期")

GROUPS = {
    "金融": ("回復期", ["銀行業", "証券、商品先物取引業", "保険業", "その他金融業", "不動産業"]),
    "ハイテク": ("回復期", ["電気機器", "精密機器"]),
    "製造業": ("好況期", ["機械", "建設業", "海運業", "空運業", "倉庫・運輸関連業"]),
    "素材": ("好況期", ["化学", "鉄鋼", "非鉄金属", "金属製品", "ガラス・土石製品", "パルプ・紙", "繊維製品", "ゴム製品"]),
    "消費財": ("好況期", ["輸送用機器", "小売業", "サービス業", "その他製品"]),
    "エネルギー": ("後退期", ["鉱業", "石油・石炭製品", "卸売業"]),
    "通信": ("不況期", ["情報・通信業"]),
    "ヘルスケア": ("不況期", ["医薬品"]),
    "生活必需品": ("不況期", ["食料品", "水産・農林業"]),
    "公共": ("不況期", ["電気・ガス業", "陸運業"]),
}
IND_NAMES = {
    "DGS10": "米国10年国債利回り", "FEDFUNDS": "米国の政策金利(FF金利)",
    "CFNAIMA3": "シカゴ連銀 全米活動指数(3か月平均)", "UNRATE": "米国の失業率",
    "IRLTLT01JPM156N": "日本の10年国債利回り",
}


def sector_key(name: str) -> str:
    """業種名の読点などの表記ゆれを吸収する。"""
    return "".join(ch for ch in str(name or "") if ch not in "、､,，・･ 　")


SECTOR_TO_GROUP = {sector_key(s): g for g, (_, secs) in GROUPS.items() for s in secs}


def phase_of(rate: float, econ: float) -> str:
    if econ >= 0:
        return "回復期" if rate < 0 else "好況期"
    return "不況期" if rate < 0 else "後退期"


def _monthly(rows) -> pd.Series:
    s = pd.Series({pd.Timestamp(d): v for d, v in rows}, dtype=float).sort_index()
    return s.groupby(s.index.to_period("M")).last()


def macro_view(fred: dict) -> dict | None:
    """金利スコア = (米10年債 − 過去5年平均) + 0.5×(1年の変化)
    景気スコア = CFNAIMA3 + (1年前の失業率 − 今の失業率)。どちらも0が境目。"""
    try:
        rate = _monthly(fred["DGS10"])
        cfna = _monthly(fred["CFNAIMA3"])
        unem = _monthly(fred["UNRATE"])
    except (KeyError, TypeError, ValueError):
        return None
    if len(rate) < 75 or len(cfna) < 2 or len(unem) < 14:
        return None
    idx = pd.period_range(rate.index[-90], max(rate.index[-1], cfna.index[-1], unem.index[-1]), freq="M")
    rate_f = rate.reindex(pd.period_range(rate.index[0], idx[-1], freq="M")).ffill()
    un = unem.reindex(pd.period_range(unem.index[0], idx[-1], freq="M")).ffill()
    cf = cfna.reindex(pd.period_range(cfna.index[0], idx[-1], freq="M")).ffill()
    r_score = (rate_f - rate_f.rolling(60).mean()) + 0.5 * rate_f.diff(12)
    e_score = cf + (un.shift(12) - un)
    common = r_score.dropna().index.intersection(e_score.dropna().index)
    if len(common) < 13:
        return None
    trail = [[str(m), rnd(r_score[m], 3), rnd(e_score[m], 3)] for m in common[-25:]]
    r, e = r_score[common[-1]], e_score[common[-1]]
    r1, e1 = r_score[common[-13]], e_score[common[-13]]
    ind = []
    for sid, name in IND_NAMES.items():
        rows = fred.get(sid)
        if not rows:
            continue
        m = _monthly(rows)
        # 1年前の月が欠けていれば、その前の最新の値を使う(統計の欠測対策)
        older = m[m.index <= m.index[-1] - 12]
        ind.append({"id": sid, "name": name, "v": rnd(m.iloc[-1], 2),
                    "v1y": rnd(older.iloc[-1], 2) if len(older) else None, "d": str(m.index[-1])})
    return {"phase": phase_of(r, e), "phase_1y": phase_of(r1, e1), "r": rnd(r, 3), "e": rnd(e, 3),
            "d": str(common[-1]), "trail": trail, "ind": ind}


def _agg(members: pd.Index, returns: dict, mc: pd.Series, market: dict | None) -> dict:
    row = {"n": int(len(members)), "mc": rnd(mc.reindex(members).sum(), 0)}
    for p in PERIODS:
        r = returns[p].reindex(members)
        row[f"r{p}"] = rnd(r.median(), 4)
        if market is not None:
            row[f"x{p}"] = rnd(r.median() - market[f"r{p}"], 4) if pd.notna(r.median()) else None
        m = mc.reindex(members)
        delta = (m - m / (1 + r)).dropna()
        row[f"md{p}"] = rnd(delta.sum(), 0)
    return row


def market_view(sectors: pd.Series, returns: dict, mc: pd.Series, close: pd.DataFrame) -> dict:
    codes = sectors.index
    market = _agg(codes, returns, mc, None)
    group_of = sectors.map(lambda s: SECTOR_TO_GROUP.get(sector_key(s)))
    phase_of_group = {g: v[0] for g, v in GROUPS.items()}
    phase = group_of.map(phase_of_group)

    sec_rows = []
    for name, idx in sectors.groupby(sectors).groups.items():
        g = SECTOR_TO_GROUP.get(sector_key(name))
        row = {"name": name, **_agg(pd.Index(idx), returns, mc, market),
               "group": g, "phase": phase_of_group.get(g)}
        sec_rows.append(row)
    grp_rows = []
    for g, (ph, secs) in GROUPS.items():
        idx = group_of[group_of == g].index
        if len(idx):
            grp_rows.append({"name": g, **_agg(idx, returns, mc, market), "phase": ph, "sectors": secs})
    ph_rows = []
    for ph in PHASES:
        idx = phase[phase == ph].index
        if len(idx):
            ph_rows.append({"name": ph, **_agg(idx, returns, mc, market)})

    def best(key):
        cands = [r for r in ph_rows if r.get(key) is not None]
        return max(cands, key=lambda r: r[key])["name"] if cands else None

    # 局面ごとの市場比の1年推移
    tail = close[codes].iloc[-53:]
    cum = tail.div(tail.iloc[0]) - 1
    mk_line = cum.median(axis=1)
    series = {}
    for ph in PHASES:
        idx = phase[phase == ph].index
        if len(idx):
            series[ph] = [rnd(v, 4) or 0 for v in (cum[idx].median(axis=1) - mk_line).tolist()]
    return {"market": market, "phases": ph_rows, "groups": grp_rows, "sectors": sec_rows,
            "lead": best("x13"), "rising": best("x4"),
            "lines": {"d0": tail.index[0].date().isoformat(), "series": series}}
