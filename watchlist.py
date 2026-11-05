"""
아침 시황 - 목요일 '흐름 체크' (watch.json 을 만든다)

하는 일
  1) 민주당 승리 이후 흐름을 보려고 고른 ETF·주식을 그룹별로 모아, Yahoo Finance(무료)에서 일봉 종가를 가져온다
  2) 종목마다 '최근 5거래일 등락률'과 '선거(11/03 종가) 이후 등락률'을 코드가 계산한다
  3) 200일선(일봉) 대비 몇 % 위/아래인지도 같이 계산한다 (-15% 이하는 따로 모아 알려준다)
  4) 그룹별 평균과 강한 그룹/약한 그룹을 정리한다 (주도주 → 소외주 순환이 일어나는지 보려는 용도)
  5) 목요일(한국시간)에만 실행한다. 손으로 시험할 때는 FORCE_SECTIONS 에 watch 를 적는다.
실패해도 멈추지 않는다 (아침 시황 본문은 그대로 나간다). 숫자는 전부 코드가 계산하고 AI는 쓰지 않는다.
내 보유 종목 표시(⭐보유)는 실행 때만 HOLDINGS 에서 읽어 붙이고, 파일·로그에는 이름을 남기지 않는다.
"""
import datetime as dt
import json
import re
import time

import requests

import extras  # HEADERS·TIMEOUT·HOLDINGS 를 그대로 가져다 쓴다
import ma_signals  # 200일선 계산·구간(광기~공포)을 이동평균선 점검과 똑같이 쓴다

KST = dt.timezone(dt.timedelta(hours=9))

# 흐름 체크를 보내는 요일 (월=0 … 일=6). 목요일(3). 숫자만 고치면 바뀝니다.
RUN_DAYS = (3,)

# 기준일: 미국 중간선거 당일(11/03) 종가. 이 종가 대비 지금까지 얼마나 움직였는지 같이 본다.
ELECTION_DATE = "2026-11-03"
WEEK_BARS = 5  # 최근 5거래일

# (그룹 이름, 이모지, 한 줄 설명, [(심볼, 짧은 이름), ...]) — 종목은 여기서 마음대로 더하고 뺄 수 있습니다.
GROUPS = [
    ("민주당 테마", "🗳", "민주당 정책 기대 (신재생 보조금·규제 완화)",
     [("TAN", "태양광"), ("ICLN", "신재생"), ("PFE", "화이자"), ("MSOS", "대마")]),
    ("헬스케어·방어주", "🏥", "예산·보조금 복원 기대 + 경기 방어",
     [("XLV", "헬스케어"), ("LLY", "일라이릴리"), ("UNH", "유나이티드헬스"), ("KO", "코카콜라"), ("WM", "웨이스트매니지먼트")]),
    ("소비·유통·결제", "🛒", "관세 정책 제동 기대 → 소비 회복 (환급 기대)",
     [("AMZN", "아마존"), ("WMT", "월마트"), ("COST", "코스트코"), ("V", "비자"), ("MA", "마스터카드"),
      ("XLP", "필수소비재"), ("XRT", "소매"), ("XLY", "경기소비재")]),
    ("약달러·대체자산", "💵", "달러 약세·유동성 기대 → 금·원자재 강세",
     [("GDX", "금광"), ("SLV", "은"), ("COPX", "구리광산"), ("DIA", "다우")]),
    ("금리 하락 수혜", "🏗", "금리 하락 기대 → 건설·리츠·배당주",
     [("ITB", "주택건설"), ("LEN", "레나"), ("VNQ", "리츠"), ("O", "리얼티인컴"), ("SCHD", "배당 ETF")]),
    ("항공 (유가 하락)", "✈", "유가·전쟁 우려 완화 → 여행주",
     [("UAL", "유나이티드항공"), ("DAL", "델타항공")]),
    ("산업재 (관세 영향)", "🏭", "관세·경기 민감",
     [("CAT", "캐터필러")]),
]


