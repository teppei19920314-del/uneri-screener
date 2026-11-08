"""JPX の公表資料: 空売り残高(Excel)と銘柄別信用取引残高(PDF)。

JPX へのアクセスは1日1回・1秒間隔、ブラウザ相当の User-Agent で行う。
"""
from __future__ import annotations

import datetime as dt
import io
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import requests

from .util import BROWSER_UA, Throttle, code4, log, read_json_gz, write_json_gz

SHORT_INDEX = "https://www.jpx.co.jp/markets/public/short-selling/index.html"
MARGIN_INDEX = "https://www.jpx.co.jp/markets/statistics-equities/margin/01.html"
MARGIN_KEEP_DAYS = 400
SHORT_KEEP_DAYS = 400


class JpxClient:
    def __init__(self, session=None, sleep=time.sleep):
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": BROWSER_UA, "Accept-Language": "ja,en;q=0.8"})
        self.t = Throttle(1.0, sleep=sleep)

    def get(self, url: str, timeout: int = 90) -> requests.Response:
        self.t.wait()
        r = self.s.get(url, timeout=timeout)
        r.raise_for_status()
        return r


# ---------------------------------------------------------------- 空売り
def short_links(html: str, base: str) -> list[str]:
    links = re.findall(r'href="([^"]*_Short_Positions\.xlsx?)"', html, flags=re.I)
    return [urljoin(base, u) for u in links]


def archive_pages(html: str, base: str) -> list[str]:
    pages = re.findall(r'(?:value|href)="([^"]*short-selling/00-archives-\d+\.html)"', html)
    out: list[str] = []
    for p in pages:
        u = urljoin(base, p)
        if u not in out:
            out.append(u)
    return out


def _excel_date(v, datemode=0) -> str | None:
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, (int, float)):
        if v < 20000:
            return None
        if v > 19000000:  # 20261105 のような数値
            s = str(int(v))
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        return (dt.date(1899, 12, 30) + dt.timedelta(days=int(v))).isoformat()
    m = re.search(r"(\d{4})[/\-.年](\d{1,2})[/\-.月](\d{1,2})", str(v))
    if m:
        return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.fullmatch(r"\s*(\d{4})(\d{2})(\d{2})\s*", str(v))
    return f"{m[1]}-{m[2]}-{m[3]}" if m else None


def _ratio(v):
    """割合を小数に。'0.79%' は 0.0079。数値はそのまま返し、後でファイル単位で%表記を判定する。"""
    if v in (None, ""):
        return None, False
    if isinstance(v, (int, float)):
        return float(v), False
    s = str(v).strip().replace(",", "").replace("％", "%")
    pct = s.endswith("%")
    try:
        x = float(s.rstrip("%"))
    except ValueError:
        return None, False
    return (x / 100 if pct else x), pct


