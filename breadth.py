"""
아침 시황 - 주 2회(화·목) '시장 체력(50일선·200일선 위 종목 비율)' 점검표 그림 (breadth.json, breadth.png)

하는 일
  1) barchart.com/stocks/momentum 의 Market Average 표(Today, Last Week)에서 전체 종목의 50일선·200일선 위 비율을 읽는다
  2) barchart.com/stocks/market-performance 에서 S&P500·러셀2000·나스닥100·섹터 11개의 50일선·200일선 위 비율을 읽는다
  3) 강의 점수표로 1(공포)~5(광기)점을 매긴다:  80%~ 5광기 · 70~80 4과열 · 30~70 3중립 · 20~30 2불안 · ~20 1공포
  4) 그림 한 장으로 만든다. 화·목(한국시간)에만 실행. 손으로 시험할 때는 FORCE_SECTIONS 에 breadth 를 적는다.
사이트 모양이 바뀌거나 막혀서 못 읽은 칸은 '—' 로 두고 이유를 failures 에 적는다. 실패해도 멈추지 않는다.
숫자는 전부 코드가 읽고 계산하며 AI는 쓰지 않는다.
"""
import datetime as dt
import html
import json
import os
import re

import requests

import extras

KST = dt.timezone(dt.timedelta(hours=9))
RUN_DAYS = (1, 3)  # 화·목. 숫자만 고치면 바뀝니다. (월0 화1 수2 목3 금4)
URL_MOM = "https://www.barchart.com/stocks/momentum"
URL_PERF = "https://www.barchart.com/stocks/market-performance"
HEAD = {"User-Agent": extras.BROWSER_UA, "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9"}

# (그림에 쓸 이름, 사이트에서 찾을 이름들)
SIZE = [("대형주 · S&P 500", ["S&P 500 Index"]),
        ("소형주 · 러셀 2000", ["Russell 2000 Index", "Russell 2000"]),
        ("기술주 · 나스닥 100", ["Nasdaq 100 Index", "NASDAQ 100 Index", "Nasdaq 100"])]
OFFENSE = [("경기소비재", "S&P 500 Consumer Discretionary"), ("정보기술", "S&P 500 Information Technology"),
           ("금융", "S&P 500 Financials"), ("산업재", "S&P 500 Industrials"), ("소재", "S&P 500 Materials"),
           ("에너지", "S&P 500 Energy"), ("통신서비스", "S&P 500 Comm Services")]
DEFENSE = [("필수소비재", "S&P 500 Consumer Staples"), ("헬스케어", "S&P 500 Health Care"),
           ("유틸리티", "S&P 500 Utilities"), ("부동산", "S&P 500 Real Estate")]

NAMES = {5: "광기", 4: "과열", 3: "중립", 2: "불안", 1: "공포"}
COLORS = {5: ("#e53935", "white"), 4: ("#f28c28", "white"), 3: ("#ffe27a", "#222222"),
          2: ("#cfeab8", "#222222"), 1: ("#2fb67a", "white")}
NUM = r"(\d+(?:\.\d+)?)%"
SIX = r"\s+".join([NUM] * 6)  # 5일·20일·50일·100일·150일·200일 순서


def score(p):
    """강의 표: 80~ 5 · 70~80 4 · 30~70 3 · 20~30 2 · ~20 1"""
    if p >= 80:
        return 5
    if p >= 70:
        return 4
    if p >= 30:
        return 3
    if p >= 20:
        return 2
    return 1


def page_text(url):
    r = requests.get(url, headers=HEAD, timeout=30)
    raw = r.text
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", raw)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", html.unescape(t))
    # 진단: 못 읽었을 때 원인을 알 수 있게 짧은 정보를 로그에 남긴다 (페이지 내용 일부일 뿐, 개인정보 없음)
    m = re.search(r"(?is)<title>(.*?)</title>", raw)
    blocked = re.findall(r"(?i)captcha|cloudflare|access denied|verify you are|enable javascript|robot", raw)[:3]
    print(f"[진단] {url.split('/')[-1]} 상태 {r.status_code} · HTML {len(raw):,}자 · 글자 {len(t):,}자 · 제목 "
          f"{(m.group(1).strip()[:60] if m else '없음')} · 차단표시 {blocked or '없음'} · "
          f"'Market Average' {'있음' if 'market average' in t.lower() else '없음'} · "
          f"'S&P 500 Index' {'있음' if 'S&P 500 Index' in t else '없음'} · 퍼센트 {len(re.findall(r'%', t))}개")
    print("[진단] 글 앞부분:", t[:200])
    r.raise_for_status()
    return t


