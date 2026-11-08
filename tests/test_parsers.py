"""空売りExcel・信用残PDF・Yahoo応答を合成してパーサーをテストする。"""
import datetime as dt
import io
import unittest

import numpy as np
import pandas as pd

from uneri import fred, jpx, yahoo

HEAD = ["", "計算年月日", "銘柄コード", "銘柄名\n（日本語／英語）", "", "商号・名称・氏名", "住所・所在地",
        "委託者・投資一任契約の相手方の商号・名称・氏名", "委託者・投資一任契約の相手方の住所・所在地",
        "信託財産・運用財産の名称", "空売り残高割合", "空売り残高数量", "空売り残高売買単位数", "直近計算年月日",
        "直近空売り残高割合", "備考"]


def make_xls(rows, head=HEAD):
    import xlwt

    wb = xlwt.Workbook()
    sh = wb.add_sheet("20261106")
    sh.write(1, 1, "空売り残高に関する情報")
    for j, h in enumerate(head):
        sh.write(6, j, h)
        sh.write(7, j, "english")
    for i, row in enumerate(rows):
        for j, v in enumerate(row):
            sh.write(8 + i, j, v)
    b = io.BytesIO()
    wb.save(b)
    return b.getvalue()


def row(date, code, name, ratio, prev_date="", prev="", note="", client="", fund=""):
    return ["", date, code, "銘柄", "Stock", name, "住所", client, "", fund, ratio, 1000, 10, prev_date, prev, note]


class ShortExcel(unittest.TestCase):
    def test_xls(self):
        r = jpx.parse_short_excel(make_xls([
            row(46331.0, 1333.0, "Citigroup　Global  Markets", 0.0079, 46325.0, 0.0083),
            row("2026/11/05", "130A", "個人", 0.012, "", "", "顧客取引に係るヘッジポジション"),
            row(46331.0, "", "コードなし", 0.01),
        ]), "20261106_Short_Positions.xls")
        self.assertEqual(len(r), 2)
        self.assertEqual(r[0][:7], ["2026-11-05", "1333", "Citigroup Global Markets", 0.0079, "2026-10-30", 0.0083, 0])
        self.assertEqual(r[1][1], "130A")
        self.assertEqual(r[1][6], 1)  # 備考あり

    def test_percent_strings_and_numbers(self):
        r = jpx.parse_short_excel(make_xls([row(46331.0, 1333.0, "A", "0.79%", 46325.0, "0.83%")]))
        self.assertAlmostEqual(r[0][3], 0.0079)
        self.assertAlmostEqual(r[0][5], 0.0083)
        r = jpx.parse_short_excel(make_xls([row(46331.0, 1333.0, "A", 0.79, 46325.0, 0.83),
                                            row(46331.0, 1334.0, "B", 1.5)]))
        self.assertAlmostEqual(r[0][3], 0.0079)
        self.assertAlmostEqual(r[0][5], 0.0083)

    def test_xlsx(self):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        for _ in range(3):
            ws.append([""])
        ws.append(HEAD)
        ws.append(row(dt.datetime(2026, 11, 5), "13330", "A", 0.01))
        b = io.BytesIO()
        wb.save(b)
        r = jpx.parse_short_excel(b.getvalue(), "x.xlsx")
        self.assertEqual(r[0][:4], ["2026-11-05", "1333", "A", 0.01])

    def test_no_header(self):
        with self.assertRaises(ValueError):
            jpx.parse_short_excel(make_xls([], head=["", "違う見出し"]))

    def test_links(self):
        html = '<option value="/markets/public/short-selling/00-archives-06.html">x</option>' \
               '<a href="/markets/public/short-selling/a-att/20261106_Short_Positions.xls">'
        base = jpx.SHORT_INDEX
        self.assertEqual(jpx.archive_pages(html, base),
                         ["https://www.jpx.co.jp/markets/public/short-selling/00-archives-06.html"])
        self.assertEqual(jpx.short_links(html, base),
                         ["https://www.jpx.co.jp/markets/public/short-selling/a-att/20261106_Short_Positions.xls"])


