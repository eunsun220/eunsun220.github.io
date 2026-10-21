"""
아침 시황 - 3단계: 뉴스 · 주식 공포탐욕 · 일정 모으기

하는 일
  1) 무료 뉴스 목록(RSS)에서 밤사이 기사 제목과 링크를 모은다
  2) Gemini(무료)에게 "시장에 중요한 기사"만 고르고 분야를 나누게 한다
     - 링크는 AI가 쓰지 않는다. 번호로 원래 기사 주소를 찾아 붙인다
  3) CNN 주식 공포탐욕지수를 가져온다
  4) 미국 경제지표 일정(오늘·이번 주)과 주요 기업 실적 일정을 가져온다
  5) extras.json 으로 저장한다. 일부가 실패해도 멈추지 않는다

필요한 것 (GitHub 금고에 저장, 이 파일에 직접 쓰지 않는다)
  - GEMINI_API_KEY  : 뉴스 선별용 (없으면 AI 없이 제목만 모아 보여준다)
  - FINNHUB_API_KEY : 실적 일정용 (없으면 실적 줄만 빠진다)
"""
import datetime as dt
import email.utils
import json
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests

KST = dt.timezone(dt.timedelta(hours=9))
TIMEOUT = 20
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {"User-Agent": BROWSER_UA}

NEWS_HOURS = 20          # 이 시간 안에 나온 기사만
PER_FEED = 15            # 목록마다 최대 몇 개까지 후보로 쓸지
MAX_CANDIDATES = 110     # AI에게 보여줄 최대 기사 수
MAX_PICK = 14            # 화면에 보여줄 최대 기사 수
CATEGORIES = ["월가", "테크", "지정학", "정치", "코인"]
LEVEL_ORDER = {"high": 0, "mid": 1, "low": 2}


def gnews(query):
    return f"https://news.google.com/rss/search?q={quote(query + ' when:1d')}&hl=ko&gl=KR&ceid=KR:ko"


# 열리지 않는 주소가 있어도 나머지로 계속 진행한다. 실행 결과를 보고 고쳐 나간다.
FEEDS = [
    ("연합뉴스 국제", "https://www.yna.co.kr/rss/international.xml"),
    ("연합뉴스 경제", "https://www.yna.co.kr/rss/economy.xml"),
    ("한국경제 국제", "https://www.hankyung.com/feed/international"),
    ("한국경제 증권", "https://www.hankyung.com/feed/finance"),
    ("매일경제 증권", "https://www.mk.co.kr/rss/50200011/"),
    ("구글뉴스 미국증시", gnews("미국 증시 뉴욕증시 마감")),
    ("구글뉴스 연준·금리", gnews("연준 금리 美 국채금리")),
    ("구글뉴스 반도체·AI", gnews("엔비디아 반도체 AI 빅테크")),
    ("구글뉴스 비트코인", gnews("비트코인 암호화폐 ETF")),
    ("구글뉴스 지정학", gnews("美 이란 중동 우크라이나 중국 관세")),
    ("구글뉴스 트럼프", gnews("트럼프 관세 정책 행정부")),
]

