"""決算短信の行から、財務・予想・直近の決算・市場の歪み用の業績チェックを計算する。"""
from __future__ import annotations

import datetime as dt

from .util import num, rnd

OKU = 1e8


def _d(s) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def is_statement(r: dict) -> bool:
    return "FinancialStatements" in (r.get("DocType") or "")


def doc_label(r: dict) -> str:
    doc = r.get("DocType") or ""
    if "FinancialStatements" in doc:
        pt = r.get("CurPerType") or ""
        return f"決算短信({pt})" if pt else "決算短信"
    if "EarnForecastRevision" in doc:
        return "業績予想の修正"
    if "DividendForecastRevision" in doc:
        return "配当予想の修正"
    return doc or "開示"


def change(now, prev):
    """増減率。前年が赤字なら赤字幅に対する増減。"""
    if now is None or prev is None or prev == 0:
        return None
    return (now - prev) / abs(prev)


def _sort(rows):
    return sorted(rows, key=lambda r: (r.get("DiscDate") or "", r.get("DiscTime") or "", r.get("DiscNo") or ""))


def _prior_same_period(rows, cur):
    """1年前の同じ四半期の決算。訂正があれば新しいほうを使う。"""
    fy = _d(cur.get("CurFYEn"))
    disc = _d(cur.get("DiscDate"))
    found = None
    for r in rows:
        if r is cur or r.get("CurPerType") != cur.get("CurPerType"):
            continue
        rfy, rdisc = _d(r.get("CurFYEn")), _d(r.get("DiscDate"))
        if fy and rfy:
            ok = 330 <= (fy - rfy).days <= 400
        else:
            ok = bool(disc and rdisc) and 300 <= (disc - rdisc).days <= 430
        if ok:
            found = r
    return found


FC_FIELDS = {"sales": ("FSales", "NxFSales"), "op": ("FOP", "NxFOP"), "odp": ("FOdP", "NxFOdP"),
             "np": ("FNP", "NxFNp"), "eps": ("FEPS", "NxFEPS"), "div": ("FDivAnn", "NxFDivAnn")}


def forecast_state(rows):
    """開示を時系列にたどって、いま有効な通期予想を求める。

    通期決算のあとは翌期予想、四半期のあとは当期予想。業績予想・配当予想の修正は、
    対象の事業年度が同じなら上書きする。
    """
    fc: dict = {}
    fcnext = False
    target = None
    for r in rows:
        if is_statement(r):
            is_fy = r.get("CurPerType") == "FY"
            idx = 1 if is_fy else 0
            fc = {k: num(r.get(f[idx])) for k, f in FC_FIELDS.items()}
            fcnext = is_fy
            target = (r.get("NxtFYEn") if is_fy else r.get("CurFYEn")) or None
        elif "ForecastRevision" in (r.get("DocType") or ""):
            rfy = r.get("CurFYEn") or None
            if target and rfy and rfy < target:
                continue  # 前の事業年度の修正
            if target and rfy and rfy > target:
                continue
            for k, f in FC_FIELDS.items():
                v = num(r.get(f[0]))
                if v is not None:
                    fc[k] = v
    return fc, fcnext, target


def _last_value(rows, *keys):
    for r in reversed(rows):
        for k in keys:
            v = num(r.get(k))
            if v is not None:
                return v
    return None


