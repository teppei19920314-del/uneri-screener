"""チャートのうねりの指標: ジグザグ、レンジ位置、傾き、RSI など。入力はすべて numpy 配列。"""
from __future__ import annotations

import math

import numpy as np

ZIG = 0.25
WEEKS_PER_YEAR = 365.25 / 7


def zigzag(c: np.ndarray, threshold: float = ZIG):
    """週足終値で threshold 以上の反転を1波として山(H)と谷(L)を確定する。

    返り値: (確定した山谷 [(添字, 'H'|'L', 価格)], 今の向き 'up'|'down'|None, 未確定の極値の添字)
    """
    n = len(c)
    pivots: list[tuple[int, str, float]] = []
    if n == 0:
        return pivots, None, None
    direction = None
    hi_i = lo_i = 0
    for i in range(1, n):
        v = c[i]
        if direction is None:
            if v > c[hi_i]:
                hi_i = i
            if v < c[lo_i]:
                lo_i = i
            if v >= c[lo_i] * (1 + threshold) and lo_i < i:
                pivots.append((lo_i, "L", float(c[lo_i])))
                direction, hi_i = "up", i
            elif v <= c[hi_i] * (1 - threshold) and hi_i < i:
                pivots.append((hi_i, "H", float(c[hi_i])))
                direction, lo_i = "down", i
        elif direction == "up":
            if v > c[hi_i]:
                hi_i = i
            elif v <= c[hi_i] * (1 - threshold):
                pivots.append((hi_i, "H", float(c[hi_i])))
                direction, lo_i = "down", i
        else:
            if v < c[lo_i]:
                lo_i = i
            elif v >= c[lo_i] * (1 + threshold):
                pivots.append((lo_i, "L", float(c[lo_i])))
                direction, hi_i = "up", i
    ext = hi_i if direction == "up" else lo_i if direction == "down" else None
    return pivots, direction, ext


def wave_amp(pivots, c: np.ndarray, ext) -> float | None:
    """1波の値幅の中央値。上げも下げも「安いほうから高いほうへ何%」で測る。"""
    pts = [p[2] for p in pivots]
    if pivots and ext is not None and ext > pivots[-1][0]:
        pts.append(float(c[ext]))
    legs = [max(a, b) / min(a, b) - 1 for a, b in zip(pts, pts[1:]) if min(a, b) > 0]
    return float(np.median(legs)) if legs else None


def trend(c: np.ndarray) -> float | None:
    """対数株価の回帰の傾き(年率)。"""
    c = c[np.isfinite(c) & (c > 0)]
    if len(c) < 26:
        return None
    x = np.arange(len(c)) / WEEKS_PER_YEAR
    slope = np.polyfit(x, np.log(c), 1)[0]
    return float(math.exp(slope) - 1)


def rsi(c: np.ndarray, period: int = 14) -> float | None:
    """Wilder の RSI。"""
    if len(c) <= period:
        return None
    d = np.diff(c)
    up = np.where(d > 0, d, 0.0)
    dn = np.where(d < 0, -d, 0.0)
    au, ad = up[:period].mean(), dn[:period].mean()
    for u, v in zip(up[period:], dn[period:]):
        au = (au * (period - 1) + u) / period
        ad = (ad * (period - 1) + v) / period
    if ad == 0:
        return 100.0 if au > 0 else 50.0
    return float(100 - 100 / (1 + au / ad))


def nearest_valley(pivots, price: float) -> float | None:
    """最寄りの過去の谷との距離(現在値 ÷ 谷 − 1)。"""
    lows = [p[2] for p in pivots if p[1] == "L" and p[2] > 0]
    if not lows:
        return None
    best = min(lows, key=lambda v: abs(math.log(price / v)))
    return price / best - 1


def span_metrics(c: np.ndarray, h: np.ndarray, l: np.ndarray, price: float | None = None) -> dict:
    """1つの期間の指標。価格は期間の週足。"""
    p = float(c[-1]) if price is None else price
    hi, lo = float(np.nanmax(h)), float(np.nanmin(l))
    hi, lo = max(hi, p), min(lo, p)
    pivots, direction, ext = zigzag(c)
    pos = (p - lo) / (hi - lo) if hi > lo else 0.5
    leg = None
    if direction == "up":
        ref = next((v for v in reversed(pivots) if v[1] == "L"), None)
        leg = p / ref[2] - 1 if ref else None
    elif direction == "down":
        ref = next((v for v in reversed(pivots) if v[1] == "H"), None)
        leg = p / ref[2] - 1 if ref else None
    return {
        "pos": pos, "hi": hi, "lo": lo, "tr": trend(c),
        "sw": sum(1 for v in pivots if v[1] == "L"),
        "amp": wave_amp(pivots, c, ext), "ph": direction, "leg": leg,
        "sup": nearest_valley(pivots, p), "pivots": pivots,
    }