def find_row(text, names, start=0):
    """이름 바로 뒤에 %가 6개 이어지는 줄을 찾아 (50일, 200일) 반환. 없으면 None"""
    for nm in ([names] if isinstance(names, str) else names):
        m = re.search(re.escape(nm) + r"\s+" + SIX, text[start:], re.I)
        if m:
            g = [float(x) for x in m.groups()]
            return g[2], g[5]
    return None


def read_momentum(text):
    """Market Average 표: Today / Last Week 줄 → {'today': (50,200), 'week': (50,200)}"""
    i = text.lower().find("market average")
    if i < 0:
        return None
    out = {}
    for key, label in (("today", "Today"), ("yesterday", "Yesterday"), ("week", "Last Week"), ("month", "Last Month")):
        m = re.search(r"\b" + label + r"\s+" + SIX, text[i:])
        if m:
            g = [float(x) for x in m.groups()]
            out[key] = (g[2], g[5])
    return out if "today" in out else None


def collect():
    fails, data = [], {}
    try:
        data["mom"] = read_momentum(page_text(URL_MOM))
        if not data["mom"]:
            fails.append("momentum 페이지에서 Market Average 표를 못 찾음")
    except Exception as e:
        data["mom"] = None
        fails.append("momentum 접속 실패: " + extras.err_text(e))
    try:
        t = page_text(URL_PERF)
    except Exception as e:
        t = ""
        fails.append("market-performance 접속 실패: " + extras.err_text(e))
    data["size"] = [(n, find_row(t, k)) for n, k in SIZE] if t else [(n, None) for n, _ in SIZE]
    data["off"] = [(n, find_row(t, k)) for n, k in OFFENSE] if t else [(n, None) for n, _ in OFFENSE]
    data["def"] = [(n, find_row(t, k)) for n, k in DEFENSE] if t else [(n, None) for n, _ in DEFENSE]
    miss = [n for grp in ("size", "off", "def") for n, v in data[grp] if v is None]
    if t and miss:
        fails.append("못 찾은 줄: " + ", ".join(miss))
    return data, fails


def avg(rows):
    v = [x for _, x in rows if x]
    if not v:
        return None
    return sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v)


RULES = ["70% 이상이 이동평균선 위 → 낙관론 팽배 = 매도 준비, 내려오기 시작하면 비중 축소·매도",
         "30% 이하만 이동평균선 위 → 비관론 형성 = 매수 준비, 올라오기 시작하면 비중 확대·매수",
         "200일선 위 종목 비중이 아래로 내려갈 때는 매수 X. 30% 밑으로 내려갔다가 올라올 때가 저점 매수 확인 구간",
         "시장 분위기(SPY)가 절반 이상으로 중요 — 개별종목이 아무리 날뛰어도 분위기가 나쁘면 혼자 날뛰기 쉽지 않다"]


