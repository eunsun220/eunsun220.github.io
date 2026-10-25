"""
아침 시황 - 2단계: 숫자 모으기

하는 일
  1) 무료 데이터에서 시장 숫자를 가져온다 (FRED, Yahoo Finance, alternative.me, DefiLlama)
  2) 화면에 보기 좋게 출력하고, data.json 파일로 저장한다
  3) 가져오지 못한 항목은 건너뛰고, 마지막에 실패 목록을 알려준다

필요한 것
  - FRED API 키(무료). 환경변수 FRED_API_KEY 에 넣어 둔다. 키는 이 파일에 직접 쓰지 않는다.
"""
import datetime as dt
import json
import os
import sys

import requests

KST = dt.timezone(dt.timedelta(hours=9))
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (personal morning brief project)"}


# ---------------------------------------------------------------- 데이터 가져오기

def fred(series_id):
    """FRED에서 최신 값과 직전 값을 가져온다."""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FRED_API_KEY가 설정되지 않음")
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={
            "series_id": series_id,
            "api_key": key,
            "file_type": "json",
            "sort_order": "desc",
            "limit": 15,
        },
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    obs = [o for o in r.json().get("observations", []) if o.get("value") not in (".", "", None)]
    if not obs:
        raise RuntimeError("값이 비어 있음")
    last = obs[0]
    prev = obs[1] if len(obs) > 1 else None
    return {
        "value": float(last["value"]),
        "date": last["date"],
        "prev": float(prev["value"]) if prev else None,
        "prev_date": prev["date"] if prev else None,
    }


def fred_history(series_id, limit=60):
    """FRED에서 최근 기록을 오래된 순서로 가져온다. [(날짜, 값), ...]"""
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise RuntimeError("FRED_API_KEY가 설정되지 않음")
    r = requests.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": key, "file_type": "json", "sort_order": "desc", "limit": limit},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    obs = [(o["date"], float(o["value"])) for o in r.json().get("observations", []) if o.get("value") not in (".", "", None)]
    obs.reverse()
    return obs


NFCI_WINDOW = 13   # 주. 13주(약 한 분기)를 한 구간으로 보고 직전 13주와 비교한다
NFCI_TOLERANCE = 0.01  # 이 정도 차이는 "비슷"으로 본다


def _cmp(new, old, up, down):
    if abs(new - old) < NFCI_TOLERANCE:
        return "비슷"
    return up if new > old else down


def nfci_trend(hist=None):
    """최근 3개월의 저점·고점을 직전 3개월과 비교하고, 1년 범위 안의 위치를 알려준다. (숫자가 클수록 불안)"""
    hist = hist if hist is not None else fred_history("NFCI", 56)
    vals = [v for _, v in hist]
    if len(vals) < NFCI_WINDOW * 2:
        raise RuntimeError("기록이 부족함")
    recent, prior = vals[-NFCI_WINDOW:], vals[-NFCI_WINDOW * 2:-NFCI_WINDOW]
    low_r, low_p, high_r, high_p = min(recent), min(prior), max(recent), max(prior)
    low_dir = _cmp(low_r, low_p, "상승", "하락")
    high_dir = _cmp(high_r, high_p, "상승", "하락")
    arrow = {"상승": "⬆", "하락": "⬇", "비슷": "➖"}
    if low_dir == "상승" and high_dir == "상승":
        verdict = "둘 다 올라옴 → 점점 불안(긴축) 쪽"
    elif low_dir == "하락" and high_dir == "하락":
        verdict = "둘 다 내려옴 → 점점 안정(완화) 쪽"
    elif low_dir == "비슷" and high_dir == "비슷":
        verdict = "큰 변화 없음"
    else:
        verdict = "엇갈림 → 방향이 아직 뚜렷하지 않음"
    lines = [
        f"최근 3개월 저점 {low_r:.2f} {arrow[low_dir]} (직전 3개월 {low_p:.2f})",
        f"최근 3개월 고점 {high_r:.2f} {arrow[high_dir]} (직전 3개월 {high_p:.2f})",
        f"해석: {verdict}",
    ]
    if len(vals) >= 52:
        year = vals[-52:]
        lo, hi, cur = min(year), max(year), vals[-1]
        pos = (cur - lo) / (hi - lo) * 100 if hi > lo else 50
        lines.append(f"1년 범위 {lo:.2f}(가장 안정) ~ {hi:.2f}(가장 불안), 지금은 {pos:.0f}% 지점")
    return "\n".join(lines)


