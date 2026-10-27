"""
아침 시황 - 토요일 이동평균선(50일선·200일선) 점검 (ma.json 을 만든다)

하는 일
  1) 지수 3개(S&P500·나스닥·다우)와 내 보유 종목의 일봉 종가를 Yahoo Finance 에서 가져온다 (무료)
  2) 50일선·200일선 대비 몇 % 위/아래인지, 200일선이 오르는 중인지 내리는 중인지를 코드가 계산한다
  3) 200일선보다 20% 이상 아래면 "적극 매수 검토" 로 따로 모아 알려준다
  4) 지수는 200주선(침체·위기 때 최후 방어선)도 같이 본다
  5) 토요일(한국시간)에만 실행한다. 손으로 시험할 때는 FORCE_SECTIONS 에 ma 를 적는다.
실패해도 멈추지 않는다 (아침 시황 본문은 그대로 나간다).
숫자는 전부 코드가 계산하고 AI는 쓰지 않는다. 보유 종목 이름은 로그에 찍지 않는다.
"""
import datetime as dt
import json
import os

import requests

import extras  # HOLDINGS(내 종목)·HEADERS 를 그대로 가져다 쓴다

KST = dt.timezone(dt.timedelta(hours=9))

INDICES = [("S&P500", "^GSPC"), ("나스닥", "^IXIC"), ("다우", "^DJI")]

# ---- 과냉/과열 기준 (% 는 이동평균선에서 얼마나 떨어져 있는지). 숫자만 고치면 됩니다. ----
# 지수와 개별 종목은 흔들리는 폭이 달라서 기준을 따로 둔다. 과열 기준은 참고용 가정값입니다.
THRESH = {
    "index": {"c50": -5, "h50": 5, "c200": -10, "h200": 15},
    "stock": {"c50": -10, "h50": 15, "c200": -10, "h200": 40},
}
DEEP_200 = -20      # 200일선에서 이만큼 이상 내려가면 "적극 매수 검토"
SLOPE_DAYS = 20     # 200일선이 20거래일 전보다 올랐는지 내렸는지
SLOPE_FLAT = 0.2    # 변화가 ±0.2% 안이면 횡보
CROSS_DAYS = 10     # 최근 10거래일 안에 200일선을 넘나들었는지


def yahoo_symbol(sym):
    s = sym.upper()
    return "BTC-USD" if s == "BTC" else sym


def fetch_closes(sym, rng="2y", interval="1d"):
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"range": rng, "interval": interval}, headers=extras.HEADERS, timeout=extras.TIMEOUT)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    off = res.get("meta", {}).get("gmtoffset", 0)
    bars = []
    for t, c in zip(res["timestamp"], res["indicators"]["quote"][0]["close"]):
        if c is not None:
            bars.append((dt.datetime.fromtimestamp(t + off, dt.timezone.utc).strftime("%Y-%m-%d"), float(c)))
    if not bars:
        raise ValueError("종가 없음")
    return bars


def sma_at(closes, n, end):
    """closes[end-n+1 .. end] 의 평균. 데이터가 모자라면 None."""
    if end - n + 1 < 0:
        return None
    seg = closes[end - n + 1:end + 1]
    return sum(seg) / n


def pct(a, b):
    return (a / b - 1) * 100