REAL = [
    "銘柄 合計 Total 売残高 Outstanding Sales 買残高 Outstanding Purchases",
    "B 極洋 普通株式 プライム 貸 13010 JP3257200000株数 Shs. 2,100 1,200 0.0% 128,000 ▲ 15,400 1.1% 700 600 1,400 600 36,800 ▲ 1,800 91,200 ▲ 13,600",
    "KYOKUYO CO.,LTD. Prime Loan 13010 JP3257200000金額 Val. 9,745,000 5,667,000 - 588,593,500 ▲ 56,777,500 - 3,278,000 2,824,000 6,467,000 2,843,000 167,741,000 ▲ 6,032,000 420,852,500 ▲ 50,745,500",
    "B Ｃｏｃｏｌｉｖｅ 普通株式 グロース 制 137A0 JP3297390001株数 Shs. 0 0 0.0% 98,200 ▲ 1,200 3.2% 0 0 0 0 60,600 ▲ 1,000 37,600 ▲ 200",
    "B ビジネスエンジニアリング 普通株式プライム 貸 48280 JP3618800001株数 Shs. 7,100 ▲ 3,100 0.0% 61,400 1,200 0.1% 0 ▲ 2,600 7,100 ▲ 500 28,600 1,300 32,800 ▲ 100",
    "投信等 小計 553 銘柄 株数 Shs. 33,040,786 1,884,641 - 225,362,367 ▲ 3,441,317 - 292,315 42,106 32,748,471 1,842,535 134,515,517 ▲ 2,434,764 90,846,850 ▲ 1,006,553",
]


class MarginPdf(unittest.TestCase):
    def test_lines(self):
        m = jpx.parse_margin_lines(REAL)
        self.assertEqual(m, {"1301": [2100, 128000], "137A": [0, 98200], "4828": [7100, 61400]})

    def test_swap_when_short_exceeds_long(self):
        swapped = ["B 甲 普通株式 プライム 貸 13010 JP3257200000株数 Shs. 128,000 0 1.0% 2,100 0 0.0% 36,800 0 91,200 0 700 0 1,400 0"]
        self.assertEqual(jpx.parse_margin_lines(swapped), {"1301": [2100, 128000]})

    def test_noncanonical_position(self):
        # 正規の位置からずれていても「合計=一般+制度」で探す
        nums = [5, 100, 1, 200, 2, 60, 1, 40, 1, 150, 1, 50, 1]
        self.assertEqual(jpx.pick_totals(nums), (100, 200))

    def test_pdf_roundtrip(self):
        from reportlab.lib.pagesizes import A3, landscape
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.pdfgen import canvas

        pdfmetrics.registerFont(UnicodeCIDFont("HeiseiKakuGo-W5"))
        b = io.BytesIO()
        c = canvas.Canvas(b, pagesize=landscape(A3))
        c.setFont("HeiseiKakuGo-W5", 7)
        for i, line in enumerate(REAL):
            c.drawString(10, 700 - i * 14, line)
        c.save()
        m = jpx.parse_margin_pdf(b.getvalue())
        self.assertEqual(m.get("1301"), [2100, 128000])
        self.assertEqual(m.get("137A"), [0, 98200])

    def test_links(self):
        html = '<a href="/markets/statistics-equities/margin/x-att/20261105_mtall.pdf">'
        self.assertEqual(jpx.margin_links(html, jpx.MARGIN_INDEX)[0][0], "2026-11-05")

    def test_thin(self):
        s = {d: {"a": [1, 2]} for d in ("2026-01-05", "2026-01-09", "2026-01-12", "2026-11-04", "2026-11-05")}
        kept = jpx.thin_margin(s)
        self.assertNotIn("2026-01-05", kept)
        self.assertIn("2026-01-09", kept)
        self.assertIn("2026-11-04", kept)


def yahoo_frame(tickers, dates, seed=1):
    rng = np.random.default_rng(seed)
    cols = pd.MultiIndex.from_product([tickers, ["Open", "High", "Low", "Close", "Adj Close", "Volume"]])
    df = pd.DataFrame(rng.uniform(100, 200, (len(dates), len(cols))), index=dates, columns=cols)
    df.index.name = "Date"
    return df