def yahoo(symbol):
    """Yahoo Finance(비공식)에서 일봉 종가를 가져온다."""
    r = requests.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
        params={"range": "10d", "interval": "1d"},
        headers=HEADERS,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    result = r.json()["chart"]["result"][0]
    offset = result.get("meta", {}).get("gmtoffset", 0)
    stamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    points = [(t, c) for t, c in zip(stamps, closes) if c is not None]
    if not points:
        raise RuntimeError("값이 비어 있음")

    def day(ts):
        return (dt.datetime.fromtimestamp(ts, dt.timezone.utc) + dt.timedelta(seconds=offset)).strftime("%Y-%m-%d")

    last = points[-1]
    prev = points[-2] if len(points) > 1 else None
    return {
        "value": float(last[1]),
        "date": day(last[0]),
        "prev": float(prev[1]) if prev else None,
        "prev_date": day(prev[0]) if prev else None,
    }


def crypto_fear_greed():
    """코인 공포탐욕지수 (alternative.me, 무료). 주식용 지수 아님."""
    r = requests.get("https://api.alternative.me/fng/", params={"limit": 2}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    rows = r.json()["data"]
    last = rows[0]
    prev = rows[1] if len(rows) > 1 else None
    to_date = lambda row: dt.datetime.fromtimestamp(int(row["timestamp"]), dt.timezone.utc).strftime("%Y-%m-%d")
    return {
        "value": float(last["value"]),
        "date": to_date(last),
        "prev": float(prev["value"]) if prev else None,
        "prev_date": to_date(prev) if prev else None,
        "label": last.get("value_classification"),
    }


def stablecoin_supply():
    """스테이블코인 전체 발행량 (DefiLlama, 무료). 단위: 달러."""
    r = requests.get("https://stablecoins.llama.fi/stablecoincharts/all", headers=HEADERS, timeout=40)
    r.raise_for_status()
    rows = r.json()
    if len(rows) < 8:
        raise RuntimeError("데이터가 너무 적음")
    get = lambda row: float(row["totalCirculatingUSD"]["peggedUSD"])
    to_date = lambda row: dt.datetime.fromtimestamp(int(row["date"]), dt.timezone.utc).strftime("%Y-%m-%d")
    last, week_ago = rows[-1], rows[-8]
    return {
        "value": get(last),
        "date": to_date(last),
        "prev": get(week_ago),
        "prev_date": to_date(week_ago),
    }


def yield_10y():
    """10년물 금리: 야후(거의 실시간)를 먼저 쓰고, 안 되면 FRED(하루 이상 늦음)를 쓴다."""
    try:
        raw = yahoo("^TNX")
        if raw["value"] > 20:  # 예전 방식(금리x10)으로 오면 보정
            raw["value"] /= 10
            if raw.get("prev") is not None:
                raw["prev"] /= 10
        if not (0 < raw["value"] < 20):
            raise RuntimeError("값이 이상함")
        return raw
    except Exception:
        return fred("DGS10")


# ---------------------------------------------------------------- 항목 정의
# kind: price(퍼센트 변화), level(차이값 변화)
# scale: 원래 값을 나누는 수 (표시 단위로 바꾸기 위해)

def metrics():
    return [
        # 시장
        {"group": "시장", "name": "달러지수(DXY)", "fn": lambda: yahoo("DX-Y.NYB"), "kind": "price", "unit": "", "scale": 1, "freq": "일간"},
        {"group": "시장", "name": "WTI 유가", "fn": lambda: yahoo("CL=F"), "kind": "price", "unit": "달러", "scale": 1, "freq": "일간"},
        {"group": "시장", "name": "S&P500", "fn": lambda: yahoo("^GSPC"), "kind": "price", "unit": "", "scale": 1, "freq": "일간"},
        {"group": "시장", "name": "나스닥", "fn": lambda: yahoo("^IXIC"), "kind": "price", "unit": "", "scale": 1, "freq": "일간"},
        {"group": "시장", "name": "필라델피아 반도체지수", "fn": lambda: yahoo("^SOX"), "kind": "price", "unit": "", "scale": 1, "freq": "일간"},
        {"group": "시장", "name": "원/달러 환율", "fn": lambda: yahoo("KRW=X"), "kind": "price", "unit": "원", "scale": 1, "freq": "일간"},
        {"group": "시장", "name": "비트코인", "fn": lambda: yahoo("BTC-USD"), "kind": "price", "unit": "달러", "scale": 1, "freq": "일간"},
        # 금리
        {"group": "금리", "name": "미국 10년물 금리", "fn": yield_10y, "kind": "level", "unit": "%", "scale": 1, "freq": "일간"},
        {"group": "금리", "name": "장단기금리차(10년-2년)", "fn": lambda: fred("T10Y2Y"), "kind": "level", "unit": "%p", "scale": 1, "freq": "일간(하루 늦음)"},
        # 유동성 (대부분 주 1회 발표)
        {"group": "유동성", "name": "연준 대차대조표", "fn": lambda: fred("WALCL"), "kind": "level", "unit": "조 달러", "scale": 1e6, "freq": "주간"},
        {"group": "유동성", "name": "은행 지급준비금", "fn": lambda: fred("WRESBAL"), "kind": "level", "unit": "조 달러", "scale": 1e6, "freq": "주간"},
        {"group": "유동성", "name": "재무부 일반계정(TGA, 주평균)", "fn": lambda: fred("WTREGEN"), "kind": "level", "unit": "조 달러", "scale": 1e6, "freq": "주간"},
        {"group": "유동성", "name": "은행 대출 및 리스", "fn": lambda: fred("TOTLL"), "kind": "level", "unit": "조 달러", "scale": 1e3, "freq": "주간"},
        {"group": "유동성", "name": "은행 신용 총액", "fn": lambda: fred("TOTBKCR"), "kind": "level", "unit": "조 달러", "scale": 1e3, "freq": "주간"},
        {"group": "유동성", "name": "금융상황지수 NFCI(클수록 불안)", "fn": lambda: fred("NFCI"), "kind": "level", "unit": "", "scale": 1, "freq": "주간", "digits": 3, "post": nfci_trend},
        # 코인
        {"group": "코인", "name": "코인 공포탐욕지수(0~100)", "fn": crypto_fear_greed, "kind": "level", "unit": "", "scale": 1, "freq": "일간"},
        {"group": "코인", "name": "스테이블코인 총 발행량(7일 전 대비)", "fn": stablecoin_supply, "kind": "price", "unit": "억 달러", "scale": 1e8, "freq": "일간"},
    ]


# ---------------------------------------------------------------- 실행

def build(item):
    raw = item["fn"]()
    scale = item["scale"]
    value = raw["value"] / scale
    prev = raw["prev"] / scale if raw.get("prev") is not None else None
    change = None
    if prev is not None:
        if item["kind"] == "price":
            change = (raw["value"] / raw["prev"] - 1) * 100 if raw["prev"] else None
        else:
            change = value - prev
    row = {
        "group": item["group"],
        "name": item["name"],
        "value": value,
        "prev": prev,
        "change": change,
        "change_kind": "pct" if item["kind"] == "price" else "diff",
        "unit": item["unit"],
        "date": raw["date"],
        "prev_date": raw.get("prev_date"),
        "freq": item["freq"],
        "label": raw.get("label"),
        "digits": item.get("digits"),
    }
    if item.get("post"):
        try:
            row["note"] = item["post"]()
        except Exception:  # 설명줄이 실패해도 숫자는 그대로 보낸다
            row["note"] = None
    return row


def fmt_number(v, unit="", digits=None):
    # 조 달러 단위는 변화가 작아서 소수 셋째 자리까지 보여준다
    if digits is not None:
        return f"{v:,.{digits}f}"
    if unit == "조 달러":
        return f"{v:,.3f}"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:,.1f}"
    return f"{v:,.2f}"


