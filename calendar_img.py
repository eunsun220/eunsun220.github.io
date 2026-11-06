"""
아침 시황 - 일정 달력 그림 (calendar.json 과 cal_1.png, cal_2.png 를 만든다)

하는 일
  1) extras.json 에 모아 둔 경제지표·정책·발언·실적·코인 일정을 한 곳에 모은다
  2) '위클리 캘린더' 모양(월~일 칸)의 그림으로 그린다: 이번 주 1장, 다음 주 일정이 있으면 1장 더
     - 종류별 색: 지표(초록) · 정책(분홍) · 실적(하늘) · 발언(노랑) · 코인(주황) · 이벤트(보라)
     - 오늘은 빨간 테두리, 지난 날은 흐리게, ★=중요도 높음, 보유 종목 실적에는 '보유' 딱지
  3) 그림을 못 만들면 아무 파일도 남기지 않는다 → 텔레그램 쪽이 알아서 글 목록으로 대신 보낸다
실패해도 멈추지 않는다 (아침 시황 본문은 그대로 나간다). 숫자·날짜는 전부 코드가 처리하고 AI는 쓰지 않는다.
"""
import datetime as dt
import json
import os

import extras
import send_telegram as tg  # PRIORITY_EARN(주요 기업 목록)을 같이 쓴다

KST = dt.timezone(dt.timedelta(hours=9))
WD = ["월", "화", "수", "목", "금", "토", "일"]
MAX_EARN_DAY = 8  # 하루 칸에 보여줄 실적 발표 최대 개수 (넘으면 '외 N곳')

# 며칠 동안 이어지는 기간(칸마다 넣기 어려운 것)은 '보는 법' 칸에 따로 보여준다: (이름, 시작, 끝, 설명)
PERIODS = [("자사주 매입 재개 기간", "2026-10-30", "2026-12-14", "기업들이 자사주를 사들일 수 있는 기간 (수급에 우호적)")]

BG = "#fbf7ee"
KIND_COLORS = {"지표": ("#c8f0d2", "#1f7a3a"), "정책": ("#f9cdd2", "#a02a3a"), "실적": ("#bfeefb", "#0b6f8a"),
               "발언": ("#fff2a8", "#8a6d00"), "코인": ("#ffd8a8", "#9a5300"), "이벤트": ("#e3d6f7", "#5b3a99")}
KIND_ORDER = ["정책", "지표", "실적", "발언", "코인", "이벤트"]


# ---------------------------------------------------------------- 일정 모으기

def clean_title(title):
    t = str(title)
    for p in ("[코인] ", "[코인]"):
        if t.startswith(p):
            t = t[len(p):]
    reason = ""
    if " — " in t:
        t, reason = t.split(" — ", 1)
    return t.strip(), reason.strip()


