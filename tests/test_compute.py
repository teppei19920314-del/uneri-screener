"""指標・財務・ローテーション・暗号化・J-Quantsクライアントのテスト。"""
import base64
import datetime as dt
import gzip
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from uneri import compute, demo, financials as fin, indicators as ind, jq_prices, jquants, packer, rotation, util

ROOT = Path(__file__).resolve().parent.parent


class Zigzag(unittest.TestCase):
    def test_pivots(self):
        c = np.array([100, 110, 130, 95, 90, 120, 118, 150, 100, 105], float)
        piv, d, ext = ind.zigzag(c)
        self.assertEqual([(p[0], p[1]) for p in piv], [(0, "L"), (2, "H"), (4, "L"), (7, "H")])
        self.assertEqual(d, "down")
        self.assertEqual(ext, 8)

    def test_small_moves_ignored(self):
        piv, d, _ = ind.zigzag(np.array([100, 110, 100, 115, 105], float))
        self.assertEqual(piv, [])
        self.assertIsNone(d)

    def test_exact_threshold(self):
        piv, d, _ = ind.zigzag(np.array([100, 125], float))
        self.assertEqual(piv[0][:2], (0, "L"))
        self.assertEqual(d, "up")

    def test_span_metrics(self):
        c = np.array([200, 100, 150, 120, 80, 120], float)
        m = ind.span_metrics(c, c * 1.0, c * 1.0)
        self.assertEqual(m["sw"], 2)
        self.assertAlmostEqual(m["pos"], (120 - 80) / 120)
        self.assertEqual(m["ph"], "up")
        self.assertAlmostEqual(m["leg"], 0.5)
        # 波: 200→100(+100%), 100→150(+50%), 150→80(+87.5%), 80→120(+50%)
        self.assertAlmostEqual(m["amp"], np.median([1.0, .5, .875, .5]))
        self.assertAlmostEqual(m["sup"], 0.2)  # 最寄りの谷は100

    def test_trend_and_rsi(self):
        c = 100 * 1.1 ** (np.arange(520) / ind.WEEKS_PER_YEAR)
        self.assertAlmostEqual(ind.trend(c), 0.1, places=4)
        self.assertEqual(ind.rsi(c), 100.0)
        self.assertIsNone(ind.rsi(c[:10]))
        self.assertLess(ind.rsi(c[::-1]), 1)


def st(date, pt, **k):
    base = {"DiscDate": date, "DiscTime": "15:00:00", "Code": "13010", "DiscNo": date,
            "DocType": f"{pt}FinancialStatements_Consolidated_JP", "CurPerType": pt, "ShOutFY": "1000", "TrShFY": "100"}
    base.update({a: str(b) for a, b in k.items()})
    return base