def fmt_line(row):
    text = f"{row['name']}: {fmt_number(row['value'], row['unit'], row.get('digits'))}{(' ' + row['unit']) if row['unit'] else ''}"
    if row.get("label"):
        text += f" ({row['label']})"
    if row["change"] is not None:
        sign = "+" if row["change"] >= 0 else ""
        digits = row.get("digits") or (3 if row["unit"] == "조 달러" else 2)
        if row["change_kind"] == "pct":
            text += f"  [{sign}{row['change']:.2f}%]"
        else:
            text += f"  [{sign}{row['change']:.{digits}f}]"
    text += f"  · 기준일 {row['date']} ({row['freq']})"
    return text


def main():
    now = dt.datetime.now(KST)
    rows, failures = [], []
    for item in metrics():
        try:
            rows.append(build(item))
        except Exception as e:  # 한 항목이 실패해도 나머지는 계속 진행
            failures.append({"name": item["name"], "error": f"{type(e).__name__}: {e}"})

    print(f"아침 시황 숫자 모으기  ({now:%Y-%m-%d %H:%M} KST)")
    print("=" * 60)
    current = None
    for row in rows:
        if row["group"] != current:
            current = row["group"]
            print(f"\n[{current}]")
        print("  " + fmt_line(row))
        if row.get("note"):
            for ln in row["note"].split("\n"):
                print("      ↳ " + ln)
    if failures:
        print("\n[가져오지 못한 항목]")
        for f in failures:
            print(f"  - {f['name']}: {f['error']}")
    print("\n참고: 주간 항목은 발표 주기상 며칠 전 숫자일 수 있습니다(기준일 확인).")

    with open("data.json", "w", encoding="utf-8") as fp:
        json.dump(
            {"generated_at": now.isoformat(), "rows": rows, "failures": failures},
            fp,
            ensure_ascii=False,
            indent=2,
        )

    # 전부 실패했을 때만 오류로 표시 (설정이 잘못됐을 가능성)
    if not rows:
        sys.exit(1)


if __name__ == "__main__":
    main()