def collect(ex):
    """extras.json -> {ymd: [event, ...]} (event: kind, time, text, reason, star, held)"""
    today = ex.get("today_ymd", "")
    seen, days = set(), {}

    def add(ymd, ev):
        key = (ymd, ev["kind"], ev["text"])
        if ymd and key not in seen:
            seen.add(key)
            days.setdefault(ymd, []).append(ev)

    for e in (ex.get("today_events") or []) + (ex.get("week_events") or []):
        kind = e.get("kind", "지표")
        text, reason = clean_title(e.get("title", ""))
        extra = []
        if kind == "지표":
            if e.get("forecast"):
                extra.append(f"예상 {e['forecast']}")
            if e.get("previous"):
                extra.append(f"이전 {e['previous']}")
        if extra:
            text += " · " + " / ".join(extra)
        add(e["ymd"], {"kind": kind, "time": e.get("time") or "", "text": text, "reason": reason,
                       "star": e.get("impact") == "High", "held": False})
    earn_names = {}
    for e in ex.get("earnings") or []:
        earn_names.setdefault(e["ymd"], set()).add(e.get("name", ""))
    for m in ex.get("manual_events") or []:
        if m["ymd"] < today:
            continue
        kind = m.get("kind", "이벤트")
        text, reason = clean_title(m.get("title", ""))
        if kind == "실적" and any(n and n in text for n in earn_names.get(m["ymd"], ())):
            continue  # 자동 실적 일정에 같은 회사가 이미 있으면 직접 적은 '예상' 일정은 뺀다
        add(m["ymd"], {"kind": kind, "time": "", "text": text, "reason": reason,
                       "star": m.get("impact") == "High", "held": False})
    earn_by_day = {}
    for e in ex.get("earnings") or []:
        if e["ymd"] >= today:
            earn_by_day.setdefault(e["ymd"], []).append(e)
    for ymd, lst in earn_by_day.items():
        lst.sort(key=lambda e: (not e.get("held"), e.get("symbol") not in tg.PRIORITY_EARN))
        for e in lst[:MAX_EARN_DAY]:
            text = f"{e['name']} 실적 · {e['when']}"
            if e.get("eps_estimate") is not None:
                text += f" · 예상 EPS {e['eps_estimate']:.2f}"
            add(ymd, {"kind": "실적", "time": "", "text": text, "reason": "", "star": False,
                      "held": bool(e.get("held"))})
        if len(lst) > MAX_EARN_DAY:
            add(ymd, {"kind": "실적", "time": "", "text": f"그 밖에 실적 발표 {len(lst) - MAX_EARN_DAY}곳",
                      "reason": "", "star": False, "held": False, "more": True})
    for lst in days.values():
        lst.sort(key=lambda ev: (ev.get("more", False), ev["time"] or "99:99", KIND_ORDER.index(ev["kind"])
                                 if ev["kind"] in KIND_ORDER else 9, not ev["held"]))
    return days


# ---------------------------------------------------------------- 그리기

def font_files():
    """한글 글꼴 파일 경로 (보통, 굵게). 없으면 글꼴 설치까지 시도한다 (판독기와 같은 방식)."""
    import contrarian
    from matplotlib import font_manager as fm
    family = contrarian.find_korean_font()
    reg = fm.findfont(fm.FontProperties(family=family, weight="normal"), fallback_to_default=False)
    try:
        bold = fm.findfont(fm.FontProperties(family=family, weight="bold"), fallback_to_default=False)
    except Exception:
        bold = reg
    return reg, bold


class Painter:
    def __init__(self):
        from PIL import ImageFont
        reg, bold = font_files()
        self.F = lambda size, b=False: ImageFont.truetype(bold if b else reg, size)
        self.f_chip, self.f_reason = self.F(21), self.F(17)
        self.f_num, self.f_wd = self.F(40, True), self.F(19, True)
        self.f_tag, self.f_title = self.F(15, True), self.F(46, True)
        self.f_small, self.f_pill = self.F(18), self.F(24, True)

    def wrap(self, draw, text, font, max_w):
        lines, cur = [], ""
        for ch in str(text):
            if draw.textlength(cur + ch, font=font) > max_w and cur:
                lines.append(cur)
                cur = ch.lstrip()
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines or [""]


def star(draw, cx, cy, r, color):
    import math
    pts = []
    for i in range(10):
        ang = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(ang), cy + rr * math.sin(ang)))
    draw.polygon(pts, fill=color)


def chip_layout(p, draw, ev, width):
    """칩 하나의 글줄과 높이를 미리 계산한다."""
    pad = 10
    right = (26 if ev["star"] else 0) + (62 if ev["held"] else 0)
    text = (ev["time"] + "  " if ev["time"] else "") + ev["text"]
    lines = p.wrap(draw, text, p.f_chip, width - pad * 2 - right)
    rlines = p.wrap(draw, ev["reason"], p.f_reason, width - pad * 2) if ev["reason"] else []
    h = pad * 2 + len(lines) * 27 + len(rlines) * 22
    return {"lines": lines, "rlines": rlines, "h": h, "pad": pad, "right": right}