class Yahoo(unittest.TestCase):
    def test_split_and_weekly(self):
        dates = pd.date_range("2026-10-05", periods=5, freq="W-MON")
        df = yahoo_frame(["7203.T", "130A.T", "9999.T"], dates)
        df[("9999.T", "Close")] = np.nan
        got = yahoo.split_download(df, ["7203.T", "130A.T", "9999.T", "0000.T"])
        self.assertEqual(sorted(got), ["130A.T", "7203.T"])
        wk = yahoo.to_weekly(got)["7203.T"]
        self.assertTrue((wk.index.day_of_week == 4).all())  # 月曜日付 → 金曜日付にそろう
        self.assertEqual(len(wk), 5)

    def test_duplicate_week_merged(self):
        idx = pd.to_datetime(["2026-10-26", "2026-11-02", "2026-11-06"])
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0], "High": [1.5, 2.5, 3.5], "Low": [.5, 1.5, 2.5],
                           "Volume": [10.0, 20.0, 30.0]}, index=idx)
        wk = yahoo.to_weekly({"a": df})["a"]
        self.assertEqual(len(wk), 2)
        self.assertEqual(wk["c"].iloc[-1], 3.0)
        self.assertEqual(wk["h"].iloc[-1], 3.5)
        self.assertEqual(wk["v"].iloc[-1], 50.0)

    def test_rate_limit_retry_and_small_batches(self):
        dates = pd.date_range("2026-10-05", periods=3, freq="W-MON")
        calls, waits = [], []

        def fake(tickers, **kw):
            calls.append(list(tickers))
            self.assertFalse(kw["auto_adjust"])
            if len(calls) == 1:
                raise RuntimeError("YFRateLimitError")
            # 大きい塊では b を返さない(小分けで取れる)
            ok = [t for t in tickers if t != "b.T" or len(tickers) <= 10]
            return yahoo_frame(ok, dates)

        tick = [f"{c}.T" for c in "ab"] + [f"{i}.T" for i in range(20)]
        got = yahoo.fetch_batched(tick, "10y", "1wk", fake, sleep=waits.append, batch=80, small=10)
        self.assertEqual(len(got), len(tick))
        self.assertIn(60, waits)
        self.assertEqual(calls[-1], ["b.T"])

    def test_fallback_to_previous(self):
        import tempfile
        from pathlib import Path

        dates = pd.date_range("2026-10-05", periods=4, freq="W-MON")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            yahoo.fetch_prices(["1111", "2222"], d, downloader=lambda t, **k: yahoo_frame(list(t), dates), sleep=lambda s: None)

            def only_one(t, **k):
                ok = [x for x in t if x == "1111.T"]
                if not ok:
                    return pd.DataFrame()
                return yahoo_frame(ok, dates, seed=2)

            pnl, daily, reused = yahoo.fetch_prices(["1111", "2222"], d, downloader=only_one, sleep=lambda s: None)
            self.assertEqual(reused, 1)
            self.assertIn("2222", pnl["c"].columns)
            self.assertIn("2222", daily)

    def test_daily(self):
        dates = pd.date_range("2026-10-01", periods=25, freq="B")
        df = pd.DataFrame({"Close": 100.0, "High": 101.0, "Low": 99.0, "Volume": 1000.0}, index=dates)
        s = yahoo.daily_stats({"7203.T": df})["7203"]
        self.assertAlmostEqual(s["tv"], 0.1)
        self.assertEqual(s["date"], dates[-1].date().isoformat())


class Fred(unittest.TestCase):
    def test_parse(self):
        v = fred.parse_csv("observation_date,DGS10\n2026-01-01,4.1\n2026-01-02,.\n2026-01-05,\n2026-01-06,4.2\n")
        self.assertEqual(v, [["2026-01-01", 4.1], ["2026-01-06", 4.2]])


if __name__ == "__main__":
    unittest.main()
