"""
아침 시황 - 평일(월~금) '이격도·RSI 점검표' 그림 2장 (score.json, score_table.png=보유종목, score_watch.png=관심종목)

하는 일
  1) 표1 = 지수 3개 + 보유종목, 표2 = 관심종목. 일봉·주봉 종가를 Yahoo Finance(무료)에서 가져온다
  2) 50일선·200일선 이격도(%)와 RSI(14)를 코드가 계산한다 (핀비즈의 SMA·RSI 와 같은 정의: 단순이동평균, 와일더 RSI)
  3) 강의 점수표대로 각각 1(공포)~5(광기)점을 매기고, 50일 이격도·200일 이격도·RSI 3개의 평균을 종합 점수로 적는다
  3-1) 200주선은 점수 없이 '선과의 거리(%)'만 보여준다
  4) 내 매수 기준을 표시한다: 이격도 0~10% 가 최적(0 위로 올라와야 매수세가 붙은 것), RSI 30 미만은 적극 매수 구간
  5) 월~금(한국시간)에만 실행한다. 손으로 시험할 때는 FORCE_SECTIONS 에 score 를 적는다.
핀비즈는 자동 접속을 막아 두었으므로(robots 금지) 같은 계산식을 직접 쓴다. 실패해도 멈추지 않는다.
숫자는 전부 코드가 계산하고 AI는 쓰지 않는다. 보유 종목 이름은 로그에 찍지 않는다.
"""
import datetime as dt
import json
import os

import extras
import ma_signals

KST = dt.timezone(dt.timedelta(hours=9))
RUN_DAYS = (0, 1, 2, 3, 4)  # 월~금. 숫자만 고치면 바뀝니다.

# ---- 표2 '관심종목' (트레이딩뷰 목록). 표1(보유종목)과 같은 종목은 자동으로 뺀다. 고치려면 이 줄들만 바꾸면 됩니다. ----
# (심볼, 짧은 설명)
WATCH = [
    ("ICLN", "청정에너지"), ("MSOS", "대마 ETF"), ("TAN", "태양광"), ("PFE", "화이자"),                    # 민주당 테마
    ("IHE", "제약 ETF"), ("KO", "코카콜라"), ("XLV", "헬스케어"), ("UNH", "유나이티드헬스"),
    ("WM", "웨이스트매니지먼트"), ("LLY", "일라이릴리"),                                                  # 헬스케어·방어주
    ("V", "비자"), ("XLY", "경기소비재"), ("AMZN", "아마존"), ("XLP", "필수소비재"), ("XRT", "소매 ETF"),
    ("WMT", "월마트"), ("MA", "마스터카드"), ("COST", "코스트코"),                                        # 소비·유통·결제
    ("GDX", "금광주"), ("COPX", "구리광산"), ("SLV", "은"),                                                # 약달러·대체자산
    ("SCHD", "배당성장"), ("O", "리얼티인컴"), ("VNQ", "리츠"), ("ITB", "주택건설"), ("LEN", "레나"),      # 금리 하락 수혜
    ("UAL", "유나이티드항공"), ("DAL", "델타항공"),                                                       # 항공(유가 하락)
    ("CAT", "캐터필러"),                                                                                  # 산업재(관세)
]
# 표1에 나오는 종목의 다른 모습이라 관심종목에서 뺄 것 (DIA 는 다우 지수와 같은 움직임)
WATCH_SKIP = {"DIA"}
MIN_BARS = 60               # 이보다 짧으면 계산하지 않는다
RSI_N = 14
BEST_LO, BEST_HI = 0, 10    # 내 기준: 이격도 0~10% 가 가장 좋은 매수 구간
RSI_BUY = 30                # 내 기준: RSI 30 미만 = 적극 매수 구간
DEEP = -15                  # 200일선 -15% 아래 = 과냉(역발상 1차 매수)
WEEKS_200 = 200             # 200주선 = 최근 200주 종가의 단순평균