def fin_metrics(rows: list[dict], price: float | None) -> dict:
    """1銘柄の財務指標。rows は15か月分の開示。"""
    out: dict = {}
    rows = _sort(rows)
    st = [r for r in rows if is_statement(r)]
    if not rows:
        return out
    fc, fcnext, target = forecast_state(rows)

    shout, trsh = _last_value(st, "ShOutFY"), _last_value(st, "TrShFY")
    shares = None
    if shout is not None:
        shares = shout - (trsh or 0)
        if shares <= 0:
            shares = None
    out["shares"] = shares

    last = st[-1] if st else None
    eq = _last_value(st, "EqAR", "NCEqAR")
    out["eq"] = rnd(eq, 3)

    if price and price > 0:
        bps = num(last.get("BPS")) if last else None
        if bps is None and last is not None and shares:
            sheq = num(last.get("ShEq"))
            bps = sheq / shares if sheq is not None else None
        if bps is None:
            bps = _last_value(st, "BPS")
        if bps and bps > 0:
            out["pbr"] = rnd(price / bps, 2)
        eps = fc.get("eps")
        if eps and eps > 0:
            out["per"] = rnd(price / eps, 1)
        if fc.get("div") is not None:
            out["dy"] = rnd(fc["div"] / price, 4)
        if shares:
            out["mc"] = rnd(price * shares / OKU, 0)
    if fc.get("div") is not None:
        out["dv"] = rnd(fc["div"], 2)
    if fc.get("op") is not None:
        out["opf"] = rnd(fc["op"] / OKU, 1)
    fnp, fop = fc.get("np"), fc.get("op")
    if (fnp is not None and fnp < 0) or (fnp is None and fop is not None and fop < 0):
        out["loss"] = True

    if not last:
        return out
    out["fdt"] = last.get("DiscDate")

    # 直近の決算
    prior = _prior_same_period(st, last)
    pt = last.get("CurPerType") or ""
    is_fy = pt == "FY"
    tbl = [("売上高", "Sales", "sales", OKU), ("営業利益", "OP", "op", OKU), ("経常利益", "OdP", "odp", OKU),
           ("純利益", "NP", "np", OKU), ("EPS(円)", "EPS", "eps", 1.0)]
    e_rows = []
    for label, key, fk, unit in tbl:
        v = num(last.get(key))
        p = num(prior.get(key)) if prior else None
        y = change(v, p)
        f = fc.get(fk)
        prog = v / f if (not is_fy and v is not None and f and f > 0) else None
        e_rows.append([label, rnd(v / unit, 2) if v is not None else None, rnd(y, 4),
                       rnd(f / unit, 2) if f is not None else None, rnd(prog, 3)])
    hist = []
    seen = set()
    for r in reversed(rows):
        item = (r.get("DiscDate"), doc_label(r))
        if item in seen:
            continue
        seen.add(item)
        hist.append(list(item))
        if len(hist) == 4:
            break
    out["earn"] = {"hist": hist, "dt": last.get("DiscDate"), "pt": pt, "fy": is_fy, "doc": doc_label(last),
                   "rows": e_rows, "div": rnd(fc.get("div"), 2), "fcnext": fcnext}

    # 市場の歪みの「業績は崩れていないか」用
    op, sales = num(last.get("OP")), num(last.get("Sales"))
    p_op = num(prior.get("OP")) if prior else None
    p_sales = num(prior.get("Sales")) if prior else None
    dist = {"bpt": pt, "bdt": last.get("DiscDate"), "sy": rnd(change(sales, p_sales), 4),
            "oy": rnd(change(op, p_op), 4), "oyl": bool(op is not None and op < 0)}

    # 今期予想 ÷ 前期実績
    fy_rows = [r for r in st if r.get("CurPerType") == "FY"]
    prev_fy = None
    if is_fy:
        prev_fy = last
    else:
        cur_end = last.get("CurFYEn") or ""
        older = [r for r in fy_rows if (r.get("CurFYEn") or "") < cur_end]
        prev_fy = older[-1] if older else None
    if prev_fy is not None:
        base = num(prev_fy.get("OP"))
        if fop is not None and base and base > 0:
            dist["fcp"] = rnd(fop / base - 1, 4)
        # 期初予想からの修正率: 通期決算で出た翌期予想 → 直近の当期予想
        init = num(prev_fy.get("NxFOP"))
        if fop is not None and init and init > 0:
            dist["fcr"] = rnd(fop / init - 1, 4)
    out["dist"] = dist
    return out