def render(data, path, asof):
    from PIL import Image, ImageDraw, ImageFont
    import calendar_img
    reg, bold = calendar_img.font_files()
    F = lambda s, b=False: ImageFont.truetype(bold if b else reg, s)
    f_title, f_sub, f_head, f_name = F(44, True), F(20), F(20, True), F(24, True)
    f_num, f_small, f_pill, f_sec = F(24), F(19), F(19, True), F(21, True)
    W, M, RH, HEAD_H, SEC_H = 1240, 28, 54, 50, 40
    cols = [("구분", 360), ("50일선 위(%)", 190), ("점수", 150), ("200일선 위(%)", 190), ("점수", 150)]
    cols[-1] = ("점수", W - 2 * M - sum(c[1] for c in cols[:-1]))
    widths = [c[1] for c in cols]

    sections = []
    mom = data.get("mom")
    if mom:
        rows = [("전체 종목 · 오늘", mom["today"])]
        if "week" in mom:
            rows.append(("전체 종목 · 지난주", mom["week"]))
        if "month" in mom:
            rows.append(("전체 종목 · 지난달", mom["month"]))
    else:
        rows = [("전체 종목 · 오늘", None)]
    sections.append(("① 시장 전체 (바차트 Market Average)", rows))
    sections.append(("② 규모·스타일", data["size"]))
    off = list(data["off"])
    if avg(off):
        off.append(("공격형 평균", avg(off)))
    de = list(data["def"])
    if avg(de):
        de.append(("방어형 평균", avg(de)))
    sections.append(("③ 공격형 (경기민감) 섹터", off))
    sections.append(("④ 방어형 섹터", de))

    n_rows = sum(len(r) for _, r in sections)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    notes = []
    for ln in RULES:
        words, cur = ln, ""
        for ch in words:
            if probe.textlength(cur + ch, font=f_small) > W - 2 * M - 30:
                notes.append(cur)
                cur = "  " + ch
            else:
                cur += ch
        notes.append(cur)
    H = 130 + HEAD_H + n_rows * RH + len(sections) * SEC_H + 40 + 30 + 5 * 30 + 30 + len(notes) * 28 + 40
    img = Image.new("RGB", (W, H), "#fbf7ee")
    dr = ImageDraw.Draw(img)
    dr.text((M, 22), "시장 체력 점검표 · 50일·200일선 위 종목 비율", font=F(38, True), fill="#222222")
    dr.text((M, 84), asof, font=f_sub, fill="#666666")

    def center(x, w, y, h, text, font, fill):
        dr.text((x + w // 2, y + h // 2), text, font=font, fill=fill, anchor="mm")

    def pill(x, w, y, s):
        bgc, fg = COLORS[s]
        dr.rounded_rectangle([x + 6, y + 8, x + w - 6, y + RH - 8], radius=9, fill=bgc)
        center(x, w, y, RH, f"{s} {NAMES[s]}", f_pill, fg)

    y, x = 130, M
    for name, w in cols:
        dr.rectangle([x, y, x + w, y + HEAD_H], fill="#262626")
        center(x, w, y, HEAD_H, name, f_head, "white")
        x += w
    y += HEAD_H
    for title, rows in sections:
        dr.rectangle([M, y, W - M, y + SEC_H], fill="#e8dfc8")
        dr.text((M + 14, y + SEC_H // 2), title, font=f_sec, fill="#3a3322", anchor="lm")
        y += SEC_H
        for i, (nm, v) in enumerate(rows):
            is_avg = nm.endswith("평균")
            dr.rectangle([M, y, W - M, y + RH], fill="#eef3fb" if is_avg else ("#ffffff" if i % 2 == 0 else "#f6f1e4"))
            dr.line([M, y + RH, W - M, y + RH], fill="#d8d2c2", width=1)
            dr.text((M + 14, y + RH // 2), nm, font=f_name, fill="#1a1a1a", anchor="lm")
            x = M + widths[0]
            for k in (0, 1):
                if v is None:
                    center(x, widths[1 + 2 * k], y, RH, "—", f_num, "#999999")
                    x += widths[1 + 2 * k] + widths[2 + 2 * k]
                else:
                    p = v[k]
                    center(x, widths[1 + 2 * k], y, RH, f"{p:.1f}%", f_num, "#1a1a1a")
                    x += widths[1 + 2 * k]
                    pill(x, widths[2 + 2 * k], y, score(p))
                    x += widths[2 + 2 * k]
            y += RH
    y += 28
    dr.text((M, y), "점수표 (50일선·200일선 위 종목 비율 공통)", font=f_head, fill="#222222")
    y += 36
    for s, rng in ((5, "80% ~"), (4, "70 ~ 80%"), (3, "30 ~ 70%"), (2, "20 ~ 30%"), (1, "~ 20%")):
        bgc, fg = COLORS[s]
        dr.rounded_rectangle([M, y, M + 200, y + 26], radius=7, fill=bgc)
        dr.text((M + 100, y + 13), f"{s} {NAMES[s]}", font=f_small, fill=fg, anchor="mm")
        dr.text((M + 220, y + 13), rng, font=f_small, fill="#333333", anchor="lm")
        y += 30
    y += 14
    dr.text((M, y), "보는 법 (강의 메모)", font=f_head, fill="#222222")
    y += 34
    for ln in notes:
        dr.text((M + 6, y), ln, font=f_small, fill="#444444")
        y += 28
    img.crop((0, 0, W, min(H, y + 20))).save(path)


def main():
    now = dt.datetime.now(KST)
    for f in ("breadth.json", "breadth.png"):
        if os.path.exists(f):
            os.remove(f)
    if now.weekday() not in RUN_DAYS and not extras.forced("breadth"):
        print("화·목요일이 아니라서 시장 체력 점검표는 건너뜁니다.")
        return
    data, fails = collect()
    result = {"generated_at": now.isoformat(), "failures": [{"name": "시장 체력 점검표", "error": f} for f in fails]}
    got = sum(v is not None for g in ("size", "off", "def") for _, v in data[g]) + (1 if data["mom"] else 0)
    print(f"시장 체력 점검표: {got}/15줄 읽음", "; ".join(fails))
    if got:
        try:
            asof = f"{now:%Y-%m-%d} (한국시간 {now:%H:%M}) 바차트 기준 · 전날 미국 장 마감 값 · 점수는 50일선·200일선 위 종목 비율로 매깁니다"
            render(data, "breadth.png", asof)
            cap = "📊 시장 체력 점검표 · 50일선·200일선 위 종목 비율 (바차트)"
            m = data.get("mom")
            if m:
                cap += f"\n전체 종목: 50일선 위 {m['today'][0]:.1f}% · 200일선 위 {m['today'][1]:.1f}%"
            result.update({"image": "breadth.png", "caption": cap})
        except Exception as e:
            result["failures"].append({"name": "시장 체력 그림", "error": extras.err_text(e)})
    with open("breadth.json", "w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
