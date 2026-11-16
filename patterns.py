"""
차트 모양 점검표 (일봉) — 고점·저점이 낮아지는지(M자) / 높아지는지(W자) / 반전 시그널 / 컵앤핸들
책 기준:  M자 = 고점 다음 고점이 낮아짐(동시에 저점도) → 매도 준비
          W자 = 저점 다음 저점이 높아짐(동시에 고점도) → 매수 준비
          반전 시그널 = 낮아지던 고점·저점이 어느 순간부터 높아지기 시작
          컵앤핸들 = W자 뒤 잠깐 쉬는 손잡이, 핸들 고점을 넘는 구간이 매수 포인트
          200일선 위로 올라오면 상승 전환 확인, 아래로 내려가면 추가 매수 주의
지금은 예시 단계: 가짜(만든) 가격으로 모양만 보여준다. 코드가 찾은 것은 신호가 아니라 '확인해 볼 후보'다.
"""
import datetime as dt
import json
import os

import numpy as np

KST = dt.timezone(dt.timedelta(hours=9))
RUN_DAYS = (5,)   # 토요일. 숫자만 고치면 바뀝니다. (월0 화1 수2 목3 금4 토5 일6)
PER_IMG = 9       # 그림 한 장에 넣는 종목 수 (텔레그램이 큰 그림을 줄이므로 너무 길면 글씨가 작아진다)
SWING_LO, SWING_HI = 0.05, 0.12   # 고점·저점으로 치는 최소 움직임: 종목의 흔들림에 따라 5~12% 사이에서 정한다
LOOK = 300        # 고점·저점을 찾는 기간(거래일)
SHOW = 250        # 그림에 보여주는 기간(약 1년)


def swing_of(c):
    """종목이 평소 흔들리는 폭(일간 등락 표준편차의 4배)을 5~12% 사이로 맞춘 값"""
    r = np.diff(c[-250:]) / c[-250:-1]
    return float(min(max(4 * r.std(), SWING_LO), SWING_HI)) if len(r) > 20 else 0.08


def zigzag(c, pct):
    """(위치, 'H'/'L') 꺾임점 목록, 지금 방향(1 오르는 중/-1 내리는 중), 꺾인 뒤 가장 먼 지점 위치"""
    piv, trend, hi, lo, ext = [], 0, 0, 0, 0
    for i in range(1, len(c)):
        if trend == 0:
            if c[i] > c[hi]:
                hi = i
            if c[i] < c[lo]:
                lo = i
            if c[i] <= c[hi] * (1 - pct):
                piv.append((hi, "H")); trend, ext = -1, i
            elif c[i] >= c[lo] * (1 + pct):
                piv.append((lo, "L")); trend, ext = 1, i
        elif trend == 1:
            if c[i] > c[ext]:
                ext = i
            elif c[i] <= c[ext] * (1 - pct):
                piv.append((ext, "H")); trend, ext = -1, i
        else:
            if c[i] < c[ext]:
                ext = i
            elif c[i] >= c[ext] * (1 + pct):
                piv.append((ext, "L")); trend, ext = 1, i
    return piv, trend, ext