NAMES = {5: "광기", 4: "과열", 3: "중립", 2: "불안", 1: "공포"}
COLORS = {5: ("#e53935", "white"), 4: ("#f28c28", "white"), 3: ("#ffe27a", "#222222"),
          2: ("#cfeab8", "#222222"), 1: ("#2fb67a", "white")}


def score_gap(d):
    """200일선 이격도(%) 점수표: 15~ 5 · 10~15 4 · -10~10 3 · -15~-10 2 · ~-15 1"""
    if d >= 15:
        return 5
    if d >= 10:
        return 4
    if d >= -10:
        return 3
    if d > -15:
        return 2
    return 1


def score_gap50(d):
    """50일선 이격도(%) 점수표(강의 표): 15~ 5 · 10~15 4 · 0~10 3 · -9~0 2 · ~-10 1"""
    if d >= 15:
        return 5
    if d >= 10:
        return 4
    if d >= 0:
        return 3
    if d > -10:
        return 2
    return 1


def score_rsi(r):
    """RSI 점수표: 70~ 5 · 60~70 4 · 30~60 3 · 21~30 2 · ~20 1"""
    if r >= 70:
        return 5
    if r >= 60:
        return 4
    if r >= 30:
        return 3
    if r > 20:
        return 2
    return 1


def rsi14(closes, n=RSI_N):
    """와일더 방식 RSI. 데이터가 모자라면 None."""
    if len(closes) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0)
        losses += max(-ch, 0)
    ag, al = gains / n, losses / n
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(ch, 0)) / n
        al = (al * (n - 1) + max(-ch, 0)) / n
    if al == 0:
        return 100.0
    return 100 - 100 / (1 + ag / al)


def fmt(x):
    return f"{round(x, 1):g}"


def gap200w(weekly, last_date, price):
    """200주선(주봉 200개 종가의 단순평균)과 현재가의 거리(%).
    핀비즈 주봉(Weekly) 차트의 SMA 200 과 같은 방식: 이번 주 막대도 한 개로 세고, 그 종가는 가장 최근 일봉 종가로 쓴다.
    주봉이 200개가 안 되면 None."""
    if not weekly:
        return None
    try:
        ld = dt.datetime.strptime(last_date, "%Y-%m-%d")
        closes = [c for d, c in weekly if (ld - dt.datetime.strptime(d, "%Y-%m-%d")).days >= 7]  # 지난주까지 끝난 주봉
    except Exception:
        return None
    closes.append(price)  # 이번 주 막대 = 최근 종가
    if len(closes) < WEEKS_200:
        return None
    sma = sum(closes[-WEEKS_200:]) / WEEKS_200
    return round((price / sma - 1) * 100, 1)


def analyze(bars, weekly=None):
    closes = [c for _, c in bars]
    if len(closes) < MIN_BARS:
        raise ValueError("데이터 부족")
    last = closes[-1]
    n200 = min(200, len(closes))
    partial = len(closes) < 200  # 상장 후 200일이 안 된 종목은 있는 만큼으로 계산하고 * 표시
    s200 = sum(closes[-n200:]) / n200
    d = (last / s200 - 1) * 100
    s50 = sum(closes[-50:]) / 50
    d50 = (last / s50 - 1) * 100
    r = rsi14(closes)
    if r is None:
        raise ValueError("RSI 계산 불가")
    sg, sr = score_gap(d), score_rsi(r)
    return {"date": bars[-1][0], "d200": round(d, 1), "rsi": round(r, 1), "sg": sg, "sr": sr,
            "d50": round(d50, 1), "sg50": score_gap50(d50), "w200": gap200w(weekly, bars[-1][0], last),
            "total": (sg + sr + score_gap50(d50)) / 3, "partial": partial, "days": len(closes),
            "best_gap": BEST_LO <= d <= BEST_HI, "buy_rsi": r < RSI_BUY, "deep": d <= DEEP}