def draw_chip(p, draw, ev, lay, x, y, width):
    bg, fg = KIND_COLORS.get(ev["kind"], KIND_COLORS["이벤트"])
    draw.rounded_rectangle([x, y, x + width, y + lay["h"]], radius=9, fill=bg)
    ty = y + lay["pad"]
    for ln in lay["lines"]:
        draw.text((x + lay["pad"], ty), ln, font=p.f_chip, fill="#222222")
        ty += 27
    for ln in lay["rlines"]:
        draw.text((x + lay["pad"], ty), ln, font=p.f_reason, fill=fg)
        ty += 22
    rx = x + width - 12
    if ev["star"]:
        star(draw, rx - 9, y + lay["pad"] + 13, 11, "#e03131")
        rx -= 26
    if ev["held"]:
        draw.rounded_rectangle([rx - 52, y + lay["pad"] + 2, rx, y + lay["pad"] + 25], radius=11, fill="#e03131")
        draw.text((rx - 44, y + lay["pad"] + 5), "보유", font=p.f_tag, fill="white")


def day_box_height(p, draw, evs, inner_w):
    if not evs:
        return 140
    return max(140, 18 + sum(chip_layout(p, draw, e, inner_w)["h"] + 8 for e in evs))


def draw_day(p, draw, x, y, w, h, date, evs, today, past):
    outline = "#e03131" if date == today else "#7a7a7a"
    fill = "#f1efe9" if past else "white"
    draw.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=fill, outline=outline,
                           width=5 if date == today else 2)
    # 왼쪽: 날짜 숫자 + 요일
    num_bg = "#9aa5a8" if past else ("#e03131" if date == today else "#38b6c4")
    draw.rounded_rectangle([x + 10, y + 10, x + 82, y + 82], radius=10, fill=num_bg)
    d = date.day
    draw.text((x + 46, y + 46), str(d), font=p.f_num, fill="white", anchor="mm")
    draw.text((x + 46, y + 108), WD[date.weekday()] + ("(오늘)" if date == today else ""),
              font=p.f_wd, fill="#e03131" if date == today else "#555555", anchor="mm")
    # 오른쪽: 일정 칩
    cx, cw = x + 96, w - 96 - 12
    cy = y + 10
    if not evs:
        msg = "지난 날" if past else ("휴장·주요 일정 없음" if date.weekday() >= 5 else "주요 일정 없음")
        draw.text((cx + 4, cy + 14), msg, font=p.f_small, fill="#aaaaaa")
        return
    for ev in evs:
        lay = chip_layout(p, draw, ev, cw)
        draw_chip(p, draw, ev, lay, cx, cy, cw)
        cy += lay["h"] + 8