def analyze(bars, kind):
    closes = [c for _, c in bars]
    last = len(closes) - 1
    price = closes[last]
    s50, s200 = sma_at(closes, 50, last), sma_at(closes, 200, last)
    row = {"date": bars[last][0], "price": price, "days": len(closes),
           "d50": round(pct(price, s50), 1) if s50 else None,
           "d200": round(pct(price, s200), 1) if s200 else None,
           "trend": None, "cross": None, "zone": None, "deep": False}
    if s200:
        prev = sma_at(closes, 200, last - SLOPE_DAYS)
        slope = pct(s200, prev) if prev else None
        row["slope"] = "up" if slope is not None and slope >= SLOPE_FLAT else (
            "down" if slope is not None and slope <= -SLOPE_FLAT else "flat")
        above = price >= s200
        row["above200"] = above
        row["trend"] = {(True, "up"): "상승 추세", (True, "flat"): "위쪽이지만 방향 횡보", (True, "down"): "위쪽이지만 방향 꺾임",
                        (False, "up"): "아래쪽이지만 방향 상승 전환", (False, "flat"): "아래쪽·방향 횡보",
                        (False, "down"): "하락 추세"}[(above, row["slope"])]
        # 최근 200일선을 넘나들었는지
        rel = []
        for i in range(max(0, last - CROSS_DAYS), last + 1):
            m = sma_at(closes, 200, i)
            if m:
                rel.append(closes[i] >= m)
        if above and rel and not all(rel):
            row["cross"] = "up"
        elif not above and rel and any(rel):
            row["cross"] = "down"
    # 과냉/과열 판정
    t = THRESH[kind]
    tags = []
    if row["d200"] is not None and row["d200"] <= DEEP_200:
        row["deep"] = True
        tags.append("🔵 200일선 -20% 이하 — 적극 매수 검토")
    elif row["d200"] is not None and row["d200"] <= t["c200"]:
        tags.append("🔵 200일선 대비 과냉")
    elif row["d200"] is not None and row["d200"] >= t["h200"]:
        tags.append("🔴 200일선 대비 과열")
    if row["d50"] is not None and not row["deep"]:
        if row["d50"] <= t["c50"]:
            tags.append("🔵 50일선 대비 과냉")
        elif row["d50"] >= t["h50"]:
            tags.append("🔴 50일선 대비 과열")
    row["zone"] = " · ".join(tags) if tags else "정상 범위"
    return row


def weekly200(sym):
    """지수의 200주선(침체·위기 최후 방어선) 대비 위치."""
    bars = fetch_closes(sym, rng="6y", interval="1wk")
    closes = [c for _, c in bars]
    last = len(closes) - 1
    m = sma_at(closes, 200, last)
    if not m:
        return None
    return {"value": round(m, 1), "d": round(pct(closes[last], m), 1)}


def main():
    now = dt.datetime.now(KST)
    if now.weekday() != 5 and not extras.forced("ma"):
        print("토요일이 아니라서 이동평균선 점검은 건너뜁니다.")
        if os.path.exists("ma.json"):
            os.remove("ma.json")
        return
    items = [{"name": n, "symbol": s, "kind": "index"} for n, s in INDICES]
    items += [{"name": h["ko"], "symbol": yahoo_symbol(h["symbol"]), "kind": "stock"} for h in extras.HOLDINGS]
    rows, failures, no_data = [], [], 0
    for it in items:
        try:
            extras.time.sleep(0.3)
            row = analyze(fetch_closes(it["symbol"]), it["kind"])
            row.update({"name": it["name"], "symbol": it["symbol"], "kind": it["kind"]})
            if it["kind"] == "index":
                try:
                    row["w200"] = weekly200(it["symbol"])
                except Exception as e:
                    failures.append({"name": f"200주선 {it['name']}", "error": extras.err_text(e)})
            rows.append(row)
        except Exception:
            no_data += 1  # 종목 이름은 기록하지 않는다(공개 저장소 로그)
    if no_data:
        failures.append({"name": "이동평균선 계산 실패", "error": f"{no_data}개 종목"})
    result = {"generated_at": now.isoformat(), "as_of": max((r["date"] for r in rows), default=""),
              "rows": rows, "failures": failures}
    with open("ma.json", "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    deep = sum(1 for r in rows if r["deep"])
    print(f"이동평균선 점검: {len(rows)}개 계산, 200일선 -20% 이하 {deep}개, 실패 {no_data}개")


if __name__ == "__main__":
    main()