def _read_table(content: bytes, filename: str) -> list[list]:
    if filename.lower().endswith(".xlsx"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    import xlrd

    wb = xlrd.open_workbook(file_contents=content)
    sh = wb.sheet_by_index(0)
    return [[sh.cell_value(i, j) for j in range(sh.ncols)] for i in range(sh.nrows)]


def _find_columns(rows: list[list]):
    """見出しの文言で列を判定する。"""
    for i, row in enumerate(rows[:40]):
        cells = [re.sub(r"\s+", "", str(c or "")) for c in row]
        if not any("銘柄コード" in c for c in cells):
            continue
        col: dict[str, int] = {}
        for j, c in enumerate(cells):
            if c.startswith("直近計算年月日"):
                col["prev_date"] = j
            elif c.startswith("計算年月日"):
                col["date"] = j
            elif c.startswith("銘柄コード"):
                col["code"] = j
            elif "商号・名称・氏名" in c and not c.startswith("委託者") and "name" not in col:
                col["name"] = j
            elif c.startswith("委託者") and "商号" in c:
                col["client"] = j
            elif "信託財産" in c:
                col["fund"] = j
            elif c.startswith("直近空売り残高割合"):
                col["prev_ratio"] = j
            elif c.startswith("空売り残高割合"):
                col["ratio"] = j
            elif c.startswith("備考"):
                col["note"] = j
        need = {"date", "code", "name", "ratio"}
        if need <= col.keys():
            return i, col
    raise ValueError("空売りExcelの見出しが見つかりません")


def parse_short_excel(content: bytes, filename: str = "x.xls") -> list[list]:
    """1行 = [計算日, 4桁コード, 機関名, 割合, 直近計算日, 直近割合, 備考あり(0/1), 委託者+ファンド]"""
    rows = _read_table(content, filename)
    head, col = _find_columns(rows)
    parsed = []
    for row in rows[head + 1:]:
        if len(row) <= col["ratio"]:
            continue
        date = _excel_date(row[col["date"]])
        raw = row[col["code"]]
        code = code4(int(raw) if isinstance(raw, float) and raw == int(raw) else raw)
        if not date or not code:
            continue
        ratio, rp = _ratio(row[col["ratio"]])
        if ratio is None:
            continue
        prev, pp = (None, False)
        if "prev_ratio" in col and len(row) > col["prev_ratio"]:
            prev, pp = _ratio(row[col["prev_ratio"]])
        prev_date = _excel_date(row[col["prev_date"]]) if "prev_date" in col and len(row) > col["prev_date"] else None
        note = str(row[col["note"]] or "").strip() if "note" in col and len(row) > col["note"] else ""
        extra = "|".join(
            str(row[col[k]] or "").strip() for k in ("client", "fund") if k in col and len(row) > col[k]
        ).strip("|")
        name = re.sub(r"\s+", " ", str(row[col["name"]] or "").replace("　", " ")).strip()
        parsed.append([date, code, name, ratio, prev_date, prev, 1 if note else 0, extra, rp, pp])
    # %表記の数値(0.79 = 0.79%)はファイル全体の中央値で判定する。割合は普通 0.5〜5% 程度
    plain = sorted(r[3] for r in parsed if not r[8])
    is_percent = bool(plain) and plain[len(plain) // 2] > 0.1
    out = []
    for date, code, name, ratio, prev_date, prev, note, extra, rp, pp in parsed:
        if is_percent and not rp:
            ratio /= 100
        if is_percent and prev is not None and not pp:
            prev /= 100
        out.append([date, code, name, round(ratio, 6), prev_date,
                    None if prev is None else round(prev, 6), note, extra])
    return out


def update_shorts(client: JpxClient, cache_dir: Path, today: dt.date) -> list[list]:
    path = cache_dir / "shorts.json.gz"
    store = read_json_gz(path, {"files": [], "rows": []}) or {"files": [], "rows": []}
    seen = set(store["files"])
    first = client.get(SHORT_INDEX).text
    links = short_links(first, SHORT_INDEX)
    for page in archive_pages(first, SHORT_INDEX):
        try:
            links += short_links(client.get(page).text, page)
        except requests.RequestException as e:
            log(f"空売り: バックナンバーの取得に失敗 {page}: {e}")
    new = [u for u in dict.fromkeys(links) if u.rsplit("/", 1)[-1] not in seen]
    log(f"空売り: 掲載 {len(set(links))} ファイル、うち新規 {len(new)}")
    rows = store["rows"]
    done = 0
    for u in new:
        name = u.rsplit("/", 1)[-1]
        try:
            rows.extend(parse_short_excel(client.get(u).content, name))
            seen.add(name)
            done += 1
        except Exception as e:  # 1ファイルの不調で全体を止めない
            log(f"空売り: {name} を読めません: {e}")
        if done and done % 50 == 0:
            write_json_gz(path, {"files": sorted(seen), "rows": rows})
    cutoff = (today - dt.timedelta(days=SHORT_KEEP_DAYS)).isoformat()
    uniq = {}
    for r in rows:
        if r[0] >= cutoff:
            uniq[(r[0], r[1], r[2], r[7])] = r
    rows = sorted(uniq.values(), key=lambda r: (r[0], r[1]))
    files = sorted(seen)[-500:]
    write_json_gz(path, {"files": files, "rows": rows})
    return rows


# ---------------------------------------------------------------- 信用残
_ROW = re.compile(r"(?<![0-9A-Za-z])([0-9][0-9A-Z]{3}[0-9A-Z])\s+JP[0-9A-Z]{10}\s*株数\s*Shs\.?\s*(.*)$")
_TOKEN = re.compile(r"▲?\s*[\d,]+(?:\.\d+)?%?|-")


def _numbers(rest: str) -> list[float]:
    out = []
    for t in _TOKEN.findall(rest):
        t = t.replace(" ", "")
        if t == "-" or t.endswith("%"):
            continue  # 上場比は使わない
        neg = t.startswith("▲")
        v = float(t.lstrip("▲").replace(",", ""))
        out.append(-v if neg else v)
    return out


def pick_totals(nums: list[float]):
    """上段の数値から「合計=一般信用+制度信用」が成り立つ位置を探す。

    正規の並び: 売残, 前日比, 買残, 前日比, 売一般, 前日比, 売制度, 前日比, 買一般, 前日比, 買制度, 前日比
    左の合計を売り残、右の合計を買い残として返す。
    """
    n = len(nums)
    # まず正規の並びを確認する(0が多い行で別の位置に偶然一致しないように)
    if n >= 12 and nums[4] + nums[6] == nums[0] and nums[8] + nums[10] == nums[2]:
        return nums[0], nums[2]
    for i in range(n):
        if nums[i] < 0:
            continue
        for j in range(i + 1, n):
            if nums[j] < 0:
                continue
            for k in range(j + 1, n - 6):
                if nums[k] + nums[k + 2] == nums[i] and nums[k + 4] + nums[k + 6] == nums[j]:
                    return nums[i], nums[j]
    return None


def parse_margin_lines(lines: list[str]) -> dict[str, list[float]]:
    """{4桁コード: [売り残, 買い残]}。市場全体で売り残>買い残なら列を入れ替える。"""
    out: dict[str, list[float]] = {}
    bad = 0
    for line in lines:
        m = _ROW.search(line)
        if not m:
            continue
        code = code4(m[1])
        if code is None:
            continue
        t = pick_totals(_numbers(m[2]))
        if t is None:
            bad += 1
            continue
        out[code] = [t[0], t[1]]
    if bad:
        log(f"信用残: 数値を読めない行 {bad}")
    if sum(v[0] for v in out.values()) > sum(v[1] for v in out.values()):
        log("信用残: 売り残>買い残のため列を入れ替えます")
        out = {k: [v[1], v[0]] for k, v in out.items()}
    return out


def parse_margin_pdf(content: bytes) -> dict[str, list[float]]:
    import pdfplumber

    lines: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            lines.extend((page.extract_text() or "").split("\n"))
            page.flush_cache()
    return parse_margin_lines(lines)


def margin_links(html: str, base: str) -> list[tuple[str, str]]:
    out = []
    for u in re.findall(r'href="([^"]*?(\d{8})_mtall\.pdf)"', html):
        d = u[1]
        out.append((f"{d[:4]}-{d[4:6]}-{d[6:8]}", urljoin(base, u[0])))
    return out


def update_margin(client: JpxClient, cache_dir: Path, today: dt.date) -> dict:
    """掲載は直近5営業日分だけなので、まだ持っていない日を取り込んで蓄積する。"""
    path = cache_dir / "margin_by_date.json.gz"
    store: dict[str, dict] = read_json_gz(path, {}) or {}
    links = margin_links(client.get(MARGIN_INDEX).text, MARGIN_INDEX)
    new = [(d, u) for d, u in links if d not in store]
    log(f"信用残: 掲載 {len(links)} ファイル、うち新規 {len(new)}")
    for d, u in new:
        try:
            parsed = parse_margin_pdf(client.get(u, timeout=180).content)
        except Exception as e:
            log(f"信用残: {d} を読めません: {e}")
            continue
        if len(parsed) < 100:
            log(f"信用残: {d} の銘柄数が少なすぎるので捨てます ({len(parsed)})")
            continue
        store[d] = parsed
        log(f"信用残: {d} {len(parsed)}銘柄")
    cutoff = (today - dt.timedelta(days=MARGIN_KEEP_DAYS)).isoformat()
    store = thin_margin({k: v for k, v in store.items() if k >= cutoff})
    write_json_gz(path, store)
    return store


def thin_margin(store: dict) -> dict:
    """週の最終日と直近14日だけ残して軽くする。"""
    dates = sorted(store)
    if not dates:
        return store
    recent = (dt.date.fromisoformat(dates[-1]) - dt.timedelta(days=14)).isoformat()
    last_of_week: dict[tuple, str] = {}
    for d in dates:
        last_of_week[dt.date.fromisoformat(d).isocalendar()[:2]] = d
    keep = set(last_of_week.values()) | {d for d in dates if d >= recent}
    return {d: store[d] for d in dates if d in keep}