def find_cup_handle(c):
    n = len(c)
    if n < 120:
        return None
    seg = c[-200:] if n > 200 else c
    off = n - len(seg)
    i = int(np.argmax(seg[: len(seg) // 2]))
    left = seg[i]
    j = i + int(np.argmin(seg[i:]))
    depth = (left - seg[j]) / left
    if not (0.12 <= depth <= 0.45) or j - i < 25:
        return None
    k = j + int(np.argmax(seg[j:]))
    rim = seg[k]
    hl = len(seg) - 1 - k
    if rim < left * 0.93 or k - j < 15 or not (4 <= hl <= 40):
        return None
    pull = (rim - seg[k:].min()) / rim
    if not (0.03 <= pull <= 0.15):
        return None
    return {"rim": float(rim), "pts": [off + i, off + j, off + k], "broke": bool(seg[-1] > rim)}


def analyze(closes, pct=None):
    c = np.asarray(closes, float)
    pct = pct or swing_of(c)
    n = len(c)
    start = max(0, n - LOOK)
    w = c[start:]
    piv, trend, ext = zigzag(w, pct)
    if piv and piv[0][0] == 0 and start > 0:   # 기간 맨 앞 가장자리는 진짜 꺾임점이 아니다
        piv = piv[1:]
    highs = [(start + i, w[i], False) for i, t in piv if t == "H"]
    lows = [(start + i, w[i], False) for i, t in piv if t == "L"]
    # 아직 꺾이지 않은 마지막 구간: 직전 고점(저점)을 이미 넘었을 때만 인정한다
    if trend == 1 and highs and w[ext] > highs[-1][1]:
        highs.append((start + ext, w[ext], True))
    if trend == -1 and lows and w[ext] < lows[-1][1]:
        lows.append((start + ext, w[ext], True))
    ma200 = float(c[-200:].mean()) if n >= 200 else None
    out = {"highs": highs, "lows": lows, "last": float(c[-1]), "ma200": ma200,
           "gap200": (c[-1] / ma200 - 1) * 100 if ma200 else None, "cup": find_cup_handle(c), "swing": pct}
    hh = lh = hl = ll = None
    if len(highs) >= 2:
        hh, lh = highs[-1][1] > highs[-2][1], highs[-1][1] < highs[-2][1]
    if len(lows) >= 2:
        hl, ll = lows[-1][1] > lows[-2][1], lows[-1][1] < lows[-2][1]
    last_conf = piv[-1][1] if piv else None
    state = "none"
    if hh and hl:
        state = "W"
        vals = [v for _, v, _ in lows]
        k = int(np.argmin(vals))
        if k >= 1 and len(vals) - 1 - k in (1, 2):                    # 최저점이 최근 2~3번째 저점이고
            before = [v for i, v, _ in highs if i < lows[k][0]]
            if vals[k - 1] > vals[k] and len(before) >= 2 and before[-1] < before[-2]:   # 그 전엔 고점·저점이 낮아지고 있었다
                state = "REV_UP"
    elif lh and ll:
        state = "M"
        vals = [v for _, v, _ in highs]
        k = int(np.argmax(vals))
        if k >= 1 and len(vals) - 1 - k in (1, 2):
            before = [v for i, v, _ in lows if i < highs[k][0]]
            if vals[k - 1] < vals[k] and len(before) >= 2 and before[-1] > before[-2]:
                state = "REV_DOWN"
    elif lh and last_conf == "H":
        state = "M_EARLY"
    elif hl and last_conf == "L":
        state = "W_EARLY"
    elif hh is not None and hl is not None:
        state = "MIXED"
    out["state"] = state
    return out


LABEL = {  # 상태: (알약 글자, 배경색, 글자색, 해석 큰 글자, 해석 작은 글자)
    "REV_UP":   ("★ 반전 시그널 (하락→상승)", "#0f9d8a", "white", "매수 준비", "낮아지던 고점·저점이 높아지기 시작"),
    "W":        ("W자 · 상승 추세", "#2fb67a", "white", "매수 준비", "저점·고점이 함께 높아지는 중"),
    "W_EARLY":  ("W자 초기 (저점 높아짐)", "#cfeab8", "#222222", "관찰", "고점도 높아지는지 확인"),
    "REV_DOWN": ("▼ 반전 시그널 (상승→하락)", "#b3261e", "white", "매도 준비", "높아지던 고점·저점이 낮아지기 시작"),
    "M":        ("M자 · 하락 추세", "#e53935", "white", "매도 준비", "고점·저점이 함께 낮아지는 중"),
    "M_EARLY":  ("M자 초기 (오른쪽 어깨 처짐)", "#f28c28", "white", "주의", "전고점 못 넘음 · 저점도 낮아지는지 확인"),
    "MIXED":    ("뚜렷한 모양 없음", "#d9d4c7", "#333333", "관망", "고점·저점 방향이 엇갈림"),
    "none":     ("판단 보류", "#d9d4c7", "#333333", "관망", "꺾임점이 부족함"),
}


def dashed(dr, p, q, fill, width=2, dash=9, gap=6):
    x0, y0 = p
    x1, y1 = q
    L = max(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5, 1)
    t = 0
    while t < L:
        a, b = t / L, min(t + dash, L) / L
        dr.line([x0 + (x1 - x0) * a, y0 + (y1 - y0) * a, x0 + (x1 - x0) * b, y0 + (y1 - y0) * b], fill=fill, width=width)
        t += dash + gap


def price(v):
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:,.1f}"


def render(rows, path, asof, title="차트 모양 점검표 · M자 / W자 / 반전 시그널 / 컵앤핸들"):
    from PIL import Image, ImageDraw, ImageFont
    import calendar_img
    reg, bold = calendar_img.font_files()
    F = lambda s, b=False: ImageFont.truetype(bold if b else reg, s)
    f_title, f_sub, f_head, f_name = F(40, True), F(20), F(20, True), F(25, True)
    f_num, f_small, f_pill, f_big, f_tiny = F(22), F(18), F(18, True), F(24, True), F(16)
    W, M, HEAD_H, RH = 1640, 28, 52, 132
    cols = [("종목", 190), ("차트 모양", 290), ("고점 흐름", 170), ("저점 흐름", 170), ("200일선", 140), ("책 기준 해석", 270), ("최근 1년 차트", 0)]
    cols[-1] = (cols[-1][0], W - 2 * M - sum(c[1] for c in cols[:-1]))
    widths = [c[1] for c in cols]
    notes = ["M자 = 고점 다음 고점이 낮아짐(동시에 저점도) → 힘이 부족, 매도 준비   /   W자 = 저점 다음 저점이 높아짐(동시에 고점도) → 힘이 생김, 매수 준비",
             "반전 시그널 = 낮아지던 고점·저점이 어느 순간부터 높아지기 시작한 곳(분위기가 바뀌는 변곡점). 반대 방향이면 ▼ 로 표시",
             "컵앤핸들 = W자로 올라온 뒤 잠깐 쉬는 손잡이. 핸들 고점(주황 점선)을 넘어서기 시작하는 구간을 매수 포인트로 봄",
             "200일선: 주가가 200일선 위로 올라오면 상승 전환 확인, 아래로 내려가면 추가 매수 주의. 이격도·RSI 점검표와 함께 보면 정확도↑",
             "차트: 검은 선 주가 · 파란 선 200일선 · 초록 점 고점 · 분홍 점 저점 · 점선은 최근 두 고점끼리, 두 저점끼리 이은 선(빨강 낮아짐 / 초록 높아짐)",
             "고점·저점은 종목의 평소 흔들림에 맞춰 5~12% 이상 움직였다 꺾인 지점만 셉니다. 코드가 찾은 것은 신호가 아니라 '직접 차트로 확인해 볼 후보'입니다."]
    H = 130 + HEAD_H + RH * len(rows) + 30 + len(notes) * 29 + 24
    img = Image.new("RGB", (W, H), "#fbf7ee")
    dr = ImageDraw.Draw(img)
    dr.text((M, 22), title, font=f_title, fill="#222222")
    dr.text((M, 80), asof, font=f_sub, fill="#666666")
    cen = lambda x, w, y, h, t, f, fill: dr.text((x + w // 2, y + h // 2), t, font=f, fill=fill, anchor="mm")
    UP, DN = "#0a7f3f", "#b3261e"
    y, x = 130, M
    for name, w in cols:
        dr.rectangle([x, y, x + w, y + HEAD_H], fill="#262626")
        cen(x, w, y, HEAD_H, name, f_head, "white")
        x += w
    y += HEAD_H
    for ri, r in enumerate(rows):
        a = r["res"]
        dr.rectangle([M, y, W - M, y + RH], fill="#ffffff" if ri % 2 == 0 else "#f6f1e4")
        dr.line([M, y + RH, W - M, y + RH], fill="#d8d2c2", width=1)
        lab = r.get("label")
        dr.text((M + 14, y + RH // 2 - (11 if lab else 0)), r["name"], font=f_name, fill="#1a1a1a", anchor="lm")
        if lab:
            dr.text((M + 14, y + RH // 2 + 19), lab, font=f_tiny, fill="#8a8578", anchor="lm")
        x = M + widths[0]
        # 차트 모양 알약 (+ 컵앤핸들 알약)
        txt, bg, fg, big, sub = LABEL[a["state"]]
        cup = a["cup"]
        both = cup and a["state"] != "none"
        py0 = y + RH // 2 - (46 if both else 21)
        if not cup or both:
            dr.rounded_rectangle([x + 8, py0, x + widths[1] - 8, py0 + 42], radius=10, fill=bg)
            cen(x, widths[1], py0, 42, txt, f_pill, fg)
        if cup:
            cy = py0 + 50 if both else py0
            dr.rounded_rectangle([x + 8, cy, x + widths[1] - 8, cy + 42], radius=10, fill="#3b82f6")
            cen(x, widths[1], cy, 42, "컵앤핸들 · " + ("핸들 고점 돌파" if cup["broke"] else "손잡이 형성 중"), f_pill, "white")
        x += widths[1]
        # 고점·저점 흐름
        for seq in (a["highs"], a["lows"]):
            wd = widths[2]
            if len(seq) >= 2:
                p, q = seq[-2][1], seq[-1][1]
                col = UP if q > p else DN
                t = f"{price(p)} → {price(q)}"
                cen(x, wd, y - 14, RH, t, f_num if dr.textlength(t, font=f_num) < wd - 8 else f_tiny, "#1a1a1a")
                cen(x, wd, y + 18, RH, ("▲ 높아짐" if q > p else "▼ 낮아짐") + (" (진행 중)" if seq[-1][2] else ""), f_small, col)
            else:
                cen(x, wd, y, RH, "—", f_num, "#999999")
            x += wd
        # 200일선
        g = a["gap200"]
        if g is None:
            cen(x, widths[4], y, RH, "—", f_num, "#999999")
        else:
            cen(x, widths[4], y - 14, RH, "위" if g >= 0 else "아래", f_big, UP if g >= 0 else DN)
            cen(x, widths[4], y + 18, RH, f"{g:+.1f}%", f_small, "#444444")
        x += widths[4]
        # 책 기준 해석
        extra = ""
        if a["state"] in ("REV_UP", "W") and g is not None:
            extra = "200일선 위 → 상승 전환 확인" if g >= 0 else "200일선 회복은 아직"
        if a["state"] in ("REV_DOWN", "M", "M_EARLY") and g is not None:
            extra = "200일선 아래 → 추가 매수 주의" if g < 0 else "200일선 이탈 여부 확인"
        if cup:
            big, sub = ("매수 포인트 근접" if not cup["broke"] else "매수 포인트"), f"핸들 고점 {price(cup['rim'])} " + ("돌파 대기" if not cup["broke"] else "돌파")
        col = UP if big.startswith("매수") else DN if big in ("매도 준비", "주의") else "#555555"
        cen(x, widths[5], y - 34, RH, big, f_big, col)
        cen(x, widths[5], y + 2, RH, sub, f_tiny, "#444444")
        if extra:
            cen(x, widths[5], y + 28, RH, extra, f_tiny, "#444444")
        x += widths[5]
        # 작은 차트 (최근 1년 + 200일선)
        c = np.asarray(r["closes"], float)
        n = len(c)
        s0 = max(0, n - SHOW)
        seg = c[s0:]
        ma = np.array([c[max(0, i - 199): i + 1].mean() if i >= 199 else np.nan for i in range(s0, n)])
        cx0, cx1, cy0, cy1 = x + 12, x + widths[6] - 12, y + 10, y + RH - 10
        lo = min(seg.min(), np.nanmin(ma) if not np.all(np.isnan(ma)) else seg.min())
        hi = max(seg.max(), np.nanmax(ma) if not np.all(np.isnan(ma)) else seg.max())
        pad = (hi - lo) * 0.08
        lo, hi = lo - pad, hi + pad
        px = lambda k: cx0 + (cx1 - cx0) * (k - s0) / max(len(seg) - 1, 1)
        py = lambda v: cy1 - (cy1 - cy0) * (v - lo) / (hi - lo)
        dr.rectangle([cx0, cy0, cx1, cy1], fill="#fdfcf8", outline="#e0d9c6")
        mp = [(px(s0 + k), py(v)) for k, v in enumerate(ma) if not np.isnan(v)]
        if len(mp) > 1:
            dr.line(mp, fill="#3b5bdb", width=2)
        dr.line([(px(s0 + k), py(v)) for k, v in enumerate(seg)], fill="#1f2937", width=3)
        for seq, fillc in ((a["highs"], "#7bd3a8"), (a["lows"], "#f3a6c0")):
            vis = [p for p in seq if p[0] >= s0]
            if len(vis) >= 2:
                p, q = vis[-2], vis[-1]
                dashed(dr, (px(p[0]), py(p[1])), (px(q[0]), py(q[1])), UP if q[1] > p[1] else DN, 2)
            for i, v, prov in vis:
                dr.ellipse([px(i) - 6, py(v) - 6, px(i) + 6, py(v) + 6], fill=fillc, outline="#333333", width=2)
        if cup:
            k = max(cup["pts"][0], s0)     # 컵 왼쪽 끝부터 오른쪽 끝까지 핸들 고점 높이의 선
            dashed(dr, (px(k), py(cup["rim"])), (cx1, py(cup["rim"])), "#e08a00", 3)
        dr.ellipse([px(n - 1) - 5, py(seg[-1]) - 5, px(n - 1) + 5, py(seg[-1]) + 5], fill="#111111")
        y += RH
    y += 22
    for ln in notes:
        dr.text((M, y), ln, font=f_small, fill="#555555")
        y += 29
    img.crop((0, 0, W, min(H, y + 14))).save(path)


# ------------------------------------------------------------------ 가짜 데이터 예시
def _walk(points, n_each, noise, seed):
    rng = np.random.default_rng(seed)
    out = []
    for a, b, n in zip(points[:-1], points[1:], n_each):
        out += list(np.linspace(a, b, n, endpoint=False) + rng.normal(0, noise * a, n))
    out.append(points[-1])
    return np.array(out)


def demo():
    S = {
        "예시 A": _walk([60, 100, 84, 93, 76, 85, 70, 77, 66], [210, 35, 30, 35, 30, 35, 30, 30], 0.004, 1),
        "예시 B": _walk([110, 120, 100, 110, 88, 98, 78, 92, 84, 100, 91, 104], [210, 25, 20, 25, 20, 25, 22, 18, 25, 18, 22], 0.004, 2),
        "예시 C": _walk([50, 70, 82, 75, 90, 82, 98, 89, 106], [210, 30, 25, 32, 25, 32, 25, 32], 0.004, 3),
        "예시 D": _walk([60, 90, 110, 98, 125, 110, 121, 110.5], [210, 35, 28, 40, 30, 30, 25], 0.003, 4),
        "예시 E": _walk([70, 100, 80, 76, 82, 99, 93, 96], [210, 40, 30, 30, 50, 15, 10], 0.003, 5),
        "예시 F": _walk([80, 90, 100, 90, 104, 86, 95], [210, 35, 30, 40, 45, 30], 0.004, 6),
    }
    return [{"name": k, "closes": v, "res": analyze(v, 0.08)} for k, v in S.items()]


TAGS = [("REV_UP", "★ 반전 시그널(하락→상승)"), ("REV_DOWN", "▼ 반전 시그널(상승→하락)"), ("M_EARLY", "M자 초기"), ("W_EARLY", "W자 초기")]


def caption_of(title, rows, basis):
    cap = f"📐 {title} · {basis} 종가"
    for key, word in TAGS:
        names = [r["name"] for r in rows if r["res"]["state"] == key]
        if names:
            cap += f"\n{word}: " + ", ".join(names)
    cups = [r["name"] for r in rows if r["res"]["cup"]]
    if cups:
        cap += "\n컵앤핸들 후보: " + ", ".join(cups)
    return cap


def main():
    import extras
    import ma_signals
    import score_table
    now = dt.datetime.now(KST)
    for f in os.listdir("."):
        if f == "patterns.json" or (f.startswith("patterns_") and f.endswith(".png")):
            os.remove(f)
    if now.weekday() not in RUN_DAYS and not extras.forced("pattern"):
        print("토요일이 아니라서 차트 모양 점검표는 건너뜁니다.")
        return
    hold = [(h["ko"], ma_signals.yahoo_symbol(h["symbol"]), "") for h in score_table.holdings_list()]
    items1 = [(n, s, "") for n, s in ma_signals.INDICES] + hold
    have = {s.upper() for _, s, _ in items1}
    items2 = [(sym, sym, lab) for sym, lab in score_table.WATCH if sym.upper() not in have and sym.upper() not in score_table.WATCH_SKIP]
    result = {"generated_at": now.isoformat(), "failures": [], "images": []}
    total = 0
    for head, tag, items in (("보유종목", "hold", items1), ("관심종목", "watch", items2)):
        rows, bad = [], 0
        for name, sym, label in items:
            try:
                extras.time.sleep(0.3)
                bars = score_table.drop_open_bar(ma_signals.fetch_closes(sym), sym, now)
                if len(bars) < 60:
                    raise ValueError("short")
                rows.append({"name": name, "label": label, "symbol": sym, "date": bars[-1][0],
                             "closes": [b[1] for b in bars], "res": analyze([b[1] for b in bars])})
            except Exception:
                bad += 1   # 종목 이름은 기록하지 않는다(공개 저장소 로그)
        if bad:
            result["failures"].append({"name": f"차트 모양 계산 실패({head})", "error": f"{bad}개 종목"})
        if not rows:
            continue
        total += len(rows)
        basis = score_table.basis_text(rows)
        n_img = -(-len(rows) // PER_IMG)
        size = -(-len(rows) // n_img)          # 장마다 종목 수를 비슷하게 나눈다
        for k in range(n_img):
            part = rows[k * size:(k + 1) * size]
            title = f"차트 모양 점검표 · {head}" + (f" ({k + 1}/{n_img})" if n_img > 1 else "")
            path = f"patterns_{tag}{k + 1}.png"
            try:
                render(part, path, basis + " 종가 기준 · 고점·저점이 낮아지는지(M자) 높아지는지(W자)를 봅니다", title)
                result["images"].append({"image": path, "caption": caption_of(title, part, basis)})
            except Exception as e:
                result["failures"].append({"name": f"차트 모양 그림({head} {k + 1})", "error": extras.err_text(e)})
    with open("patterns.json", "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print(f"차트 모양 점검표: {total}개 계산, 그림 {len(result['images'])}장")


if __name__ == "__main__":
    import sys
    if "demo" in sys.argv:
        render(demo(), "patterns_example.png", "예시 그림입니다 · 가짜(만든) 가격으로 모양만 보여드려요 — 실제 종목 값이 아니에요")
    else:
        main()
