"""本番の取得処理(collect)の配線を、通信を模擬して通しで確認する。"""
import datetime as dt
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from uneri import compute, fred, jpx, jquants, main, util, yahoo
from uneri.util import Throttle

CODES = [f"{1000 + i}" for i in range(12)]
MASTER = [{"Code": c + "0", "CoName": "社" + c, "S33Nm": "電気機器", "MktNm": "プライム", "MrgnNm": "貸借",
           "ScaleCat": "TOPIX Core30"} for c in CODES]
MASTER += [{"Code": "99990", "CoName": "ETF", "S33Nm": "その他", "MktNm": "ETF", "MrgnNm": "", "ScaleCat": "-"},
           {"Code": "12345", "CoName": "優先株", "S33Nm": "銀行業", "MktNm": "プライム", "MrgnNm": "", "ScaleCat": "-"}]


def fake_get_all(self, path, params, fins=False):
    if path == "/equities/master":
        return [dict(r) for r in MASTER]
    if path == "/fins/summary":
        if params["date"] != "2026-05-12":
            return []
        return [{"DiscDate": "2026-05-12", "DiscTime": "15:00:00", "Code": c + "0", "DiscNo": "1",
                 "DocType": "FYFinancialStatements_Consolidated_JP", "CurPerType": "FY", "CurFYEn": "2026-03-31",
                 "NxtFYEn": "2027-03-31", "OP": "100000000", "Sales": "900000000", "EqAR": "0.6", "BPS": "500",
                 "ShOutFY": "1000000", "TrShFY": "0", "NxFOP": "120000000", "NxFNp": "80000000", "NxFEPS": "80",
                 "NxFDivAnn": "10"} for c in CODES]
    if path == "/equities/bars/daily":
        d = dt.date.fromisoformat(params["date"])
        return [{"Date": params["date"], "Code": c + "0", "H": 110.0, "L": 90.0, "C": 100.0 + d.toordinal() % 7,
                 "Vo": 1000.0, "AdjFactor": 1.0} for c in CODES]
    if path == "/markets/margin-interest":
        return [{"Code": c + "0", "ShrtVol": 100.0, "LongVol": 500.0} for c in CODES]
    if path == "/markets/short-sale-report":
        return [{"CalcDate": params["disc_date"], "Code": "10000", "SSName": "機関A", "ShrtPosToSO": 0.01,
                 "PrevRptDate": "", "PrevRptRatio": None, "Notes": "", "DICName": "", "FundName": ""}]
    raise AssertionError(path)


def fake_prices(codes, cache, **k):
    idx = pd.date_range("2016-11-11", periods=522, freq="W-FRI")
    rng = np.random.default_rng(1)
    wk = {}
    for c in codes:
        close = 1000 * np.exp(np.cumsum(rng.normal(0, 0.05, len(idx))))
        wk[c] = pd.DataFrame({"c": close, "h": close * 1.02, "l": close * 0.98, "v": 1e5}, index=idx)
    d = {c: {"close": float(wk[c]["c"].iloc[-1]), "date": idx[-1].date().isoformat(), "vo": 1e5, "adv": 1e5, "tv": 100.0}
         for c in codes}
    return yahoo.panels(wk), d, 0


class Collect(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.cache = Path(self.dir.name)
        for target in ("get_all",):
            p = mock.patch.object(jquants.JQuants, target, fake_get_all)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.dir.cleanup)
        p = mock.patch.object(fred, "fetch_all", lambda c: {})
        p.start()
        self.addCleanup(p.stop)

    def test_free_mode(self):
        calls = []
        with mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "k"}), \
                mock.patch.object(yahoo, "fetch_prices", lambda codes, c: (calls.append(codes), fake_prices(codes, c))[1]), \
                mock.patch.object(jpx, "update_margin", lambda j, c, t: {"2026-11-05": {"1000": [100.0, 500.0]}}), \
                mock.patch.object(jpx, "update_shorts", side_effect=RuntimeError("JPX down")):
            inp, status = main.collect(self.cache, "free")
        self.assertEqual(calls[0], sorted(CODES))  # ETF と優先株は入らない
        self.assertIn("失敗", status["空売り"])  # 1つ失敗しても続く
        data = compute.build(inp)
        self.assertEqual(len(data["stocks"]), 12)
        s = data["stocks"][0]
        self.assertEqual(s["eq"], 0.6)
        self.assertEqual(s["mr"], 5.0)
        self.assertEqual(s["dv"], 10)
        self.assertTrue(s["earn"]["fcnext"])
        self.assertEqual(data["source"], "free")
        self.assertFalse(data["features"]["shorts"])
        # J-Quants は「今日−86日」より先を聞かない
        self.assertTrue((self.cache / "fins_by_date.json.gz").exists())
        store = util.read_json_gz(self.cache / "fins_by_date.json.gz")
        self.assertLessEqual(max(store), (util.today_jst() - dt.timedelta(days=86)).isoformat())

    def test_master_failure_uses_previous(self):
        util.write_json_gz(self.cache / "master.json.gz", MASTER)

        def broken(self, path, params, fins=False):
            raise jquants.JQuantsError("down")

        with mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "k"}), mock.patch.object(jquants.JQuants, "get_all", broken), \
                mock.patch.object(yahoo, "fetch_prices", lambda codes, c: fake_prices(codes, c)), \
                mock.patch.object(jpx, "update_margin", lambda j, c, t: {}), \
                mock.patch.object(jpx, "update_shorts", lambda j, c, t: []):
            inp, status = main.collect(self.cache, "free")
        self.assertIn("前回分", status["銘柄一覧"])
        self.assertEqual(len(compute.build(inp)["stocks"]), 12)

    def test_paid_mode(self):
        real_today = dt.date(2026, 11, 9)
        with mock.patch.dict(os.environ, {"JQUANTS_API_KEY": "k"}), mock.patch.object(main, "today_jst", lambda: real_today), \
                mock.patch.object(Throttle, "wait", lambda self: None):
            inp, status = main.collect(self.cache, "jquants")
        self.assertIn("J-Quants", status["株価"])
        self.assertEqual(status["信用残"], "OK")
        self.assertEqual(status["空売り"], "OK")
        data = compute.build(inp)
        self.assertEqual(data["source"], "jquants")
        s = data["stocks"][0]
        self.assertEqual(s["c"], "1000")
        self.assertEqual(s["mr"], 5.0)
        self.assertTrue(s.get("st"))


if __name__ == "__main__":
    unittest.main()
