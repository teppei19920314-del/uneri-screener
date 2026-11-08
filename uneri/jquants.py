"""J-Quants API V2 のクライアント。

無料プランは直近12週のデータが取れないので、問い合わせは「今日−86日」まで。
全体は5回/分、/fins/summary だけ別枠(60回/分)なので50回/分で取得する。
429 が返ったら2分待つ(公式の推奨)。pagination_key は最後までたどる。
"""
from __future__ import annotations

import datetime as dt
import time

import requests

from .util import Throttle, log

BASE = "https://api.jquants.com/v2"
FREE_LAG_DAYS = 86

MASTER_FIELDS = ("Code", "CoName", "S33Nm", "MktNm", "MrgnNm", "ScaleCat")
FIN_FIELDS = (
    "DiscDate", "DiscTime", "Code", "DiscNo", "DocType", "CurPerType", "Sales", "OP", "OdP", "NP",
    "EPS", "BPS", "EqAR", "NCEqAR", "ShEq", "ShOutFY", "TrShFY", "FSales", "FOP", "FOdP", "FNP",
    "FEPS", "NxFSales", "NxFOP", "NxFOdP", "NxFNp", "NxFEPS", "FDivAnn", "NxFDivAnn", "DivAnn",
    "CurFYEn", "CurFYSt", "NxtFYEn", "NxtFYSt",
)


class JQuantsError(RuntimeError):
    pass


class JQuants:
    def __init__(self, api_key: str, plan: str = "free", session=None, sleep=time.sleep):
        if not api_key:
            raise JQuantsError("JQUANTS_API_KEY が設定されていません")
        self.plan = plan
        self.s = session or requests.Session()
        self.s.headers.update({"x-api-key": api_key})
        self._sleep = sleep
        # 無料5回/分 → 13秒間隔。スタンダード120回/分 → 0.6秒間隔
        general = 13.0 if plan == "free" else 0.6
        self._general = Throttle(general, sleep=sleep)
        self._fins = Throttle(60.0 / 50.0, sleep=sleep)  # 別枠60回/分に対して50回/分

    def latest_date(self, today: dt.date) -> dt.date:
        return today - dt.timedelta(days=FREE_LAG_DAYS) if self.plan == "free" else today

    def get_all(self, path: str, params: dict, fins: bool = False) -> list[dict]:
        rows: list[dict] = []
        params = dict(params)
        throttle = self._fins if fins else self._general
        while True:
            body = self._get(path, params, throttle)
            data = body.get("data")
            if data is None:
                raise JQuantsError(f"{path}: 応答に data がありません: {list(body)[:5]}")
            rows.extend(data)
            key = body.get("pagination_key")
            if not key:
                return rows
            params["pagination_key"] = key

    def _get(self, path: str, params: dict, throttle: Throttle) -> dict:
        last_err = None
        for attempt in range(6):
            throttle.wait()
            try:
                r = self.s.get(BASE + path, params=params, timeout=90)
            except requests.RequestException as e:
                last_err = e
                self._sleep(10 * (attempt + 1))
                continue
            if r.status_code == 429:
                log(f"J-Quants 429: 2分待ちます ({path})")
                self._sleep(120)
                continue
            if r.status_code >= 500:
                last_err = f"HTTP {r.status_code}"
                self._sleep(15 * (attempt + 1))
                continue
            if r.status_code != 200:
                raise JQuantsError(f"{path} {params}: HTTP {r.status_code} {r.text[:200]}")
            return r.json()
        raise JQuantsError(f"{path}: 再試行しても取得できませんでした ({last_err})")

    # ---- 個別のAPI ----
    def master(self, date: dt.date) -> list[dict]:
        rows = self.get_all("/equities/master", {"date": date.isoformat()})
        return [{k: r.get(k, "") for k in MASTER_FIELDS} for r in rows]

    def fins_on(self, date: dt.date) -> list[dict]:
        rows = self.get_all("/fins/summary", {"date": date.isoformat()}, fins=True)
        return [{k: r.get(k, "") for k in FIN_FIELDS} for r in rows]

    def bars_on(self, date: dt.date) -> list[dict]:
        return self.get_all("/equities/bars/daily", {"date": date.isoformat()})

    def margin_on(self, date: dt.date) -> list[dict]:
        return self.get_all("/markets/margin-interest", {"date": date.isoformat()})

    def shorts_on(self, date: dt.date) -> list[dict]:
        return self.get_all("/markets/short-sale-report", {"disc_date": date.isoformat()})