class Financials(unittest.TestCase):
    def rows(self):
        return [
            st("2025-08-08", "1Q", CurFYEn="2026-03-31", Sales=100e8, OP=-10e8, EqAR=0.5),
            st("2026-05-12", "FY", CurFYEn="2026-03-31", NxtFYEn="2027-03-31", Sales=500e8, OP=50e8, NP=30e8, EqAR=0.55,
               BPS=500, NxFOP=60e8, NxFNp=40e8, NxFEPS=50, NxFDivAnn=20, NxFSales=600e8),
            {"DiscDate": "2026-06-30", "DiscTime": "16:00", "Code": "13010", "DiscNo": "x", "DocType": "EarnForecastRevision",
             "CurPerType": "FY", "CurFYEn": "2027-03-31", "FOP": str(45e8), "FNP": str(30e8), "FEPS": "40", "FSales": str(590e8)},
            {"DiscDate": "2026-07-01", "DiscTime": "16:00", "Code": "13010", "DiscNo": "y", "DocType": "DividendForecastRevision",
             "CurPerType": "FY", "CurFYEn": "2027-03-31", "FDivAnn": "0"},
            st("2026-08-08", "1Q", CurFYEn="2027-03-31", Sales=90e8, OP=5e8, NP=3e8, EPS=3, FOP=48e8, FNP=30e8, FEPS=40,
               FSales=600e8, FDivAnn=0, EqAR=""),
        ]

    def test_after_quarter(self):
        m = fin.fin_metrics(self.rows(), 1000.0)
        self.assertEqual(m["shares"], 900)
        self.assertEqual(m["eq"], 0.55)  # 四半期が空なら直前の値
        self.assertEqual(m["pbr"], 2.0)
        self.assertEqual(m["per"], 25.0)
        self.assertEqual(m["dv"], 0)
        self.assertEqual(m["opf"], 48)
        e = m["earn"]
        self.assertEqual(e["pt"], "1Q")
        self.assertFalse(e["fcnext"])
        op = e["rows"][1]
        self.assertEqual(op[1], 5)
        self.assertAlmostEqual(op[2], 1.5)  # 前年の赤字幅に対する増減 (5-(-10))/10
        self.assertAlmostEqual(op[4], 5 / 48, places=3)
        self.assertEqual(e["hist"][0][1], "決算短信(1Q)")
        self.assertEqual(e["hist"][1][1], "配当予想の修正")
        self.assertEqual(e["hist"][2][1], "業績予想の修正")
        d = m["dist"]
        self.assertAlmostEqual(d["oy"], 1.5)
        self.assertAlmostEqual(d["sy"], -0.1)
        self.assertAlmostEqual(d["fcp"], 48 / 50 - 1)
        self.assertAlmostEqual(d["fcr"], 48 / 60 - 1)

    def test_after_fy_with_revision(self):
        m = fin.fin_metrics(self.rows()[:4], 1000.0)
        e = m["earn"]
        self.assertTrue(e["fy"])
        self.assertTrue(e["fcnext"])
        self.assertEqual(m["opf"], 45)  # 修正を反映
        self.assertEqual(m["dv"], 0)  # 無配
        self.assertAlmostEqual(m["dist"]["fcr"], 45 / 60 - 1)
        self.assertIsNone(e["rows"][1][4])  # 通期は進捗率なし

    def test_loss_and_blank(self):
        m = fin.fin_metrics([st("2026-08-08", "1Q", CurFYEn="2027-03-31", FNP=-5e8, FEPS=-3, NCEqAR=0.3, ShEq=9e4)], 100.0)
        self.assertTrue(m["loss"])
        self.assertNotIn("per", m)
        self.assertEqual(m["eq"], 0.3)
        self.assertEqual(m["pbr"], 1.0)  # ShEq / 株数
        self.assertNotIn("dv", m)  # 不明
        self.assertEqual(fin.fin_metrics([], 100.0), {})

    def test_stale_revision_ignored(self):
        rows = self.rows()[1:2] + [{"DiscDate": "2026-05-20", "DiscTime": "", "Code": "13010", "DiscNo": "z",
                                    "DocType": "EarnForecastRevision", "CurFYEn": "2026-03-31", "FOP": "1"}]
        self.assertEqual(fin.fin_metrics(rows, 1000.0)["opf"], 60)


