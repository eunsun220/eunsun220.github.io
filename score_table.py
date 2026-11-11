"""
아침 시황 - 평일(월~금) '이격도·RSI 점검표' 그림 (score.json 과 score_table.png 를 만든다)

하는 일
  1) 지수 3개(S&P500·나스닥·다우)와 내 보유 종목의 일봉 종가를 Yahoo Finance(무료)에서 가져온다
  2) 200일선 이격도(%)와 RSI(14)를 코드가 계산한다 (핀비즈의 SMA200·RSI 와 같은 정의: 200일 단순이동평균, 와일더 RSI)
  3) 강의 점수표대로 각각 1(공포)~5(광기)점을 매기고, 둘의 평균을 종합 점수로 적는다
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
MIN_BARS = 60               # 이보다 짧으면 계산하지 않는다
RSI_N = 14
BEST_LO, BEST_HI = 0, 10    # 내 기준: 이격도 0~10% 가 가장 좋은 매수 구간
RSI_BUY = 30                # 내 기준: RSI 30 미만 = 적극 매수 구간
DEEP = -15                  # 200일선 -15% 아래 = 과냉(역발상 1차 매수)

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
    return f"{x:g}"


def analyze(bars):
    closes = [c for _, c in bars]
    if len(closes) < MIN_BARS:
        raise ValueError("데이터 부족")
    last = closes[-1]
    n200 = min(200, len(closes))
    partial = len(closes) < 200  # 상장 후 200일이 안 된 종목은 있는 만큼으로 계산하고 * 표시
    s200 = sum(closes[-n200:]) / n200
    d = (last / s200 - 1) * 100
    r = rsi14(closes)
    if r is None:
        raise ValueError("RSI 계산 불가")
    sg, sr = score_gap(d), score_rsi(r)
    return {"date": bars[-1][0], "d200": round(d, 1), "rsi": round(r, 1), "sg": sg, "sr": sr,
            "total": (sg + sr) / 2, "partial": partial, "days": len(closes),
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
    return " · ".join(notes)


# ---------------------------------------------------------------- 그리기

def render(rows, path, asof_text):
    from PIL import Image, ImageDraw, ImageFont
    import calendar_img
    reg, bold = calendar_img.font_files()
    F = lambda size, b=False: ImageFont.truetype(bold if b else reg, size)
    f_title, f_sub, f_head = F(44, True), F(20), F(21, True)
    f_name, f_num, f_small, f_tag = F(24, True), F(24), F(18), F(17, True)

    W, M = 1560, 28
    cols = [("종목", 250), ("이격도(200일)", 175), ("점수", 90), ("판단", 90),
            ("RSI(14)", 120), ("점수", 90), ("판단", 90), ("종합", 100), ("신호", 0)]
    cols[-1] = ("신호", W - 2 * M - sum(c[1] for c in cols[:-1]))
    HEAD_H, ROW_H = 54, 62
    legend_h = 300
    H = 130 + HEAD_H + ROW_H * len(rows) + legend_h
    img = Image.new("RGB", (W, H), "#fbf7ee")
    dr = ImageDraw.Draw(img)
    dr.text((M, 22), "이격도·RSI 점검표", font=f_title, fill="#222222")
    dr.text((M, 84), asof_text, font=f_sub, fill="#666666")

    def center(x, w, y, h, text, font, fill):
        dr.text((x + w // 2, y + h // 2), text, font=font, fill=fill, anchor="mm")

    y = 130
    x = M
    for name, w in cols:
        dr.rectangle([x, y, x + w, y + HEAD_H], fill="#262626")
        center(x, w, y, HEAD_H, name, f_head, "white")
        x += w
    y += HEAD_H
    for i, r in enumerate(rows):
        bg = "#ffffff" if i % 2 == 0 else "#f6f1e4"
        dr.rectangle([M, y, W - M, y + ROW_H], fill=bg)
        dr.line([M, y + ROW_H, W - M, y + ROW_H], fill="#d8d2c2", width=1)
        x = M
        widths = [c[1] for c in cols]
        # 종목
        nm = r["name"] + ("*" if r["partial"] else "")
        dr.text((x + 14, y + ROW_H // 2), nm, font=f_name, fill="#1a1a1a", anchor="lm")
        x += widths[0]
        # 이격도 값
        gap_txt = f"{r['d200']:+.1f}%"
        if r["best_gap"]:
            dr.rectangle([x + 4, y + 5, x + widths[1] - 4, y + ROW_H - 5], outline="#0a8f3c", width=4)
        center(x, widths[1], y, ROW_H, gap_txt, f_num, "#1a1a1a")
        x += widths[1]
        for key, w in ((("sg"), widths[2]),):
            bgc, fg = COLORS[r["sg"]]
            dr.rectangle([x + 6, y + 8, x + w - 6, y + ROW_H - 8], fill=bgc)
            center(x, w, y, ROW_H, str(r["sg"]), f_name, fg)
            x += w
        center(x, widths[3], y, ROW_H, NAMES[r["sg"]], f_small, "#444444")
        x += widths[3]
        # RSI 값
        if r["buy_rsi"]:
            dr.rectangle([x + 4, y + 5, x + widths[4] - 4, y + ROW_H - 5], outline="#0a8f3c", width=4)
        center(x, widths[4], y, ROW_H, f"{r['rsi']:.1f}", f_num, "#1a1a1a")
        x += widths[4]
        bgc, fg = COLORS[r["sr"]]
        dr.rectangle([x + 6, y + 8, x + widths[5] - 6, y + ROW_H - 8], fill=bgc)
        center(x, widths[5], y, ROW_H, str(r["sr"]), f_name, fg)
        x += widths[5]
        center(x, widths[6], y, ROW_H, NAMES[r["sr"]], f_small, "#444444")
        x += widths[6]
        t5 = min(5, max(1, round(r["total"])))
        bgc, fg = COLORS[t5]
        dr.rounded_rectangle([x + 8, y + 8, x + widths[7] - 8, y + ROW_H - 8], radius=10, fill=bgc)
        center(x, widths[7], y, ROW_H, fmt(r["total"]), f_name, fg)
        x += widths[7]
        dr.text((x + 12, y + ROW_H // 2), note_of(r), font=f_tag, fill="#0a6b2e" if (r["best_gap"] or r["buy_rsi"]) else "#555555", anchor="lm")
        y += ROW_H

    # 아래 설명: 점수표 + 내 기준
    y += 24
    dr.text((M, y), "점수표 (강의 기준)", font=f_head, fill="#222222")
    y += 36
    gap_rows = [("5 광기", "+15% 이상", "70 이상"), ("4 과열", "+10~15%", "60~70"), ("3 중립", "-10~+10%", "30~60"),
                ("2 불안", "-15~-10%", "21~30"), ("1 공포", "-15% 이하", "20 이하")]
    for hx, hd in ((M, "판단"), (M + 150, "이격도(200일)"), (M + 360, "RSI(14)")):
        dr.text((hx, y), hd, font=f_tag, fill="#777777")
    y += 26
    for lab, g, rs in gap_rows:
        sc = int(lab[0])
        dr.rounded_rectangle([M, y, M + 120, y + 26], radius=6, fill=COLORS[sc][0])
        dr.text((M + 60, y + 13), lab, font=f_tag, fill=COLORS[sc][1], anchor="mm")
        dr.text((M + 150, y + 13), g, font=f_small, fill="#333333", anchor="lm")
        dr.text((M + 360, y + 13), rs, font=f_small, fill="#333333", anchor="lm")
        y += 30
    # 오른쪽 내 기준
    bx, by = M + 560, y - 5 * 30 - 62
    lines = ["내 기준 (초록 테두리 = 해당)",
             "1. 이격도는 +0~10% 가 가장 좋은 매수 구간 (0 위로 올라와야 매수세가 붙었다는 뜻)",
             "2. RSI 30 미만이면 적극 매수 구간",
             "3. 종합 = 이격도 점수와 RSI 점수의 평균 (높을수록 낙관 쏠림, 낮을수록 공포 쏠림)",
             "4. 동점이면 이격도가 0 위에 있는 쪽이 분위기가 더 좋아서 우선",
             "5. 현금이 필요하면 종합 5점인 종목부터 매도"]
    dr.text((bx, by), lines[0], font=f_head, fill="#222222")
    yy = by + 36
    for ln in lines[1:]:
        dr.text((bx, yy), ln, font=f_small, fill="#333333")
        yy += 30
    dr.text((M, H - 50), "* 상장 200일 미만은 있는 기간으로 계산 · 이격도와 RSI는 같은 방향으로 움직여 두 기준이 동시에 맞는 경우는 드뭅니다 · 참고용이며 투자 판단은 본인이 합니다",
            font=f_small, fill="#888888")
    img.save(path)


# ---------------------------------------------------------------- 실행

def main():
    now = dt.datetime.now(KST)
    for f in ("score.json", "score_table.png"):
        if os.path.exists(f):
            os.remove(f)
    if now.weekday() not in RUN_DAYS and not extras.forced("score"):
        print("평일이 아니라서 이격도·RSI 점검표는 건너뜁니다.")
        return
    items = [(n, s) for n, s in ma_signals.INDICES]
    items += [(h["ko"], ma_signals.yahoo_symbol(h["symbol"])) for h in extras.HOLDINGS]
    rows, no_data = [], 0
    for name, sym in items:
        try:
            extras.time.sleep(0.3)
            row = analyze(drop_open_bar(ma_signals.fetch_closes(sym), sym, now))
            row.update({"name": name, "symbol": sym})
            rows.append(row)
        except Exception:
            no_data += 1  # 종목 이름은 기록하지 않는다(공개 저장소 로그)
    result = {"generated_at": now.isoformat(), "failures": []}
    if no_data:
        result["failures"].append({"name": "이격도·RSI 계산 실패", "error": f"{no_data}개 종목"})
    if rows:
        us = [r["date"] for r in rows if not r["symbol"].endswith(".KS") and r["symbol"] != "BTC-USD"]
        kr = [r["date"] for r in rows if r["symbol"].endswith(".KS")]
        parts = []
        if us:
            parts.append(f"미국 {max(us)[5:].replace('-', '/')}")
        if kr:
            parts.append(f"한국 {max(kr)[5:].replace('-', '/')}")
        if any(r["symbol"] == "BTC-USD" for r in rows):
            parts.append("비트코인 " + max(r["date"] for r in rows if r["symbol"] == "BTC-USD")[5:].replace("-", "/"))
        asof = " · ".join(parts) + " 종가 기준 · 200일선 이격도와 RSI(14)를 1(공포)~5(광기)점으로 매깁니다"
        try:
            render(rows, "score_table.png", asof)
            hot = [r["name"] for r in rows if r["best_gap"] or r["buy_rsi"]]
            cap = "📊 이격도·RSI 점검표 · " + asof.split(" 종가")[0] + " 종가"
            if hot:
                cap += "\n★ 내 매수 기준에 해당: " + ", ".join(hot)
            else:
                cap += "\n오늘은 내 매수 기준(이격도 0~10% 또는 RSI 30 미만)에 맞는 종목이 없어요"
            result.update({"image": "score_table.png", "caption": cap, "as_of": max(r["date"] for r in rows)})
        except Exception as e:
            result["failures"].append({"name": "이격도·RSI 그림", "error": extras.err_text(e)})
    with open("score.json", "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print(f"이격도·RSI 점검표: {len(rows)}개 계산, 실패 {no_data}개")


if __name__ == "__main__":
    main()
