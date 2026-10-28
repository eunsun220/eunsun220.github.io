"""
아침 시황 - 수·토요일 이동평균선(50일선·200일선) 점검 (ma.json 을 만든다)

하는 일
  1) 지수 3개(S&P500·나스닥·다우)와 내 보유 종목의 일봉 종가를 Yahoo Finance 에서 가져온다 (무료)
  2) 50일선·200일선 대비 몇 % 위/아래인지, 200일선이 오르는 중인지 내리는 중인지를 코드가 계산한다
  3) 200일선 이격도를 광기/과열/중립/불안/공포로 나누고, -15% 이하(1차 매수)·-20% 이하(적극 검토)는 따로 모아 알려준다
  4) 지수는 200주선(침체·위기 때 최후 방어선)도 같이 본다
  5) 수요일·토요일(한국시간)에만 실행한다. 손으로 시험할 때는 FORCE_SECTIONS 에 ma 를 적는다.
실패해도 멈추지 않는다 (아침 시황 본문은 그대로 나간다).
숫자는 전부 코드가 계산하고 AI는 쓰지 않는다. 보유 종목 이름은 로그에 찍지 않는다.
"""
import datetime as dt
import json
import os

import requests

import extras  # HOLDINGS(내 종목)·HEADERS 를 그대로 가져다 쓴다

KST = dt.timezone(dt.timedelta(hours=9))

# 점검을 보내는 요일 (월=0 … 일=6). 수요일(2)과 토요일(5). 바꾸고 싶으면 숫자만 고치면 됩니다.
RUN_DAYS = (2, 5)

INDICES = [("S&P500", "^GSPC"), ("나스닥", "^IXIC"), ("다우", "^DJI")]

# ---- 200일선 이격도 구간 (% 는 200일선에서 얼마나 위/아래인지). 숫자만 고치면 됩니다. ----
# (아래 한도 이상, 이름) — 위에서부터 처음 맞는 것을 쓴다. 사진 "내 관심종목은 과열? 냉각?" 표 기준.
ZONES = [(30, "🟥", "광기"), (20, "🟧", "과열"), (-5, "⬜", "중립"), (-10, "🟨", "불안"), (-999, "🟦", "공포")]
BUY1 = -15        # 이 아래면 과냉 → 1차 매수 시작 (역발상·장기 관점)
DEEP_200 = -20    # 이 아래면 '적극 매수 검토'로 따로 강조
C50 = -5          # 50일선에서 이만큼 아래면 눌림목 표시 (1년에 3~4번)
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


def zone_of(d):
    for lim, emoji, name in ZONES:
        if d >= lim:
            return emoji, name
    return ZONES[-1][1], ZONES[-1][2]


def action_of(d):
    if d >= 30:
        return "추가 매수 보류 · 현금이 필요하면 일부 매도도 고려"
    if d >= 20:
        return "추가 매수 보류 (과열에서 반락할 때 일부 매도 고려)"
    if d >= 10:
        return "관망 (추가 매수 X)"
    if d >= 0:
        return "매수 가능 구간 (0~10%)"
    if d > -5:
        return "200일선 바로 아래 · 0선 위로 올라올 때까지 대기"
    if d > -10:
        return "불안 · 역발상 투자 고려 (200일선 아래 매수는 장기 관점)"
    if d > BUY1:
        return "공포 · 1차 매수 대기 (-15% 아래부터 시작)"
    return "과냉 · 1차 매수 시작 구간 (역발상·장기 관점)"


def analyze(bars, kind=None):
    closes = [c for _, c in bars]
    last = len(closes) - 1
    price = closes[last]
    s50, s200 = sma_at(closes, 50, last), sma_at(closes, 200, last)
    row = {"date": bars[last][0], "price": price, "days": len(closes),
           "d50": round(pct(price, s50), 1) if s50 else None,
           "d200": round(pct(price, s200), 1) if s200 else None,
           "trend": None, "cross": None, "zone": None, "action": None, "note": None,
           "buy1": False, "deep": False, "dip50": False}
    if row["d50"] is not None and row["d50"] <= C50:
        row["dip50"] = True
    if not s200:
        return row
    prev = sma_at(closes, 200, last - SLOPE_DAYS)
    slope = pct(s200, prev) if prev else None
    row["slope"] = "up" if slope is not None and slope >= SLOPE_FLAT else (
        "down" if slope is not None and slope <= -SLOPE_FLAT else "flat")
    above = price >= s200
    row["above200"] = above
    row["trend"] = {(True, "up"): "상승 추세", (True, "flat"): "위쪽이지만 방향 횡보", (True, "down"): "위쪽이지만 방향 꺾임",
                    (False, "up"): "아래쪽이지만 방향 상승 전환", (False, "flat"): "아래쪽·방향 횡보",
                    (False, "down"): "하락 추세"}[(above, row["slope"])]
    # 최근 이격도 흐름 (최근 10거래일 부호 변화, 최근 120거래일 최저, 5거래일 전 대비 변화)
    hist = {}
    for i in range(max(0, last - 120), last + 1):
        m = sma_at(closes, 200, i)
        if m:
            hist[i] = pct(closes[i], m)
    recent = [hist[i] >= 0 for i in range(max(0, last - CROSS_DAYS), last + 1) if i in hist]
    if above and recent and not all(recent):
        row["cross"] = "up"
    elif not above and recent and any(recent):
        row["cross"] = "down"
    low = min(hist.values()) if hist else row["d200"]
    d = row["d200"]
    emoji, name = zone_of(d)
    row["zone"] = f"{emoji} {name}"
    row["action"] = action_of(d)
    row["buy1"] = d <= BUY1
    row["deep"] = d <= DEEP_200
    notes = []
    if row["cross"] == "up":
        if low <= BUY1:
            notes.append("0선 위로 올라옴 — 2차 매수 시점 (상승장 초입)")
        else:
            notes.append("최근 0선 위로 올라옴 (2차 매수 후보)")
    elif row["cross"] == "down":
        notes.append("최근 0선 아래로 내려옴")
    d5 = hist.get(last - 5)
    if d5 is not None:
        if d <= -5 and d - d5 >= 1.0:
            notes.append("반등 중 (공포·불안에서 반등할 때가 매수 타이밍)")
        elif d >= 20 and d - d5 <= -1.0:
            notes.append("반락 중 (과열에서 반락할 때 일부 매도 고려)")
    row["note"] = " · ".join(notes) if notes else None
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
    if now.weekday() not in RUN_DAYS and not extras.forced("ma"):
        print("수요일·토요일이 아니라서 이동평균선 점검은 건너뜁니다.")
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
    buy1 = sum(1 for r in rows if r["buy1"])
    print(f"이동평균선 점검: {len(rows)}개 계산, 200일선 -15% 이하 {buy1}개, 실패 {no_data}개")


if __name__ == "__main__":
    main()