def render_week(p, monday, days, today, path, horizon_note):
    from PIL import Image, ImageDraw
    W, M, GAP = 1600, 30, 24
    colw = (W - 2 * M - GAP) // 2
    inner = colw - 96 - 12
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    dates = [monday + dt.timedelta(days=i) for i in range(7)]
    left, right = dates[:4], dates[4:]
    hs = {d: day_box_height(p, probe, days.get(d.strftime("%Y-%m-%d"), []), inner) for d in dates}
    legend_h = 270
    row_h = []
    for i in range(4):
        a = hs[left[i]]
        b = hs[right[i]] if i < 3 else legend_h
        row_h.append(max(a, b))
    top = 130
    H = top + sum(row_h) + GAP * 3 + 70
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    # 제목
    draw.text((M, 22), "위클리 캘린더", font=p.f_title, fill="#222222")
    sun = dates[6]
    rng = f"{monday.month}/{monday.day}({WD[0]}) ~ {sun.month}/{sun.day}({WD[6]})"
    tw = draw.textlength(rng, font=p.f_pill)
    draw.rounded_rectangle([M + 400, 28, M + 400 + tw + 40, 80], radius=12, fill="#bff0dc")
    draw.text((M + 420, 54), rng, font=p.f_pill, fill="#222222", anchor="lm")
    # 범례
    lx = W - M - 640
    for k in KIND_ORDER:
        bg, fg = KIND_COLORS[k]
        wlab = draw.textlength(k, font=p.f_tag) + 22
        draw.rounded_rectangle([lx, 36, lx + wlab, 66], radius=8, fill=bg)
        draw.text((lx + 11, 51), k, font=p.f_tag, fill="#222222", anchor="lm")
        lx += wlab + 8
    # 칸
    y = top
    for i in range(4):
        h = row_h[i]
        for col, lst in ((0, left), (1, right)):
            x = M + col * (colw + GAP)
            if col == 1 and i == 3:
                # 설명 칸
                draw.rounded_rectangle([x, y, x + colw, y + h], radius=14, fill="#fffdf6", outline="#c9bfa5", width=2)
                draw.text((x + 22, y + 20), "보는 법", font=p.f_wd, fill="#444444")
                notes = ["지표·정책·발언·코인 시간은 한국시간이에요",
                         "실적은 미국 날짜 기준이에요 (장 전·장 마감 후)",
                         "★ = 중요도 높음 · 보유 = 내 보유 종목",
                         "빨간 테두리 = 오늘, 흐린 칸 = 지난 날",
                         horizon_note]
                for nm, s0, s1, why in PERIODS:
                    if s0 <= today.strftime("%Y-%m-%d") <= s1:
                        notes.append(f"■ {nm} {s0[5:7].lstrip('0')}/{s0[8:].lstrip('0')}~{s1[5:7].lstrip('0')}/{s1[8:].lstrip('0')} 진행 중 — {why}")
                yy = y + 56
                for n in notes:
                    for ln in p.wrap(draw, n, p.f_small, colw - 44):
                        draw.text((x + 22, yy), ln, font=p.f_small, fill="#555555")
                        yy += 25
                    yy += 4
                continue
            d = lst[i]
            draw_day(p, draw, x, y, colw, h, d, days.get(d.strftime("%Y-%m-%d"), []), today, d < today)
        y += h + GAP
    draw.text((M, H - 48), f"{dt.datetime.now(KST):%m/%d %H:%M} 기준 · AI가 정리한 참고용입니다. 투자 판단은 본인이 합니다.",
              font=p.f_small, fill="#888888")
    img.save(path)


UPCOMING_DAYS = 60  # '다가오는 일정' 그림이 보여줄 기간 (오늘부터 며칠 앞까지)
UPCOMING_MAX = 14   # 그림 한 장에 넣을 일정 최대 개수


def upcoming_list(days, start, end, today):
    """주간 그림에 안 나오는 먼 일정: [(date, event)] (코인 일정은 따로 많아서 뺀다)."""
    out = []
    for ymd in sorted(days):
        d = dt.datetime.strptime(ymd, "%Y-%m-%d").date()
        if not (start <= d <= end):
            continue
        for ev in days[ymd]:
            if ev["kind"] == "코인":
                continue
            out.append((d, ev))
    return out[:UPCOMING_MAX]