def drop_open_bar(bars, sym, now):
    """아직 장이 끝나지 않은(진행 중인) 마지막 일봉은 뺀다. 종가가 아닌 중간 가격으로 계산하면 숫자가 틀리고 날짜 표시도 어긋난다."""
    if len(bars) < 2:
        return bars
    last_date = bars[-1][0]
    if sym.upper().endswith(".KS"):  # 한국장: 15:30 마감
        k = now.astimezone(KST)
        if last_date == k.strftime("%Y-%m-%d") and (k.hour, k.minute) < (15, 40):
            return bars[:-1]
    elif sym.upper() == "BTC-USD":  # 코인: 하루가 UTC 기준이라 오늘 날짜 봉은 진행 중
        if last_date == now.astimezone(dt.timezone.utc).strftime("%Y-%m-%d"):
            return bars[:-1]
    else:  # 미국장: 동부시간 16:00 마감 (서머타임 여부는 -4/-5시간 중 늦은 쪽으로 넉넉히 본다)
        et = now.astimezone(dt.timezone(dt.timedelta(hours=-5)))
        if last_date == et.strftime("%Y-%m-%d") and et.hour < 17:
            return bars[:-1]
    return bars


def note_of(row):
    notes = []
    if row["best_gap"] and row["buy_rsi"]:
        notes.append("★이격도·RSI 모두 매수 구간")
    elif row["best_gap"]:
        notes.append("★이격도 매수 최적(0~10)")
    elif row["buy_rsi"]:
        notes.append("★RSI 적극 매수(<30)")
    if row["deep"]:
        notes.append("200일선 -15%↓ 과냉")
    if row["total"] >= 5:
        notes.append("종합 5 · 현금 필요 시 먼저 매도")
    if row["partial"]:
        notes.append(f"*상장 {row['days']}일 기준")
    return notes


# ---------------------------------------------------------------- 그리기

MY_RULES = ["내 기준 (초록 테두리 = 해당)",
            "1. 200일 이격도는 +0~10% 가 가장 좋은 매수 구간 (0 위로 올라와야 매수세가 붙었다는 뜻)",
            "2. RSI 30 미만이면 적극 매수 구간",
            "3. 종합 = 50일 이격도·200일 이격도·RSI 점수 3개의 평균 (높을수록 낙관 쏠림, 낮을수록 공포 쏠림)",
            "4. 동점이면 이격도가 0 위에 있는 쪽이 분위기가 더 좋아서 우선",
            "5. 현금이 필요하면 종합 5점인 종목부터 매도"]
MEMO_200W = ["200주선 메모 (초장기선 · 침체 쇼크 최후 방어선)",
             "• 점수는 매기지 않고 선과의 거리(%)만 봅니다",
             "• 고점 대비 20% 넘게 하락하는 경우가 많고, 매수로 생각하면 좋은 선",
             "• 노후대비·연금계좌 등 장기적으로 모아갈 수 있는 선",
             "• 확인 방법: finviz.com 종목 페이지 → 차트에서 Daily 대신 Weekly 선택 → SMA 200 (주봉 200개 평균)",
             "• 50일·200일 이격도와 RSI: Screener → 티커 입력 → Technical 탭 (SMA50 · SMA200 · RSI)"]


def wrap(dr, text, font, maxw):
    """픽셀 폭에 맞춰 글자 단위로 줄바꿈."""
    lines, cur = [], ""
    for ch in text:
        if dr.textlength(cur + ch, font=font) > maxw and cur:
            lines.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines


def render(rows, path, asof_text, title="이격도·RSI 점검표", row_h=62):
    from PIL import Image, ImageDraw, ImageFont
    import calendar_img
    reg, bold = calendar_img.font_files()
    F = lambda size, b=False: ImageFont.truetype(bold if b else reg, size)
    f_title, f_sub, f_head = F(44, True), F(20), F(20, True)
    f_name, f_num, f_small, f_tag, f_pill = F(24, True), F(24), F(19), F(17, True), F(19, True)
    f_label = F(16)

    W, M = 1640, 28
    # (머리글, 너비)  종목 | 50일 이격도·점수 | 200주선 거리 | 200일 이격도·점수 | RSI·점수 | 종합 | 신호
    cols = [("종목", 280), ("이격도(50일)", 115), ("점수", 110), ("200주선 거리", 135),
            ("이격도(200일)", 125), ("점수", 110), ("RSI(14)", 95), ("점수", 110), ("종합", 90), ("신호", 0)]
    cols[-1] = ("신호", W - 2 * M - sum(c[1] for c in cols[:-1]))
    widths = [c[1] for c in cols]
    HEAD_H = 54
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    legend_w = W - 2 * M - 700
    right_lines = []
    for block in (MY_RULES, MEMO_200W):
        for i, ln in enumerate(block):
            right_lines += [(w, i == 0) for w in wrap(probe, ln, f_small if i else f_head, legend_w)]
        right_lines.append(("", False))
    legend_h = max(30 + 36 + 26 + 5 * 30, 30 + len(right_lines) * 29 + 10) + 80
    H = 130 + HEAD_H + row_h * len(rows) + legend_h
    img = Image.new("RGB", (W, H), "#fbf7ee")
    dr = ImageDraw.Draw(img)
    dr.text((M, 22), title, font=f_title, fill="#222222")
    dr.text((M, 84), asof_text, font=f_sub, fill="#666666")

    def center(x, w, y, h, text, font, fill):
        dr.text((x + w // 2, y + h // 2), text, font=font, fill=fill, anchor="mm")

    def pill(x, w, y, score):
        bgc, fg = COLORS[score]
        dr.rounded_rectangle([x + 6, y + 9, x + w - 6, y + row_h - 9], radius=9, fill=bgc)
        center(x, w, y, row_h, f"{score} {NAMES[score]}", f_pill, fg)

    y = 130
    x = M
    for name, w in cols:
        fill = "#555555" if name == "200주선 거리" else "#262626"
        dr.rectangle([x, y, x + w, y + HEAD_H], fill=fill)
        center(x, w, y, HEAD_H, name, f_head, "white")
        x += w
    y += HEAD_H
    for i, r in enumerate(rows):
        bg = "#ffffff" if i % 2 == 0 else "#f6f1e4"
        dr.rectangle([M, y, W - M, y + row_h], fill=bg)
        dr.line([M, y + row_h, W - M, y + row_h], fill="#d8d2c2", width=1)
        x = M
        nm = r["name"] + ("*" if r["partial"] else "")
        dr.text((x + 14, y + row_h // 2), nm, font=f_name, fill="#1a1a1a", anchor="lm")
        if r.get("label"):
            tw = dr.textlength(nm, font=f_name)
            dr.text((x + 14 + tw + 8, y + row_h // 2 + 3), r["label"], font=f_label, fill="#8a8578", anchor="lm")
        x += widths[0]
        center(x, widths[1], y, row_h, f"{r['d50']:+.1f}%", f_num, "#1a1a1a")      # 50일 이격도
        x += widths[1]
        pill(x, widths[2], y, r["sg50"])
        x += widths[2]
        w2 = r.get("w200")                                                            # 200주선 거리(점수 없음)
        center(x, widths[3], y, row_h, "—" if w2 is None else f"{w2:+.1f}%", f_num, "#999999" if w2 is None else "#333333")
        x += widths[3]
        if r["best_gap"]:                                                             # 200일 이격도
            dr.rectangle([x + 4, y + 5, x + widths[4] - 4, y + row_h - 5], outline="#0a8f3c", width=4)
        center(x, widths[4], y, row_h, f"{r['d200']:+.1f}%", f_num, "#1a1a1a")
        x += widths[4]
        pill(x, widths[5], y, r["sg"])
        x += widths[5]
        if r["buy_rsi"]:                                                              # RSI
            dr.rectangle([x + 4, y + 5, x + widths[6] - 4, y + row_h - 5], outline="#0a8f3c", width=4)
        center(x, widths[6], y, row_h, f"{r['rsi']:.1f}", f_num, "#1a1a1a")
        x += widths[6]
        pill(x, widths[7], y, r["sr"])
        x += widths[7]
        t5 = min(5, max(1, round(r["total"])))
        bgc, fg = COLORS[t5]
        dr.rounded_rectangle([x + 8, y + 9, x + widths[8] - 8, y + row_h - 9], radius=10, fill=bgc)
        center(x, widths[8], y, row_h, fmt(r["total"]), f_name, fg)
        x += widths[8]
        # 신호: 폭에 맞춰 최대 두 줄
        text = " · ".join(note_of(r))
        ls = wrap(dr, text, f_tag, widths[9] - 20)[:2]
        col = "#0a6b2e" if (r["best_gap"] or r["buy_rsi"]) else "#555555"
        for j, ln in enumerate(ls):
            yy = y + row_h // 2 + (j - (len(ls) - 1) / 2) * 22
            dr.text((x + 12, yy), ln, font=f_tag, fill=col, anchor="lm")
        y += row_h

    # 아래 설명: 점수표 + 내 기준 + 200주선 메모
    y += 24
    ly = y
    dr.text((M, y), "점수표 (강의 기준)", font=f_head, fill="#222222")
    y += 36
    heads = ((M, "판단"), (M + 130, "이격도(200일)"), (M + 290, "이격도(50일)"), (M + 450, "RSI(14)"))
    for hx, hd in heads:
        dr.text((hx, y), hd, font=f_tag, fill="#777777")
    y += 26
    rows_l = [("5 광기", "+15% 이상", "+15% 이상", "70 이상"), ("4 과열", "+10~15%", "+10~15%", "60~70"),
              ("3 중립", "-10~+10%", "0~+10%", "30~60"), ("2 불안", "-15~-10%", "-9~0%", "21~30"),
              ("1 공포", "-15% 이하", "-10% 이하", "20 이하")]
    for lab, g200, g50, rs in rows_l:
        sc = int(lab[0])
        dr.rounded_rectangle([M, y, M + 110, y + 26], radius=6, fill=COLORS[sc][0])
        dr.text((M + 55, y + 13), lab, font=f_tag, fill=COLORS[sc][1], anchor="mm")
        for hx, tx in ((M + 130, g200), (M + 290, g50), (M + 450, rs)):
            dr.text((hx, y + 13), tx, font=f_small, fill="#333333", anchor="lm")
        y += 30
    # 오른쪽: 내 기준 + 200주선 메모
    bx, yy = M + 700, ly
    for text, is_head in right_lines:
        if text:
            dr.text((bx, yy), text, font=f_head if is_head else f_small, fill="#222222" if is_head else "#333333")
        yy += 36 if is_head else 29
    dr.text((M, H - 44), "* 상장 200일 미만은 있는 기간으로 계산 · 200주선은 200주가 안 된 종목은 — 로 표시 · 참고용이며 투자 판단은 본인이 합니다",
            font=f_small, fill="#888888")
    img.save(path)


# ---------------------------------------------------------------- 실행

def md(d):
    return d[5:].replace("-", "/")


def build_rows(items, now, tag):
    rows, no_data = [], 0
    for name, sym, label in items:
        try:
            extras.time.sleep(0.3)
            raw = ma_signals.fetch_closes(sym)
            if sym.upper() == "BTC-USD":  # 날짜 점검용 기록(가격은 남기지 않음)
                print("BTC 일봉 마지막 날짜:", [b[0] for b in raw[-3:]], "현재(UTC):", now.astimezone(dt.timezone.utc).strftime("%m-%d %H:%M"))
            bars = drop_open_bar(raw, sym, now)
            try:
                extras.time.sleep(0.2)
                weekly = ma_signals.fetch_closes(sym, "6y", "1wk")
            except Exception:
                weekly = None
            row = analyze(bars, weekly)
            row.update({"name": name, "symbol": sym, "label": label})
            rows.append(row)
        except Exception:
            no_data += 1  # 종목 이름은 기록하지 않는다(공개 저장소 로그)
    return rows, no_data


def basis_text(rows):
    us = [r["date"] for r in rows if not r["symbol"].endswith(".KS") and r["symbol"] != "BTC-USD"]
    kr = [r["date"] for r in rows if r["symbol"].endswith(".KS")]
    parts = []
    if us:
        parts.append(f"미국 {md(max(us))}")
    if kr:
        parts.append(f"한국 {md(max(kr))}")
    if any(r["symbol"] == "BTC-USD" for r in rows):
        parts.append("비트코인 " + md(max(r["date"] for r in rows if r["symbol"] == "BTC-USD")))
    return " · ".join(parts)


def holdings_list():
    """표1 종목: 금고 SCORE_HOLDINGS(없으면 HOLDINGS) 순서대로. 형식은 HOLDINGS 와 같다(심볼=이름|영문)."""
    raw = os.environ.get("SCORE_HOLDINGS", "").strip()
    if raw:
        saved = os.environ.get("HOLDINGS")
        os.environ["HOLDINGS"] = raw
        try:
            return extras.parse_holdings()
        finally:
            if saved is None:
                os.environ.pop("HOLDINGS", None)
            else:
                os.environ["HOLDINGS"] = saved
    return extras.HOLDINGS


def main():
    now = dt.datetime.now(KST)
    for f in ("score.json", "score_table.png", "score_watch.png"):
        if os.path.exists(f):
            os.remove(f)
    if now.weekday() not in RUN_DAYS and not extras.forced("score"):
        print("평일이 아니라서 이격도·RSI 점검표는 건너뜁니다.")
        return
    hold = [(h["ko"], ma_signals.yahoo_symbol(h["symbol"]), "") for h in holdings_list()]
    items1 = [(n, s, "") for n, s in ma_signals.INDICES] + hold
    have = {s.upper() for _, s, _ in items1}
    items2 = [(sym, sym, lab) for sym, lab in WATCH if sym.upper() not in have and sym.upper() not in WATCH_SKIP]
    result = {"generated_at": now.isoformat(), "failures": [], "images": []}
    all_rows = []
    for (title, path, items, row_h, head) in (
            ("이격도·RSI 점검표 · 보유종목", "score_table.png", items1, 62, "보유종목"),
            ("이격도·RSI 점검표 · 관심종목", "score_watch.png", items2, 52, "관심종목")):
        if not items:
            continue
        rows, no_data = build_rows(items, now, head)
        if no_data:
            result["failures"].append({"name": f"이격도·RSI 계산 실패({head})", "error": f"{no_data}개 종목"})
        if not rows:
            continue
        all_rows += rows
        asof = basis_text(rows) + " 종가 기준 · 이격도와 RSI(14)를 1(공포)~5(광기)점으로 매깁니다"
        try:
            render(rows, path, asof, title, row_h)
            hot = [r["name"] for r in rows if r["best_gap"] or r["buy_rsi"]]
            cap = f"📊 {title} · " + basis_text(rows) + " 종가"
            if hot:
                cap += "\n★ 내 매수 기준에 해당: " + ", ".join(hot)
            else:
                cap += "\n오늘은 내 매수 기준(200일 이격도 0~10% 또는 RSI 30 미만)에 맞는 종목이 없어요"
            result["images"].append({"image": path, "caption": cap})
        except Exception as e:
            result["failures"].append({"name": f"이격도·RSI 그림({head})", "error": extras.err_text(e)})
    if result["images"]:  # 예전 send_telegram.py 는 image/caption 한 장만 읽으므로 첫 장(보유종목)도 같이 적어 둔다
        result.update({"image": result["images"][0]["image"], "caption": result["images"][0]["caption"],
                       "as_of": max(r["date"] for r in all_rows)})
    with open("score.json", "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print(f"이격도·RSI 점검표: {len(all_rows)}개 계산, 그림 {len(result['images'])}장")


if __name__ == "__main__":
    main()
