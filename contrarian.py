"""
아침 시황 - 월·수·금요일 '역발상 판독기' (contrarian.json 과 gauge.png 를 만든다)

5가지 지표를 1~5점으로 매기고(1=공포 … 5=광기) 평균을 낸다.
  1) 낙관/비관 스프레드 (주간, AAII 설문)
  2) 풋/콜 비율 (일간, CBOE 주식 옵션)
  3) 빚투 = 증권사 신용융자 잔고 전년 대비 증가율 (월간, FINRA)
  4) 하이일드 스프레드 (일간, FRED)
  5) VIX (일간)
점수 구간과 매매 판단은 "소몽의 역발상 판독기" 표를 따른다. 구간 숫자는 아래 상수에서 고칠 수 있다.

- 월·수·금요일(한국시간)에만 실행한다. 손으로 시험할 때는 FORCE_SECTIONS 에 gauge 를 적는다.
- 자동으로 못 가져온 지표는 contrarian_manual.json 에 적어 둔 값을 대신 쓴다(없으면 '미수집').
- 실패해도 멈추지 않는다 (아침 시황 본문은 그대로 나간다). 숫자는 전부 코드가 계산한다(AI 사용 안 함).
"""
import datetime as dt
import io
import json
import os
import re
import subprocess

import requests

import extras  # HEADERS·forced·err_text 를 그대로 가져다 쓴다

KST = dt.timezone(dt.timedelta(hours=9))
TIMEOUT = 25
LABEL = {5: "광기", 4: "과열", 3: "중립", 2: "불안", 1: "공포"}

# 총점(평균) 구간 → 판단. (아래 한도 이상, 이름, 투자 판단)  표의 3.2 → 과열 예시에 맞춰 둔 값입니다.
ZONE_BANDS = [(4.2, "광기", "분할 매도"), (3.2, "과열", "매도 준비"), (2.6, "중립", "매매 보류"),
              (1.8, "불안", "분할 매수"), (1.0, "공포", "역발상 매수(장기)")]
# 판독기를 보내는 요일 (월=0 … 일=6). 월(0)·수(2)·금(4). 숫자만 고치면 바뀝니다.
RUN_DAYS = (0, 2, 4)
MIN_ITEMS = 3          # 이 개수 이상 모였을 때만 총점을 낸다
HY_NEAR_BOTTOM = 0.5   # 하이일드: 1년 최저점 +0.5%p 이내면 '바닥 근처(과열)'로 본다 (표의 '바닥+1%'를 보수적으로 해석)

FINRA_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
YCHARTS_BBS = "https://ycharts.com/indicators/us_investor_sentiment_bull_bear_spread"
YCHARTS_MARGIN = "https://ycharts.com/indicators/finra_margin_debt"
AAII_URL = "https://www.aaii.com/sentimentsurvey/sent_results"
CBOE_URL = "https://www.cboe.com/us/options/market_statistics/daily/?dt={d}"
MANUAL_FILE = "contrarian_manual.json"


# ---------------------------------------------------------------- 점수표 (사진의 표)

def score_bbs(x):       # 낙관-비관 (%p)
    return 5 if x >= 30 else 4 if x >= 20 else 3 if x > -20 else 2 if x > -30 else 1


def score_pcr(x):       # 풋/콜 비율
    return 5 if x <= 0.4 else 4 if x <= 0.5 else 3 if x < 1.0 else 2 if x < 1.2 else 1


def score_margin(x):    # 신용융자 전년 대비 증가율 (%)
    return 5 if x >= 40 else 4 if x >= 20 else 3 if x > -20 else 2 if x > -30 else 1


def score_vix(x):
    return 5 if x <= 15 else 4 if x <= 20 else 3 if x <= 30 else 2 if x <= 40 else 1


def score_hy(v, peak, bottom):
    """하이일드 스프레드(%). peak=최근 120거래일 최고, bottom=최근 1년 최저. (점수, 메모)"""
    if v <= 3.0:
        return 5, "3% 이하 대호황"
    if v > 5.0:
        if peak is not None and peak > 5.0 and peak - v >= 1.0:
            return 1, f"5% 넘게 올랐다가 고점({peak:.2f})에서 1%p 이상 내려옴 → 적극 매수 구간"
        note = "7% 넘음: 2차 경계(막판 추가 급락 가능, 모니터링)" if v >= 7 else "5% 넘음: 1차 경계(조심)"
        return 2, note
    if peak is not None and peak > 5.0:
        return 2, "5% 위에서 내려오는 중 → 가짜위기·조정에서 매수 구간"
    if bottom is not None and v <= bottom + HY_NEAR_BOTTOM:
        return 4, "1년 최저점 근처(경계심이 사라진 상태)"
    return 3, "3~5% 박스권"


