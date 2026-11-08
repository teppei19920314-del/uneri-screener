"""APIキーなしで動かすための模擬データ。銘柄・業績・機関名・マクロの値はすべて架空。

業種ごとの共通の値動き、株式分割(AdjFactor)、信用残、空売り、四半期決算(累計)、
TOPIX区分を含む。本番と同じ形の入力を作って compute.build に渡す。
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from .compute import Inputs
from .jq_prices import adjust
from .rotation import GROUPS
from .yahoo import panels

PREFIX = ["朱雀", "玄武", "白虎", "青龍", "麒麟", "鳳凰", "暁", "曙", "黎明", "蒼天", "碧海", "紅葉", "若葉", "東雲", "夕凪", "銀河"]
SUFFIX = ["工業", "製作所", "ホールディングス", "産業", "商事", "電機", "化成", "フーズ", "システムズ", "精機", "技研", "物産"]
FIRMS = ["デモ証券インターナショナル", "テスト・アセット・マネジメント", "架空インベストメンツ", "サンプル・キャピタル", "模擬ファンド・パートナーズ"]


def _s(x) -> str:
    return "" if x is None else str(x)


def _money(v) -> str:
    return "" if v is None else str(int(round(v)))


def make(n: int = 260, seed: int = 7, today: dt.date | None = None) -> Inputs:
    rng = np.random.default_rng(seed)
    today = today or dt.date.today()
    weeks = 522
    idx = pd.date_range(end=pd.Timestamp(today).to_period("W-FRI").to_timestamp(how="end").normalize(),
                        periods=weeks, freq="W-FRI")
    sectors = [s for _, (_, ss) in GROUPS.items() for s in ss]
    # 市場・業種の共通の動き(約3〜4年周期のうねり)
    t = np.arange(weeks)
    mkt = 0.0015 + rng.normal(0, 0.012, weeks) + 0.006 * np.sin(t / 26)
    sec_ret = {s: mkt + rng.normal(0, 0.012, weeks) + 0.008 * np.sin(t / (22 + i % 9) + i)
               for i, s in enumerate(sectors)}
    master, weekly, daily, fins, shorts = [], {}, {}, [], []
    margin_hist: dict[str, dict] = {}
    m_dates = [min(d.date(), today) for d in idx[-60:]]
    for i in range(n):
        code = f"{chr(68 + i // 1000)}{i % 1000:03d}"
        sec = sectors[i % len(sectors)]
        scale = "TOPIX Core30" if i % 37 == 0 else "TOPIX Large70" if i % 19 == 0 else "TOPIX Mid400" if i % 5 == 0 else "TOPIX Small 1"
        market = ["プライム", "プライム", "スタンダード", "グロース"][i % 4]
        master.append({"Code": code + "0", "CoName": f"{PREFIX[i % len(PREFIX)]}{SUFFIX[(i * 5) % len(SUFFIX)]}{i}",
                       "S33Nm": sec, "MktNm": market, "MrgnNm": "貸借" if i % 3 else "信用", "ScaleCat": scale})
        beta = rng.uniform(0.7, 1.4)
        own = rng.normal(0, rng.uniform(0.025, 0.05), weeks) + rng.uniform(0.02, 0.05) * np.sin(t / rng.uniform(14, 40) + rng.uniform(0, 6)) * 0.3
        drift = rng.uniform(-0.0012, 0.0012)
        rets = beta * sec_ret[sec] + own + drift
        if i % 11 == 0:  # 長く低迷した銘柄
            rets = rets - 0.0015
        rets = np.clip(rets, -0.45, 0.6)
        px = rng.uniform(150, 3000) * np.exp(np.cumsum(rets))
        px = px / px[-1] * rng.uniform(120, 6000)
        start = 0
        if i % 17 == 3:  # 上場が新しい銘柄
            start = int(rng.integers(120, 470))
        close = pd.Series(px[start:], index=idx[start:])
        hi = close * (1 + np.abs(rng.normal(0.02, 0.012, len(close))))
        lo = close * (1 - np.abs(rng.normal(0.02, 0.012, len(close))))
        vol = pd.Series(rng.lognormal(11.5, 0.7, len(close)) * 5, index=close.index)
        raw = pd.DataFrame({"C": close, "H": hi, "L": lo, "Vo": vol, "AdjFactor": 1.0})
        if i % 13 == 5 and len(raw) > 60:  # 株式分割: 分割前の生の価格を作り、AdjFactorで調整する
            pos = int(rng.integers(20, len(raw) - 10))
            ratio = int(rng.choice([2, 3, 5]))
            raw.iloc[:pos, raw.columns.get_indexer(["C", "H", "L"])] *= ratio
            raw.iloc[:pos, raw.columns.get_indexer(["Vo"])] /= ratio
            raw.iloc[pos, raw.columns.get_loc("AdjFactor")] = 1 / ratio
        adj = adjust(raw)
        weekly[code] = pd.DataFrame({"c": adj["Close"], "h": adj["High"], "l": adj["Low"], "v": adj["Volume"]})
        price = float(adj["Close"].iloc[-1])
        vo = float(adj["Volume"].iloc[-4:].mean() / 5)
        daily[code] = {"close": price, "date": idx[-1].date().isoformat() if idx[-1].date() <= today else today.isoformat(),
                       "vo": vo, "adv": vo, "tv": price * vo / 1e6}
        fins.extend(_fins(rng, code, price, today, i))
        # 信用残(週次)
        if i % 9 != 8:
            ml = vo * rng.uniform(1, 25)
            ms = 0.0 if i % 23 == 0 else ml / rng.uniform(0.6, 12)
            for d in m_dates:
                margin_hist.setdefault(d.isoformat(), {})[code] = [float(int(ms / 100) * 100), float(int(ml / 100) * 100)]
                ml *= rng.uniform(0.94, 1.06)
                if ms:
                    ms *= rng.uniform(0.92, 1.08)
        # 機関の空売り
        if i % 4 == 1:
            for firm in rng.choice(FIRMS, size=int(rng.integers(1, 4)), replace=False):
                d0 = today - dt.timedelta(days=int(rng.integers(170, 300)))
                ratio = rng.uniform(0.005, 0.015)
                prev_d, prev_r = None, None
                d = d0
                while d < today:
                    shorts.append([d.isoformat(), code, str(firm), round(ratio, 4), prev_d, prev_r, 0, ""])
                    prev_d, prev_r = d.isoformat(), round(ratio, 4)
                    ratio = max(0.004, ratio + rng.normal(0.0004, 0.0012))
                    d += dt.timedelta(days=int(rng.integers(10, 40)))
    return Inputs(master=master, panels=panels(weekly), daily=daily, fins=fins, margin=margin_hist, shorts=shorts,
                  fred=_fred(rng, today), source="free", demo=True, today=today)


def _fins(rng, code, price, today, i) -> list[dict]:
    """3月決算の会社の開示を作る(無料プランと同じく直近86日は見えない)。"""
    limit = today - dt.timedelta(days=86)
    start = limit - dt.timedelta(days=470)
    shares = float(int(rng.uniform(5e6, 3e8)))
    treasury = float(int(shares * rng.uniform(0, 0.05)))
    sales = price * shares * rng.uniform(0.4, 2.0)
    margin = rng.uniform(-0.01, 0.18) if i % 15 != 0 else rng.uniform(-0.1, -0.01)
    growth = rng.uniform(-0.25, 0.3)
    eqar = rng.uniform(0.15, 0.85)
    bps = price / rng.uniform(0.4, 2.4)
    div = 0.0 if i % 10 == 0 else round(price * rng.uniform(0.005, 0.05), 1)
    rows = []
    for year in range(start.year - 1, today.year + 2):
        fy_end = dt.date(year, 3, 31)
        base = sales * (1 + growth) ** (year - today.year)
        prev_base = base / (1 + growth)
        next_base = base * (1 + growth)
        op_y = base * margin
        calendar = [("1Q", dt.date(year - 1, 8, 8), 0.24), ("2Q", dt.date(year - 1, 11, 8), 0.49),
                    ("3Q", dt.date(year, 2, 8), 0.74), ("FY", dt.date(year, 5, 12), 1.0)]
        for pt, disc, frac in calendar:
            noise = rng.uniform(0.92, 1.08)
            row = {
                "DiscDate": disc.isoformat(), "DiscTime": "15:30:00", "Code": code + "0",
                "DiscNo": disc.strftime("%Y%m%d") + str(i).zfill(6),
                "DocType": f"{pt}FinancialStatements_Consolidated_JP", "CurPerType": pt,
                "CurFYEn": fy_end.isoformat(), "CurFYSt": dt.date(year - 1, 4, 1).isoformat(),
                "NxtFYEn": dt.date(year + 1, 3, 31).isoformat() if pt == "FY" else "",
                "NxtFYSt": fy_end.replace(month=4, day=1).isoformat() if pt == "FY" else "",
                "Sales": _money(base * frac * noise), "OP": _money(op_y * frac * noise),
                "OdP": _money(op_y * frac * noise * 1.05), "NP": _money(op_y * frac * noise * 0.68),
                "EPS": f"{op_y * frac * noise * 0.68 / shares:.2f}",
                "BPS": f"{bps:.2f}" if pt == "FY" else "", "EqAR": f"{eqar:.3f}" if i % 29 else "",
                "NCEqAR": "" if i % 29 else f"{eqar:.3f}", "ShEq": _money(bps * (shares - treasury)),
                "ShOutFY": _money(shares), "TrShFY": _money(treasury), "DivAnn": _s(div) if pt == "FY" else "",
            }
            if pt == "FY":
                nx_op = next_base * margin
                row.update({"NxFSales": _money(next_base), "NxFOP": _money(nx_op), "NxFOdP": _money(nx_op * 1.05),
                            "NxFNp": _money(nx_op * 0.68), "NxFEPS": f"{nx_op * 0.68 / shares:.2f}",
                            "NxFDivAnn": _s(div), "FSales": "", "FOP": "", "FOdP": "", "FNP": "", "FEPS": "", "FDivAnn": ""})
            else:
                row.update({"FSales": _money(base), "FOP": _money(op_y), "FOdP": _money(op_y * 1.05),
                            "FNP": _money(op_y * 0.68), "FEPS": f"{op_y * 0.68 / shares:.2f}", "FDivAnn": _s(div),
                            "NxFSales": "", "NxFOP": "", "NxFOdP": "", "NxFNp": "", "NxFEPS": "", "NxFDivAnn": ""})
            rows.append(row)
        if i % 6 == 2:  # 業績予想の修正
            d = dt.date(year - 1, 9, 20)
            factor = rng.uniform(0.75, 1.2)
            rows.append({"DiscDate": d.isoformat(), "DiscTime": "16:00:00", "Code": code + "0",
                         "DiscNo": d.strftime("%Y%m%d") + str(i).zfill(6), "DocType": "EarnForecastRevision",
                         "CurPerType": "FY", "CurFYEn": fy_end.isoformat(), "FSales": _money(base * factor),
                         "FOP": _money(op_y * factor), "FOdP": _money(op_y * factor * 1.05),
                         "FNP": _money(op_y * factor * 0.68), "FEPS": f"{op_y * factor * 0.68 / shares:.2f}"})
        if i % 12 == 3:  # 配当予想の修正
            d = dt.date(year - 1, 12, 15)
            rows.append({"DiscDate": d.isoformat(), "DiscTime": "16:00:00", "Code": code + "0",
                         "DiscNo": d.strftime("%Y%m%d") + str(i).zfill(6), "DocType": "DividendForecastRevision",
                         "CurPerType": "FY", "CurFYEn": fy_end.isoformat(), "FDivAnn": _s(round(div * 1.1, 1))})
    return [r for r in rows if start.isoformat() <= r["DiscDate"] <= limit.isoformat()]


def _fred(rng, today) -> dict:
    months = pd.period_range(end=pd.Timestamp(today).to_period("M") - 1, periods=120, freq="M")
    t = np.arange(len(months))
    rate = 2.4 + 1.1 * np.sin(t / 15) + rng.normal(0, 0.08, len(t))
    cfna = 0.45 * np.sin(t / 13 + 1) + rng.normal(0, 0.08, len(t))
    un = 4.6 - 0.7 * np.sin(t / 15 + 1.4) + rng.normal(0, 0.05, len(t))
    ff = 1.8 + 1.2 * np.sin(t / 15 - 0.4)
    jp = 0.8 + 0.3 * np.sin(t / 18)
    def rows(a):
        return [[m.to_timestamp().date().isoformat(), round(float(v), 2)] for m, v in zip(months, a)]
    return {"DGS10": rows(rate), "FEDFUNDS": rows(ff), "CFNAIMA3": rows(cfna), "UNRATE": rows(un), "IRLTLT01JPM156N": rows(jp)}