def fetch_bars(sym):
    last_err = None
    for attempt in range(2):
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                             params={"range": "2y", "interval": "1d"}, headers=extras.HEADERS, timeout=extras.TIMEOUT)
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            off = res.get("meta", {}).get("gmtoffset", 0)
            bars = []
            for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
                if c is not None:
                    bars.append((dt.datetime.fromtimestamp(t + off, dt.timezone.utc).strftime("%Y-%m-%d"), float(c)))
            if len(bars) < WEEK_BARS + 1:
                raise ValueError("데이터 부족")
            return bars
        except Exception as e:
            last_err = e
            time.sleep(1.0)
    raise last_err


def pct(a, b):
    return (a / b - 1) * 100


def analyze(bars):
    last_date, last = bars[-1]
    week = pct(last, bars[-1 - WEEK_BARS][1])
    base = None
    for d, c in bars:  # 기준일 또는 그 이전 마지막 거래일의 종가
        if d <= ELECTION_DATE:
            base = c
    since = pct(last, base) if base else None
    closes = [c for _, c in bars]
    s200 = ma_signals.sma_at(closes, 200, len(closes) - 1)  # 200일 단순이동평균 (Finviz 의 SMA200 과 같은 방식)
    d200 = round(pct(last, s200), 1) if s200 else None
    zone = ma_signals.zone_of(d200)[0] if d200 is not None else ""
    return {"date": last_date, "price": round(last, 2), "w": round(week, 1),
            "e": round(since, 1) if since is not None else None, "d200": d200, "zone": zone}


def avg(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def held_symbols():
    return {h["symbol"].upper() for h in extras.HOLDINGS}


def main():
    now = dt.datetime.now(KST)
    if now.weekday() not in RUN_DAYS and not extras.forced("watch"):
        print("목요일이 아니라서 흐름 체크는 건너뜁니다.")
        import os
        if os.path.exists("watch.json"):
            os.remove("watch.json")
        return
    held = held_symbols()
    groups, failures, no_data = [], [], 0
    for name, emoji, note, members in GROUPS:
        items = []
        for sym, ko in members:
            try:
                time.sleep(0.3)
                row = analyze(fetch_bars(sym))
                row.update({"symbol": sym, "name": ko, "held": sym.upper() in held})
                items.append(row)
            except Exception:
                no_data += 1
        if items:
            groups.append({"name": name, "emoji": emoji, "note": note, "items": items,
                           "avg_w": avg(i["w"] for i in items), "avg_e": avg(i["e"] for i in items)})
    if no_data:
        failures.append({"name": "흐름 체크 일부 종목", "error": f"{no_data}개 종목 가져오기 실패"})
    ranked = sorted((g for g in groups if g["avg_w"] is not None), key=lambda g: g["avg_w"], reverse=True)
    summary = {}
    if len(ranked) >= 2:
        summary["strong"] = [{"name": g["name"], "emoji": g["emoji"], "avg": g["avg_w"]} for g in ranked[:2]]
        summary["weak"] = [{"name": g["name"], "emoji": g["emoji"], "avg": g["avg_w"]} for g in ranked[-2:][::-1]]
    allitems = [i for g in groups for i in g["items"]]
    if allitems:
        top = sorted(allitems, key=lambda i: i["w"], reverse=True)
        summary["top"] = [{"symbol": i["symbol"], "name": i["name"], "w": i["w"]} for i in top[:3]]
        summary["bottom"] = [{"symbol": i["symbol"], "name": i["name"], "w": i["w"]} for i in top[-3:][::-1]]
    summary["cold"] = [{"symbol": i["symbol"], "name": i["name"], "d200": i["d200"]}
                       for i in sorted(allitems, key=lambda i: (i["d200"] is None, i["d200"] or 0))
                       if i["d200"] is not None and i["d200"] <= ma_signals.BUY1]
    result = {"generated_at": now.isoformat(), "as_of": max((i["date"] for i in allitems), default=""),
              "election_date": ELECTION_DATE, "groups": groups, "summary": summary, "failures": failures}
    with open("watch.json", "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print(f"흐름 체크: {len(groups)}개 그룹, {len(allitems)}개 종목 계산, 실패 {no_data}개")


if __name__ == "__main__":
    main()