class Margin(unittest.TestCase):
    def test_metrics(self):
        dates = [(dt.date(2026, 1, 2) + dt.timedelta(days=7 * i)).isoformat() for i in range(12)]
        tables = [{"A": [100.0, 1000.0 - 10 * i]} for i in range(12)]
        m = compute.margin_metrics("A", dates, tables, 100.0, 1e6)
        self.assertEqual(m["mw"], 12)
        self.assertEqual(m["mr"], 8.9)
        self.assertAlmostEqual(m["mlc"], 890 / 970 - 1, places=3)  # 8週前と比較
        self.assertEqual(m["md"], 8.9)

    def test_young_data(self):
        dates = ["2026-01-02", "2026-01-09"]
        tables = [{"A": [0.0, 10.0]}, {"A": [0.0, 12.0]}]
        m = compute.margin_metrics("A", dates, tables, None, None)
        self.assertNotIn("mlc", m)  # 3週未満は出さない
        self.assertNotIn("mr", m)  # 売り残なし
        self.assertEqual(m["ml"], 12)
        m = compute.margin_metrics("A", dates + ["2026-01-16"], tables + [{"A": [0.0, 15.0]}], None, None)
        self.assertAlmostEqual(m["mlc"], 0.5)  # 手元の最古と比較

    def test_weekly_keeps_last_of_week(self):
        ds, t = compute.weekly_margin({"2026-11-02": {"A": 1}, "2026-11-05": {"A": 2}, "2026-11-10": {"A": 3}})
        self.assertEqual(ds, ["2026-11-05", "2026-11-10"])


class Shorts(unittest.TestCase):
    def test_metrics(self):
        rows = [
            ["2026-09-01", "A", "X", 0.01, None, None, 0, ""],
            ["2026-10-15", "A", "X", 0.012, "2026-09-01", 0.01, 0, ""],
            ["2026-10-20", "A", "Y", 0.02, None, None, 0, ""],
            ["2026-10-21", "A", "Z", 0.9, None, None, 1, ""],  # 備考つきは除外
            ["2026-10-22", "A", "W", 0.01, None, None, 0, ""],
            ["2026-11-02", "A", "W", 0.004, "2026-10-22", 0.01, 0, ""],  # 0.5%未満に減った
            ["2025-01-01", "A", "OLD", 0.03, None, None, 0, ""],  # 1年より前
        ]
        weeks = [(dt.date(2026, 11, 6) - dt.timedelta(days=7 * i)).isoformat() for i in range(30)][::-1]
        m = compute.short_metrics(compute.short_index(rows)["A"], weeks)
        self.assertAlmostEqual(m["st"], 0.032)
        self.assertEqual(m["sn"], 2)
        self.assertEqual(m["sh"][0][0], "Y")
        self.assertEqual(m["sh"][1][3], 0.01)
        self.assertEqual(len(m["sts"]), 26)
        self.assertAlmostEqual(m["stc"], 0.032 - 0.01)  # 4週前はXの最初の報告だけ

    def test_same_day_sum_and_dedup(self):
        rows = [["2026-10-01", "A", "X", 0.003, None, None, 0, "f1"],
                ["2026-10-01", "A", "X", 0.003, None, None, 0, "f2"],
                ["2026-10-01", "A", "X", 0.003, None, None, 0, "f2"]]
        m = compute.short_metrics(compute.short_index(rows)["A"], ["2026-10-02"])
        self.assertAlmostEqual(m["st"], 0.006)