# 실적 발표를 보여줄 주요 기업 (심볼: 한글 이름)
WATCH = {
    "AAPL": "애플", "MSFT": "마이크로소프트", "GOOGL": "알파벳", "GOOG": "알파벳", "AMZN": "아마존",
    "META": "메타", "NVDA": "엔비디아", "TSLA": "테슬라", "AVGO": "브로드컴", "AMD": "AMD",
    "INTC": "인텔", "MU": "마이크론", "QCOM": "퀄컴", "TSM": "TSMC", "ASML": "ASML",
    "NFLX": "넷플릭스", "ORCL": "오라클", "CRM": "세일즈포스", "ADBE": "어도비", "PLTR": "팔란티어",
    "COIN": "코인베이스", "MSTR": "스트래티지", "HOOD": "로빈후드", "JPM": "JP모건", "GS": "골드만삭스",
    "MS": "모건스탠리", "BAC": "뱅크오브아메리카", "C": "씨티그룹", "WFC": "웰스파고", "BLK": "블랙록",
    "V": "비자", "MA": "마스터카드", "UNH": "유나이티드헬스", "JNJ": "존슨앤존슨", "LLY": "일라이릴리",
    "PFE": "화이자", "XOM": "엑슨모빌", "CVX": "셰브론", "WMT": "월마트", "COST": "코스트코",
    "HD": "홈디포", "KO": "코카콜라", "PEP": "펩시코", "MCD": "맥도날드", "NKE": "나이키",
    "DIS": "디즈니", "BA": "보잉", "GE": "GE에어로스페이스", "RTX": "RTX", "NOC": "노스롭그루먼",
    "LMT": "록히드마틴", "CAT": "캐터필러", "IBM": "IBM", "TXN": "텍사스인스트루먼트", "LRCX": "램리서치",
    "AMAT": "어플라이드머티리얼즈", "KLAC": "KLA", "SMCI": "슈퍼마이크로", "ARM": "ARM", "UBER": "우버",
    "ABNB": "에어비앤비", "SHOP": "쇼피파이", "T": "AT&T", "VZ": "버라이즌", "TMUS": "T모바일",
    "VRT": "버티브", "ANET": "아리스타", "CEG": "컨스텔레이션에너지", "VST": "비스트라", "BE": "블룸에너지",
    "GLW": "코닝", "COHR": "코히런트", "CRDO": "크레도", "ALAB": "아스테라랩스",
}
HOUR_LABEL = {"bmo": "장 전", "dmh": "장 중", "amc": "장 마감 후"}
HOUR_ORDER = {"bmo": 0, "dmh": 1, "amc": 2}

# 경제지표 영어 제목 -> 한글 (위에서부터 먼저 맞는 것을 쓴다)
TITLE_KO = [
    ("Core CPI", "근원 소비자물가(CPI)"), ("CPI", "소비자물가(CPI)"),
    ("Core PPI", "근원 생산자물가(PPI)"), ("PPI", "생산자물가(PPI)"),
    ("Core PCE", "근원 PCE 물가"), ("PCE Price", "PCE 물가"),
    ("Non-Farm Employment Change", "비농업 고용(NFP)"), ("Unemployment Rate", "실업률"),
    ("Unemployment Claims", "신규 실업수당 청구"), ("JOLTS", "JOLTS 구인"),
    ("Federal Funds Rate", "FOMC 금리 결정"), ("FOMC Statement", "FOMC 성명"),
    ("FOMC Press Conference", "FOMC 기자회견"), ("FOMC Meeting Minutes", "FOMC 의사록"),
    ("Advance GDP", "GDP(속보)"), ("Prelim GDP", "GDP(잠정)"), ("Final GDP", "GDP(확정)"), ("GDP", "GDP"),
    ("Retail Sales", "소매판매"), ("ISM Manufacturing", "ISM 제조업 PMI"), ("ISM Services", "ISM 서비스업 PMI"),
    ("ADP", "ADP 고용"), ("Consumer Confidence", "소비자신뢰지수"),
    ("Consumer Sentiment", "미시간대 소비자심리"), ("Fed Chair", "연준 의장 발언"),
    ("Powell", "파월 의장 발언"), ("FOMC Member", "FOMC 위원 발언"),
]
WEEKDAYS = "월화수목금토일"


# ---------------------------------------------------------------- 공통

def err_text(e):
    """오류 글에 주소(=키)가 섞일 수 있어 종류와 상태 번호만 남긴다."""
    code = getattr(getattr(e, "response", None), "status_code", None)
    return type(e).__name__ + (f" (HTTP {code})" if code else "")


def norm(title):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", title)[:18]


# ---------------------------------------------------------------- 뉴스