def render_upcoming(p, items, today, path):
    from PIL import Image, ImageDraw
    W, M = 1200, 30
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    inner = W - 2 * M - 150 - 24
    lays = [chip_layout(p, probe, ev, inner) for _, ev in items]
    periods = [x for x in PERIODS if x[2] >= today.strftime("%Y-%m-%d")]
    H = 130 + sum(max(lay["h"], 64) + 12 for lay in lays) + (60 + 34 * len(periods) if periods else 0) + 80
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw.text((M, 22), "다가오는 주요 일정", font=p.f_title, fill="#222222")
    draw.text((M, 84), "이번 주·다음 주 그림 다음에 오는 일정이에요 (코인 일정 제외, 날짜는 바뀔 수 있어요)", font=p.f_small, fill="#666666")
    y = 130
    for (d, ev), lay in zip(items, lays):
        h = max(lay["h"], 64)
        draw.rounded_rectangle([M, y, W - M, y + h], radius=12, fill="white", outline="#9a9a9a", width=2)
        dd = (d - today).days
        draw.rounded_rectangle([M + 8, y + 8, M + 118, y + h - 8], radius=10, fill="#38b6c4")
        draw.text((M + 63, y + h // 2 - 12), f"{d.month}/{d.day}({WD[d.weekday()]})", font=p.f_wd, fill="white", anchor="mm")
        draw.text((M + 63, y + h // 2 + 14), f"D-{dd}", font=p.f_tag, fill="white", anchor="mm")
        draw_chip(p, draw, ev, lay, M + 130, y + (h - lay["h"]) // 2, inner)
        y += h + 12
    if periods:
        y += 10
        draw.text((M, y), "이어지는 기간", font=p.f_wd, fill="#444444")
        y += 36
        for nm, s0, s1, why in periods:
            draw.text((M, y), f"■ {nm} {int(s0[5:7])}/{int(s0[8:])}~{int(s1[5:7])}/{int(s1[8:])} — {why}", font=p.f_small, fill="#555555")
            y += 34
    draw.text((M, H - 48), f"{dt.datetime.now(KST):%m/%d %H:%M} 기준 · 참고용입니다. 투자 판단은 본인이 합니다.", font=p.f_small, fill="#888888")
    img.save(path)


def main():
    for f in ("calendar.json", "cal_1.png", "cal_2.png", "cal_3.png"):
        if os.path.exists(f):
            os.remove(f)
    try:
        with open("extras.json", encoding="utf-8") as fp:
            ex = json.load(fp)
    except (OSError, ValueError):
        print("extras.json 이 없어서 일정 그림은 건너뜁니다.")
        return
    now = dt.datetime.now(KST)
    today = dt.datetime.strptime(ex.get("today_ymd") or now.strftime("%Y-%m-%d"), "%Y-%m-%d").date()
    days = collect(ex)
    this_mon = today - dt.timedelta(days=today.weekday())
    next_mon = this_mon + dt.timedelta(days=7)
    failures, images = [], []
    try:
        p = Painter()
        render_week(p, this_mon, days, today, "cal_1.png", "")
        images.append({"path": "cal_1.png", "caption": f"📅 이번 주 일정 ({this_mon.month}/{this_mon.day}~{(this_mon + dt.timedelta(days=6)).month}/{(this_mon + dt.timedelta(days=6)).day})"})
        next_end = next_mon + dt.timedelta(days=6)
        has_next = any(next_mon.strftime("%Y-%m-%d") <= k <= next_end.strftime("%Y-%m-%d") for k in days)
        if has_next:
            render_week(p, next_mon, days, today, "cal_2.png", "다음 주는 아직 확정되지 않은 일정이 더 있을 수 있어요")
            images.append({"path": "cal_2.png", "caption": f"📅 다음 주 일정 ({next_mon.month}/{next_mon.day}~{(next_mon + dt.timedelta(days=6)).month}/{(next_mon + dt.timedelta(days=6)).day})"})
        far = upcoming_list(days, next_end + dt.timedelta(days=1), today + dt.timedelta(days=UPCOMING_DAYS), today)
        if far:
            render_upcoming(p, far, today, "cal_3.png")
            images.append({"path": "cal_3.png", "caption": f"📅 다가오는 주요 일정 ({far[0][0].month}/{far[0][0].day}~{far[-1][0].month}/{far[-1][0].day})"})
    except Exception as e:
        failures.append({"name": "일정 달력 그림", "error": extras.err_text(e)})
        for f in ("cal_1.png", "cal_2.png", "cal_3.png"):
            if os.path.exists(f):
                os.remove(f)
        images = []
    with open("calendar.json", "w", encoding="utf-8") as fp:
        json.dump({"generated_at": now.isoformat(), "images": images, "failures": failures}, fp, ensure_ascii=False, indent=2)
    print(f"일정 달력: 그림 {len(images)}장" + (f", 실패: {failures[0]['error']}" if failures else ""))


if __name__ == "__main__":
    main()
