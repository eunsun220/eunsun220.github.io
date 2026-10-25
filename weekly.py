"""
아침 시황 - 일요일 주간 특징주 요약 (weekly.json 을 만든다)

하는 일
  1) 분류별 대표 종목의 최근 5거래일 등락률을 Yahoo Finance 에서 가져온다 (숫자는 코드가 계산)
  2) 이번 주 기사 제목을 모아 Gemini 에게 "분류별 한 줄 배경"만 쓰게 한다 (제목에 없는 내용은 쓰지 않게 함)
  3) 일요일(한국시간)에만 실행한다. 손으로 시험할 때는 FORCE_SECTIONS 에 weekly 를 적는다.
실패해도 멈추지 않는다 (아침 시황 본문은 그대로 나간다).
"""
import datetime as dt
import json
import os
import sys

import requests

import extras  # 뉴스 수집·Gemini 호출 도구를 그대로 가져다 쓴다

KST = dt.timezone(dt.timedelta(hours=9))
HEADERS = {"User-Agent": "Mozilla/5.0 (personal morning brief project)"}

# 분류 -> [(소분류, [심볼...])]  (이름만 고치면 됩니다)
GROUPS = [
    ("시장", [("지수·원유", ["SPY", "QQQ", "SOXX", "USO"]),
              ("빅테크", ["NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META", "TSLA"])]),
    ("AI 인프라", [("메모리", ["WDC", "MU", "SNDK"]), ("CPU", ["AMD", "INTC", "ARM"]),
                 ("장비", ["ASML", "AMAT", "LRCX"]), ("전력", ["BE", "CEG", "VST"]),
                 ("네오클라우드", ["NBIS", "IREN", "CRWV"]), ("서버", ["SMCI", "HPE", "DELL"])]),
    ("사이버 보안", [("보안", ["CRWD", "PANW", "NET", "DDOG", "FSLY", "ZS", "FTNT"])]),
    ("지정학", [("에너지", ["CVX", "COP", "XOM"]), ("방산", ["LMT", "NOC", "LHX"])]),
    ("기타", [("통신리츠", ["CCI", "AMT"]), ("통신", ["T", "TMUS"]), ("금", ["GLD"])]),
]
# 분류별 이번 주 배경을 찾을 검색어
THEME_QUERY = {
    "시장": "뉴욕증시 한 주 마감 주간",
    "AI 인프라": "AI 반도체 데이터센터 주가 급락 OR 급등",
    "사이버 보안": "사이버 보안주 급등 OR 해킹 취약점",
    "지정학": "이란 중동 방산주 에너지주 유가",
    "기타": "통신주 OR 통신리츠 주가",
}


def weekly_change(symbol):
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": "1mo", "interval": "1d"}, headers=HEADERS, timeout=20,
    )
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    pts = [(t, c) for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]) if c is not None]
    if len(pts) < 6:
        raise RuntimeError("기록이 부족함")
    last, base = pts[-1], pts[-6]  # 최근 종가 vs 5거래일 전 종가
    off = res.get("meta", {}).get("gmtoffset", 0)
    day = (dt.datetime.fromtimestamp(last[0], dt.timezone.utc) + dt.timedelta(seconds=off)).strftime("%Y-%m-%d")
    return (last[1] / base[1] - 1) * 100, day


def theme_lines(now, failures):
    """분류별 한 줄 배경. 기사 제목에 있는 내용만 쓰게 하고, 실패하면 비워 둔다."""
    heads = {}
    for cat, q in THEME_QUERY.items():
        try:
            url = extras.gnews(q, "ko", days=7)
            items = extras.parse_feed("구글뉴스 " + cat, url, now, 24 * 7)[:8]
            heads[cat] = [i["title"] for i in items]
        except Exception as e:
            failures.append({"name": f"주간 기사({cat})", "error": extras.err_text(e)})
    if not any(heads.values()):
        return {}
    text = "\n".join(f"[{c}]\n" + "\n".join("- " + t for t in ts) for c, ts in heads.items() if ts)
    prompt = (
        "아래는 이번 주 분류별 뉴스 제목이다. 분류마다 이번 주 주가 흐름의 배경을 한국어 한 줄(40자 이내)로 쓴다.\n"
        "규칙: 제목에 있는 내용만 쓴다. 원인을 단정하지 말고, 근거가 부족하면 빈 문자열(\"\")로 둔다. 투자 권유 금지.\n"
        'JSON만 출력한다. 형식: {"시장": "...", "AI 인프라": "...", "사이버 보안": "...", "지정학": "...", "기타": "..."}\n\n' + text
    )
    try:
        res = extras.gemini_json(prompt)
        return {k: str(v).strip()[:60] for k, v in res.items() if k in THEME_QUERY and str(v).strip()}
    except Exception as e:
        failures.append({"name": "주간 배경 요약(AI)", "error": extras.err_text(e)})
        return {}


def main():
    now = dt.datetime.now(KST)
    if now.weekday() != 6 and not extras.forced("weekly"):
        print("일요일이 아니라서 주간 특징주는 건너뜁니다.")
        if os.path.exists("weekly.json"):
            os.remove("weekly.json")  # 지난 파일이 남아 있으면 지운다
        return
    failures, groups, last_day = [], [], None
    for cat, subs in GROUPS:
        out_subs = []
        for label, syms in subs:
            rows = []
            for sym in syms:
                try:
                    chg, day = weekly_change(sym)
                    rows.append({"symbol": sym, "change": round(chg, 1)})
                    last_day = max(last_day or day, day)
                except Exception as e:
                    failures.append({"name": f"주간 {sym}", "error": extras.err_text(e)})
            if rows:
                out_subs.append({"label": label, "rows": rows})
        if out_subs:
            groups.append({"category": cat, "subs": out_subs})
    themes = theme_lines(now, failures) if groups else {}
    result = {"generated_at": now.isoformat(), "as_of": last_day, "groups": groups, "themes": themes, "failures": failures}
    with open("weekly.json", "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print(f"주간 특징주: 분류 {len(groups)}개, 배경 {len(themes)}줄, 실패 {len(failures)}건")
    for f in failures:
        print(f"  가져오지 못함: {f['name']} - {f['error']}")


if __name__ == "__main__":
    main()