class Rotation(unittest.TestCase):
    def test_phase(self):
        self.assertEqual(rotation.phase_of(-1, 1), "回復期")
        self.assertEqual(rotation.phase_of(1, 1), "好況期")
        self.assertEqual(rotation.phase_of(1, -1), "後退期")
        self.assertEqual(rotation.phase_of(-1, -1), "不況期")

    def test_all_33_sectors_mapped_with_variants(self):
        self.assertEqual(sum(len(v[1]) for v in rotation.GROUPS.values()), 33)
        for variant, g in (("証券･商品先物取引業", "金融"), ("証券、商品先物取引業", "金融"), ("石油･石炭製品", "エネルギー"),
                           ("ガラス・土石製品", "素材"), ("倉庫・運輸関連業", "製造業"), ("水産・農林業", "生活必需品"),
                           ("電気･ガス業", "公共"), ("パルプ・紙", "素材")):
            self.assertEqual(rotation.SECTOR_TO_GROUP[rotation.sector_key(variant)], g)

    def test_macro(self):
        months = pd.period_range("2016-01", "2026-09", freq="M")
        def rows(f):
            return [[m.to_timestamp().date().isoformat(), f(i)] for i, m in enumerate(months)]
        fred = {"DGS10": rows(lambda i: 2.0), "CFNAIMA3": rows(lambda i: 0.5),
                "UNRATE": rows(lambda i: 4.0), "FEDFUNDS": rows(lambda i: 1.0), "IRLTLT01JPM156N": rows(lambda i: 1.0)}
        m = rotation.macro_view(fred)
        self.assertEqual(m["r"], 0)
        self.assertEqual(m["e"], 0.5)
        self.assertEqual(m["phase"], "好況期")  # 0は「高い」側
        self.assertEqual(len(m["ind"]), 5)
        self.assertEqual(len(m["trail"]), 25)
        self.assertIsNone(rotation.macro_view({}))

    def test_macro_missing_year_ago_month(self):
        months = pd.period_range("2016-01", "2026-09", freq="M")
        def rows(f, skip=()):
            return [[m.to_timestamp().date().isoformat(), f(i)] for i, m in enumerate(months) if str(m) not in skip]
        fred = {"DGS10": rows(lambda i: 2.0), "CFNAIMA3": rows(lambda i: 0.5),
                "UNRATE": rows(lambda i: float(i), skip={"2025-09"})}
        ind = {x["id"]: x for x in rotation.macro_view(fred)["ind"]}
        self.assertEqual(ind["UNRATE"]["v1y"], float(len(months) - 14))  # 2025-08 の値


class SplitAdjust(unittest.TestCase):
    def test_adjfactor(self):
        idx = pd.date_range("2026-01-05", periods=4, freq="B")
        df = pd.DataFrame({"C": [1000., 1000., 500., 510.], "H": [1010., 1010., 505., 515.], "L": [990., 990., 495., 505.],
                           "Vo": [100., 100., 200., 200.], "AdjFactor": [1., 1., .5, 1.]}, index=idx)
        a = jq_prices.adjust(df)
        self.assertEqual(a["Close"].tolist(), [500., 500., 500., 510.])
        self.assertEqual(a["Volume"].tolist(), [200., 200., 200., 200.])


class FakeResp:
    def __init__(self, code, body):
        self.status_code, self._b, self.text = code, body, json.dumps(body)

    def json(self):
        return self._b


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return self.responses.pop(0)


class JQuantsClient(unittest.TestCase):
    def test_pagination_and_429(self):
        s = FakeSession([FakeResp(429, {}), FakeResp(200, {"data": [{"Code": "1"}], "pagination_key": "k"}),
                         FakeResp(200, {"data": [{"Code": "2"}]})])
        waits = []
        c = jquants.JQuants("key", session=s, sleep=waits.append)
        self.assertEqual(s.headers["x-api-key"], "key")
        rows = c.fins_on(dt.date(2026, 8, 1))
        self.assertEqual([r["Code"] for r in rows], ["1", "2"])
        self.assertEqual(s.calls[-1][1]["pagination_key"], "k")
        self.assertIn(120, waits)
        self.assertTrue(s.calls[0][0].endswith("/v2/fins/summary"))

    def test_free_lag(self):
        c = jquants.JQuants("k", session=FakeSession([]))
        self.assertEqual(c.latest_date(dt.date(2026, 11, 9)), dt.date(2026, 8, 15))
        c = jquants.JQuants("k", plan="standard", session=FakeSession([]))
        self.assertEqual(c.latest_date(dt.date(2026, 11, 9)), dt.date(2026, 11, 9))

    def test_needs_key(self):
        with self.assertRaises(jquants.JQuantsError):
            jquants.JQuants("")


class Crypto(unittest.TestCase):
    def test_roundtrip(self):
        ct, env = packer.seal({"a": "日本語"}, "pw")
        self.assertEqual(packer.unseal(ct, env, "pw"), {"a": "日本語"})
        self.assertEqual(env["iter"], 250000)
        self.assertEqual(len(base64.b64decode(env["iv"])), 12)
        self.assertNotIn("日本語".encode(), ct)
        with self.assertRaises(Exception):
            packer.unseal(ct, env, "wrong")
        with self.assertRaises(ValueError):
            packer.seal({}, "")


class Pipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = compute.build(demo.make(n=150, today=dt.date(2026, 11, 9)))

    def test_shape(self):
        d = self.data
        self.assertEqual(len(d["stocks"]), 150)
        json.dumps(d, allow_nan=False)
        self.assertTrue(d["demo"])
        self.assertTrue(d["features"]["margin"] and d["features"]["shorts"])
        self.assertTrue(d["rot"]["macro"]["phase"])
        self.assertIn(d["rot"]["market"]["lead"], rotation.PHASES)
        for s in d["stocks"]:
            self.assertTrue(0 <= s["pos"] <= 1, s["c"])
            self.assertLessEqual(len(s["ch"]["c"]), 121)
            self.assertLessEqual(len(s["wk"]["c"]), 105)
            for k in ("y5", "y2", "y1"):
                if k in s:
                    limit = 61 if k == "y5" else {"y2": 105, "y1": 53}[k]
                    self.assertTrue(all(0 <= p[0] < limit for p in s[k]["piv"]), (s["c"], k, s[k]["piv"]))
            self.assertTrue(all(0 <= p[0] < len(s["ch"]["c"]) for p in s["ch"]["piv"]))

    def test_chart_rounding(self):
        for s in self.data["stocks"]:
            for v in s["ch"]["c"]:
                if v >= 100:
                    self.assertIsInstance(v, int)
                else:
                    self.assertEqual(v, round(v, 1))

    def test_split_stock_has_no_gap(self):
        # 分割を仕込んだ銘柄でも週足に分割の段差が出ない
        inp = demo.make(n=30, today=dt.date(2026, 11, 9))
        c = inp.panels["c"]["D005"].dropna()
        self.assertLess(c.pct_change().abs().max(), 0.7)

    def test_new_listing_excluded_from_long_spans(self):
        short = [s for s in self.data["stocks"] if s.get("x10")]
        self.assertTrue(short)
        self.assertTrue(all(s["ch"]["c"] for s in short))

    def test_rel_uses_medians(self):
        s = self.data["stocks"][0]
        self.assertEqual(sorted(s["dist"]["rel"]), ["13", "26", "52"])
        self.assertEqual(len(s["dist"]["rel"]["52"]), 3)


class Cli(unittest.TestCase):
    def env(self, **k):
        e = {x: v for x, v in os.environ.items() if x not in ("SITE_PASSWORD", "JQUANTS_API_KEY", "DATA_SOURCE")}
        e.update(k)
        return e

    def test_refuses_without_password(self):
        r = subprocess.run([sys.executable, "-m", "uneri.main"], cwd=ROOT, env=self.env(JQUANTS_API_KEY="x"),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("SITE_PASSWORD", r.stderr)

    def test_demo_encrypted_output(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "pub"
            r = subprocess.run([sys.executable, "-m", "uneri.main", "--demo", "--demo-n", "40", "--out", str(out)],
                               cwd=ROOT, env=self.env(SITE_PASSWORD="abc"), capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((out / "data.json").exists())
            env = json.loads((out / "data.enc").read_text())
            data = packer.unseal((out / "data.bin").read_bytes(), env, "abc")
            self.assertEqual(len(data["stocks"]), 40)
            self.assertTrue((out / "index.html").read_text(encoding="utf-8").lower().startswith("<!doctype html>"))
            self.assertTrue((out / "sector.html").read_text(encoding="utf-8").lower().startswith("<!doctype html>"))

    def test_real_mode_never_writes_plain(self):
        from uneri import main

        with self.assertRaises(SystemExit):
            main.publish({"demo": False, "stocks": []}, Path(tempfile.mkdtemp()) / "p", None)


if __name__ == "__main__":
    unittest.main()