def parse_feed(name, url, now):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        source = name
        if " - " in title and name.startswith("구글뉴스"):
            title, source = title.rsplit(" - ", 1)
        ts = None
        pub = it.findtext("pubDate")
        if pub:
            try:
                ts = email.utils.parsedate_to_datetime(pub)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                ts = None
        if ts and (now - ts) > dt.timedelta(hours=NEWS_HOURS):
            continue
        out.append({"title": title, "url": link, "source": source.strip(), "ts": ts})
    out.sort(key=lambda x: x["ts"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
    return out[:PER_FEED]


def collect_news(now, failures):
    seen, cands = set(), []
    per_feed = []
    for name, url in FEEDS:
        try:
            per_feed.append(parse_feed(name, url, now))
        except Exception as e:  # 한 곳이 막혀도 계속
            failures.append({"name": f"뉴스: {name}", "error": err_text(e)})
    # 목록들을 번갈아 가며 담아 한쪽으로 쏠리지 않게 한다
    for i in range(PER_FEED):
        for feed in per_feed:
            if i < len(feed):
                k = norm(feed[i]["title"])
                if k and k not in seen:
                    seen.add(k)
                    cands.append(feed[i])
    return cands[:MAX_CANDIDATES]


def gemini_json(prompt):
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY 없음")
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-latest").strip()
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"},
        },
        timeout=90,
    )
    r.raise_for_status()
    parts = r.json()["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text).strip()
    return json.loads(text)


def market_text(data_path="data.json"):
    try:
        with open(data_path, encoding="utf-8") as fp:
            rows = json.load(fp)["rows"]
    except (OSError, ValueError, KeyError):
        return "(없음)"
    lines = []
    for r in rows:
        if r.get("change") is None:
            continue
        sign = "+" if r["change"] >= 0 else ""
        unit = "%" if r["change_kind"] == "pct" else "p"
        lines.append(f"{r['name']} {r['value']:,.2f} ({sign}{r['change']:.2f}{unit})")
    return "\n".join(lines) or "(없음)"


def build_prompt(cands, mkt):
    lines = "\n".join(f"{i}|{c['source']}|{c['title']}" for i, c in enumerate(cands, 1))
    return f"""너는 미국 주식과 비트코인·암호화폐에 투자하는 사람을 위한 아침 뉴스 편집자다.
아래는 밤사이 모은 뉴스 제목 목록이다. 각 줄 형식은 "번호|출처|제목"이다.

규칙
1. 미국 증시나 비트코인·암호화폐 시장에 영향을 줄 만한 기사만 최대 {MAX_PICK}개 고른다.
   연예, 스포츠, 사건사고, 국내 정치 가십 등 관련 없는 기사는 버린다.
2. 같은 사건을 다룬 기사는 하나만 고른다.
3. category는 "월가", "테크", "지정학", "정치", "코인" 중 하나다.
4. title은 원래 제목의 뜻을 그대로 두고 40자 이내로 줄인다. 제목에 없는 사실, 숫자, 원인을 절대 덧붙이지 않는다.
5. level은 "high"(시장 방향을 바꿀 수 있어 꼭 확인, 최대 3개), "mid"(참고), "low"(단기 소음일 가능성) 중 하나다.
6. summary는 고른 기사와 아래 시장 숫자만 근거로 밤사이 분위기를 한국어 2문장 이내로 쓴다.
   원인을 단정하지 말고, 근거가 부족하면 "원인은 불명확"이라고 쓴다. 투자를 권유하는 표현은 쓰지 않는다.

JSON만 출력한다. 형식: {{"summary": "...", "items": [{{"id": 번호, "category": "...", "title": "...", "level": "..."}}]}}

[시장 숫자]
{mkt}

[뉴스 목록]
{lines}
"""


def pick_with_ai(cands):
    result = gemini_json(build_prompt(cands, market_text()))
    items = []
    used = set()
    for it in result.get("items", []):
        try:
            idx = int(it["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (1 <= idx <= len(cands)) or idx in used:
            continue
        used.add(idx)
        src = cands[idx - 1]
        cat = it.get("category") if it.get("category") in CATEGORIES else "월가"
        level = it.get("level") if it.get("level") in LEVEL_ORDER else "mid"
        title = str(it.get("title") or src["title"]).strip()[:60]
        items.append({"category": cat, "title": title, "url": src["url"], "source": src["source"], "level": level})
    items.sort(key=lambda x: (LEVEL_ORDER[x["level"]], CATEGORIES.index(x["category"])))
    return {"mode": "ai", "summary": str(result.get("summary") or "").strip(), "items": items[:MAX_PICK]}


def pick_raw(cands):
    """AI를 못 쓸 때: 목록마다 번갈아 가며 제목만 보여준다."""
    items = [
        {"category": "주요 뉴스", "title": c["title"][:60], "url": c["url"], "source": c["source"], "level": "mid"}
        for c in cands[:10]
    ]
    return {"mode": "raw", "summary": "", "items": items}


# ---------------------------------------------------------------- 주식 공포탐욕

def stock_fear_greed():
    r = requests.get(
        "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
        headers={
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
            "Referer": "https://edition.cnn.com/",
            "Origin": "https://edition.cnn.com",
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    fg = r.json()["fear_and_greed"]
    return {
        "score": float(fg["score"]),
        "rating": fg.get("rating", ""),
        "prev_close": float(fg["previous_close"]),
        "week": float(fg["previous_1_week"]),
        "month": float(fg["previous_1_month"]),
        "timestamp": fg.get("timestamp"),
    }


# ---------------------------------------------------------------- 일정

def title_ko(title):
    for key, ko in TITLE_KO:
        if key in title:
            if key == "FOMC Member":
                return f"{ko} ({title.replace('FOMC Member', '').replace('Speaks', '').strip()})"
            return ko
    return title


def econ_events(now):
    r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    today, week = [], []
    for e in r.json():
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        try:
            when = dt.datetime.fromisoformat(e["date"]).astimezone(KST)
        except (KeyError, ValueError):
            continue
        row = {
            "date": f"{when:%m/%d}({WEEKDAYS[when.weekday()]})",
            "time": f"{when:%H:%M}",
            "title": title_ko(e.get("title", "")),
            "impact": e.get("impact"),
            "forecast": e.get("forecast") or "",
            "previous": e.get("previous") or "",
        }
        if now <= when < now + dt.timedelta(hours=24):
            today.append((when, row))
        elif now + dt.timedelta(hours=24) <= when <= now + dt.timedelta(days=7) and e.get("impact") == "High":
            week.append((when, row))
    today.sort(key=lambda x: x[0])
    week.sort(key=lambda x: x[0])
    return [r for _, r in today], [r for _, r in week]


def earnings_today(day):
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FINNHUB_API_KEY 없음")
    r = requests.get(
        "https://finnhub.io/api/v1/calendar/earnings",
        params={"from": day, "to": day, "token": key},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    out, seen = [], set()
    for e in r.json().get("earningsCalendar", []) or []:
        sym = e.get("symbol")
        name = WATCH.get(sym)
        if not name or name in seen:
            continue
        seen.add(name)
        hour = e.get("hour") or ""
        eps = e.get("epsEstimate")
        out.append({
            "symbol": sym, "name": name, "hour": hour,
            "when": HOUR_LABEL.get(hour, "시간 미정"),
            "eps_estimate": eps if isinstance(eps, (int, float)) else None,
        })
    out.sort(key=lambda x: (HOUR_ORDER.get(x["hour"], 3), x["name"]))
    return out


# ---------------------------------------------------------------- 실행

def main():
    now = dt.datetime.now(KST)
    failures = []
    out = {"generated_at": now.isoformat(), "stock_fg": None, "news": None,
           "today_events": [], "week_events": [], "earnings": [], "earnings_day": f"{now:%m/%d}"}

    try:
        out["stock_fg"] = stock_fear_greed()
    except Exception as e:
        failures.append({"name": "주식 공포탐욕지수", "error": err_text(e)})

    cands = collect_news(now, failures)
    if cands:
        try:
            out["news"] = pick_with_ai(cands)
        except Exception as e:
            failures.append({"name": "AI 뉴스 선별", "error": err_text(e)})
            out["news"] = pick_raw(cands)
    else:
        failures.append({"name": "뉴스", "error": "후보 기사가 없음"})

    try:
        out["today_events"], out["week_events"] = econ_events(now)
    except Exception as e:
        failures.append({"name": "경제지표 일정", "error": err_text(e)})

    try:
        out["earnings"] = earnings_today(f"{now:%Y-%m-%d}")
    except Exception as e:
        failures.append({"name": "실적 일정", "error": err_text(e)})

    out["failures"] = failures
    with open("extras.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=2)

    news = out["news"] or {}
    print(f"뉴스 {len(news.get('items', []))}건({news.get('mode', '없음')}) / 오늘 지표 {len(out['today_events'])}건 / "
          f"이번 주 지표 {len(out['week_events'])}건 / 실적 {len(out['earnings'])}건 / 주식 공포탐욕 "
          f"{'성공' if out['stock_fg'] else '실패'}")
    for f in failures:
        print(f"  가져오지 못함: {f['name']} - {f['error']}")
    # 일부 실패는 정상으로 취급한다 (send_telegram.py 가 빠진 부분을 알려준다)


if __name__ == "__main__":
    main()