def zone_of(total):
    for lim, name, action in ZONE_BANDS:
        if total >= lim:
            return name, action
    return ZONE_BANDS[-1][1], ZONE_BANDS[-1][2]


# ---------------------------------------------------------------- 데이터 가져오기

def page_text(url):
    r = requests.get(url, headers=extras.HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", r.text)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    import html as _h
    return re.sub(r"\s+", " ", _h.unescape(t))


def fred_obs(series, days=800):
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FRED_API_KEY 없음")
    start = (dt.datetime.now(KST) - dt.timedelta(days=days)).strftime("%Y-%m-%d")
    r = requests.get("https://api.stlouisfed.org/fred/series/observations",
                     params={"series_id": series, "api_key": key, "file_type": "json",
                             "observation_start": start, "sort_order": "asc"}, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for o in r.json().get("observations", []):
        try:
            out.append((o["date"], float(o["value"])))
        except (ValueError, KeyError):
            continue
    if not out:
        raise ValueError("데이터 없음")
    return out


def yahoo_last(sym):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"range": "10d", "interval": "1d"}, headers=extras.HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    off = res.get("meta", {}).get("gmtoffset", 0)
    pts = [(dt.datetime.fromtimestamp(t + off, dt.timezone.utc).strftime("%Y-%m-%d"), float(c))
           for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
    if not pts:
        raise ValueError("종가 없음")
    return pts[-1]


def get_vix():
    try:
        d, v = yahoo_last("%5EVIX")
    except Exception:
        d, v = fred_obs("VIXCLS", 30)[-1]
    return {"value": v, "date": d}


def get_hy():
    obs = fred_obs("BAMLH0A0HYM2", 800)
    vals = [v for _, v in obs]
    d, v = obs[-1]
    return {"value": v, "date": d, "peak": max(vals[-120:]), "bottom": min(vals[-252:]),
            "change5": v - vals[-6] if len(vals) > 5 else None}


def get_bull_bear():
    errs = []
    try:  # 1) AAII 홈페이지 표: 날짜 | 낙관 | 중립 | 비관
        t = page_text(AAII_URL)
        for m in re.finditer(r"([A-Z][a-z]+ \d{1,2}(?:, \d{4})?)\s+(\d{1,2}\.\d)%?\s+(\d{1,2}\.\d)%?\s+(\d{1,2}\.\d)%?", t):
            bull, neu, bear = float(m.group(2)), float(m.group(3)), float(m.group(4))
            if abs(bull + neu + bear - 100) <= 1.0:
                return {"value": round(bull - bear, 1), "date": norm_date(m.group(1)), "bull": bull, "bear": bear}
        errs.append("AAII 표를 못 찾음")
    except Exception as e:
        errs.append("AAII " + type(e).__name__)
    try:  # 2) YCharts
        t = page_text(YCHARTS_BBS)
        m = re.search(r"Last Value\s*(-?\d+(?:\.\d+)?)\s*%", t)
        d = re.search(r"Latest Period\s*([A-Z][a-z]+ \d{1,2}, \d{4})", t)
        if m and abs(float(m.group(1))) <= 100:
            return {"value": float(m.group(1)), "date": d.group(1) if d else ""}
        errs.append("YCharts 값을 못 찾음")
    except Exception as e:
        errs.append("YCharts " + type(e).__name__)
    raise RuntimeError(", ".join(errs))


def get_put_call():
    """CBOE 일일 통계 페이지의 'EQUITY PUT/CALL RATIO'. 가장 최근 거래일부터 최대 7일 거슬러 올라간다."""
    day = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=14)  # 미국 현지 날짜 근사
    errs = 0
    for _ in range(7):
        ymd = f"{day:%Y-%m-%d}"
        day -= dt.timedelta(days=1)
        if dt.datetime.strptime(ymd, "%Y-%m-%d").weekday() >= 5:
            continue
        try:
            t = page_text(CBOE_URL.format(d=ymd))
        except Exception:
            errs += 1
            continue
        m = re.search(r"EQUITY\s+PUT/CALL\s+RATIO\D{0,60}?(\d+\.\d+)", t, re.I)
        if m and 0.2 <= float(m.group(1)) <= 3.0:
            return {"value": float(m.group(1)), "date": ymd}
    try:  # 못 찾으면 야후 ^CPCE
        d, v = yahoo_last("%5ECPCE")
        if 0.2 <= v <= 3.0:
            return {"value": v, "date": d}
    except Exception:
        pass
    raise RuntimeError("CBOE·야후에서 값을 못 찾음")


def get_margin():
    try:  # 1) FINRA 엑셀 (월간, 단위: 백만 달러)
        import openpyxl
        r = requests.get(FINRA_URL, headers=extras.HEADERS, timeout=40)
        r.raise_for_status()
        ws = openpyxl.load_workbook(io.BytesIO(r.content), data_only=True, read_only=True).worksheets[0]
        rows = list(ws.iter_rows(values_only=True))
        hdr = col = None
        for i, row in enumerate(rows[:20]):
            for j, c in enumerate(row):
                if isinstance(c, str) and "debit balances" in c.lower() and "margin" in c.lower():
                    hdr, col = i, j
                    break
            if hdr is not None:
                break
        vals = [(row[0], row[col]) for row in rows[hdr + 1:] if len(row) > col and isinstance(row[col], (int, float))]
        if len(vals) >= 16:
            last, prev12 = vals[-1], vals[-13]
            yoy = (last[1] / prev12[1] - 1) * 100
            yoy_3m = (vals[-4][1] / vals[-16][1] - 1) * 100
            lab = last[0].strftime("%Y-%m") if hasattr(last[0], "strftime") else str(last[0])
            if -80 < yoy < 300:
                return {"value": round(yoy, 1), "date": lab, "prev3": round(yoy_3m, 1),
                        "level": last[1] / 1_000_000}
    except Exception:
        pass
    t = page_text(YCHARTS_MARGIN)  # 2) YCharts 문장 "...X% from one year ago"
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*%\s+from\s+one\s+year\s+ago", t)
    d = re.search(r"Latest Period\s*([A-Z][a-z]{2,8} \d{4})", t)
    if not m:
        raise RuntimeError("FINRA·YCharts에서 값을 못 찾음")
    return {"value": float(m.group(1)), "date": d.group(1) if d else ""}


def manual_value(key):
    try:
        with open(MANUAL_FILE, encoding="utf-8") as fp:
            m = json.load(fp).get(key)
    except (OSError, ValueError):
        return None
    if isinstance(m, dict) and isinstance(m.get("value"), (int, float)):
        return {"value": float(m["value"]), "date": str(m.get("date", "")), "manual": True}
    return None


# ---------------------------------------------------------------- 점수 계산

def norm_date(text, year=None):
    """'October 28' / 'Oct 29, 2026' -> '2026-10-28' (못 읽으면 원문 그대로)."""
    year = year or dt.datetime.now(KST).year
    t = re.sub(r"\s+", " ", (text or "").strip())
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return dt.datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    for fmt in ("%B %d", "%b %d"):
        try:
            d = dt.datetime.strptime(t, fmt).replace(year=year)
            if d.date() > dt.datetime.now(KST).date() + dt.timedelta(days=2):
                d = d.replace(year=year - 1)  # 연초에 지난해 12월 자료를 읽는 경우
            return d.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return text or ""


def build_items(failures):
    specs = [
        ("bbs", "낙관/비관 스프레드", "주간", get_bull_bear, "bull_bear"),
        ("pcr", "풋/콜 비율", "일간", get_put_call, "put_call"),
        ("margin", "빚투(신용융자 증가율)", "월간", get_margin, "margin_yoy"),
        ("hy", "하이일드 스프레드", "일간", get_hy, "hy"),
        ("vix", "VIX", "일간", get_vix, "vix"),
    ]
    items = []
    for key, name, cycle, fn, mkey in specs:
        it = {"key": key, "name": name, "cycle": cycle, "value": None, "value_text": "미수집",
              "date": "", "score": None, "label": None, "extra": "", "manual": False}
        try:
            data = fn()
        except Exception as e:
            data = manual_value(mkey)
            if data is None:
                failures.append({"name": f"판독기 {name}", "error": extras.err_text(e)})
                items.append(it)
                continue
            data["manual_reason"] = True
        v = data["value"]
        it.update({"value": v, "date": data.get("date", ""), "manual": bool(data.get("manual"))})
        if key == "bbs":
            it["score"], it["value_text"] = score_bbs(v), f"{v:+.1f}%p"
            if data.get("bull") is not None:
                it["extra"] = f"낙관 {data['bull']:.1f}% − 비관 {data['bear']:.1f}%" + (
                    "  ※ 비관론이 50% 이상이면 저점인 경우가 많았음" if data["bear"] >= 50 else "")
        elif key == "pcr":
            it["score"], it["value_text"] = score_pcr(v), f"{v:.2f}"
        elif key == "margin":
            it["score"], it["value_text"] = score_margin(v), f"{v:+.1f}%"
            if data.get("prev3") is not None:
                way = "늘어나는 중" if v > data["prev3"] + 1 else ("줄어드는 중" if v < data["prev3"] - 1 else "비슷")
                it["extra"] = f"3개월 전 증가율 {data['prev3']:+.1f}% → 지금 {way}"
        elif key == "hy":
            it["score"], it["extra"] = score_hy(v, data.get("peak"), data.get("bottom"))
            it["value_text"] = f"{v:.2f}%"
        elif key == "vix":
            it["score"], it["value_text"] = score_vix(v), f"{v:.1f}"
        it["label"] = LABEL[it["score"]]
        items.append(it)
    return items


# ---------------------------------------------------------------- 그림

FONT_CANDIDATES = ("NanumGothic", "Nanum Gothic", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic", "AppleGothic")
COLORS = {"공포": "#1a9850", "불안": "#7bbf4a", "중립": "#f2c200", "과열": "#f08a24", "광기": "#d73027"}
TEXT_COLORS = {"공포": "#1a9850", "불안": "#5a9a2c", "중립": "#333333", "과열": "#e07000", "광기": "#d73027"}


def find_korean_font():
    from matplotlib import font_manager as fm
    def look():
        names = {f.name for f in fm.fontManager.ttflist}
        return next((c for c in FONT_CANDIDATES if c in names), None)
    found = look()
    if not found:  # 서버에 한글 글꼴이 없으면 설치 시도 (GitHub 서버에서만 동작)
        try:
            subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", "fonts-nanum"],
                           check=True, timeout=240, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            fm._load_fontmanager(try_read_cache=False)
            found = look()
        except Exception:
            found = None
    if not found:
        raise RuntimeError("한글 글꼴 없음")
    return found


def draw_gauge(res, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Wedge, Circle
    import math
    plt.rcParams["font.family"] = find_korean_font()
    plt.rcParams["axes.unicode_minus"] = False
    fig = plt.figure(figsize=(10.4, 5.6), dpi=110, facecolor="#f7f5ee")
    fig.text(0.03, 0.93, "역발상 판독기", fontsize=24, fontweight="bold", va="center")
    fig.text(0.31, 0.93, f"{res['as_of'][5:7]}/{res['as_of'][8:10]} 기준  ·  1점=공포(매수 기회) … 5점=광기(조심)",
             fontsize=11.5, va="center", color="#555")
    # 왼쪽: 지표 표
    ax = fig.add_axes([0.02, 0.10, 0.52, 0.75])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    cols = [0.0, 0.40, 0.62, 0.78, 0.92]
    for x, h in zip(cols, ("지표", "현재값", "점수", "판단", "주기")):
        ax.text(x, 0.94, h, fontsize=11, fontweight="bold", color="white", va="center",
                bbox=dict(boxstyle="square,pad=0.35", fc="#2f5fd0", ec="none"))
    for i, it in enumerate(res["items"]):
        y = 0.80 - i * 0.145
        ax.plot([0, 1], [y - 0.07, y - 0.07], color="#cfcfcf", lw=0.8)
        ax.text(cols[0], y, it["name"], fontsize=11.5, va="center")
        ax.text(cols[1], y, it["value_text"] + ("*" if it.get("manual") else ""), fontsize=11.5, va="center")
        sc = it["score"]
        ax.text(cols[2] + 0.02, y, "-" if sc is None else str(sc), fontsize=13, fontweight="bold", va="center")
        lab = it["label"] or "-"
        ax.text(cols[3], y, lab, fontsize=12, fontweight="bold", va="center", color=TEXT_COLORS.get(lab, "#777"))
        ax.text(cols[4], y, it["cycle"], fontsize=10, va="center", color="#666")
    y = 0.80 - 5 * 0.145
    ax.text(cols[0], y, "종합 (평균)", fontsize=13, fontweight="bold", va="center")
    total = res["total"]
    zone = res["zone"] or "-"
    ax.text(cols[2] + 0.02, y, "-" if total is None else f"{total:.1f}", fontsize=14, fontweight="bold",
            va="center", color=TEXT_COLORS.get(zone, "#777"))
    ax.text(cols[3], y, zone, fontsize=13, fontweight="bold", va="center", color=TEXT_COLORS.get(zone, "#777"))
    if any(it.get("manual") for it in res["items"]):
        ax.text(0, 0.0, "* 수동 입력값", fontsize=9, color="#888")
    # 오른쪽: 반원 게이지
    g = fig.add_axes([0.52, 0.06, 0.47, 0.80])
    g.set_xlim(-1.3, 1.3)
    g.set_ylim(-0.45, 1.3)
    g.set_aspect("equal")
    g.axis("off")

    def ang(s):
        return 180 * (1 - (min(max(s, 1), 5) - 1) / 4)

    edges = [1.0, 1.8, 2.6, 3.2, 4.2, 5.0]
    names = ["공포", "불안", "중립", "과열", "광기"]
    for lo, hi, nm in zip(edges[:-1], edges[1:], names):
        g.add_patch(Wedge((0, 0), 1.0, ang(hi), ang(lo), width=0.34, fc=COLORS[nm], ec="white", lw=2))
        mid = math.radians((ang(lo) + ang(hi)) / 2)
        g.text(1.14 * math.cos(mid), 1.14 * math.sin(mid), nm, fontsize=11.5, fontweight="bold",
               ha="center", va="center", color=TEXT_COLORS[nm])
    if total is not None:
        a = math.radians(ang(total))
        g.plot([0, 0.82 * math.cos(a)], [0, 0.82 * math.sin(a)], color="#111", lw=5, solid_capstyle="round")
        g.add_patch(Circle((0, 0), 0.07, fc="#111"))
        g.text(0, -0.2, f"{total:.1f}", fontsize=34, fontweight="bold", ha="center", va="center")
        g.text(0, -0.40, f"{zone} → {res['action']}", fontsize=13, fontweight="bold", ha="center",
               va="center", color=TEXT_COLORS.get(zone, "#333"))
    else:
        g.text(0, -0.2, "집계 불가", fontsize=22, ha="center", va="center", color="#888")
    fig.text(0.03, 0.03, f"{res['count']}/5개 지표 반영 · 참고용이며 투자 판단은 본인이 합니다", fontsize=9, color="#777")
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


# ---------------------------------------------------------------- 실행

def compute(now=None):
    now = now or dt.datetime.now(KST)
    failures = []
    items = build_items(failures)
    scores = [it["score"] for it in items if it["score"] is not None]
    total = round(sum(scores) / len(scores), 1) if len(scores) >= MIN_ITEMS else None
    zone, action = zone_of(total) if total is not None else (None, None)
    return {"generated_at": now.isoformat(), "as_of": f"{now:%Y-%m-%d}", "items": items, "count": len(scores),
            "total": total, "zone": zone, "action": action, "failures": failures, "image": None}


def main():
    now = dt.datetime.now(KST)
    if now.weekday() not in RUN_DAYS and not extras.forced("gauge"):
        print("월·수·금요일이 아니라서 역발상 판독기는 건너뜁니다.")
        for f in ("contrarian.json", "gauge.png"):
            if os.path.exists(f):
                os.remove(f)
        return
    res = compute(now)
    try:
        draw_gauge(res, "gauge.png")
        res["image"] = "gauge.png"
    except Exception as e:
        res["failures"].append({"name": "판독기 그림", "error": extras.err_text(e)})
    with open("contrarian.json", "w", encoding="utf-8") as fp:
        json.dump(res, fp, ensure_ascii=False, indent=2)
    print(f"역발상 판독기: {res['count']}/5개 반영, 총점 {res['total']}, 판단 {res['zone']}, 그림 {'성공' if res['image'] else '실패'}")
    for f in res["failures"]:
        print(f"  가져오지 못함: {f['name']} - {f['error']}")


if __name__ == "__main__":
    main()
