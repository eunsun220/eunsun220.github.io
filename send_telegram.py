"""
아침 시황 - 텔레그램으로 보내기

하는 일
  1) collect_data.py 가 만든 data.json (숫자)
     extras.py 가 만든 extras.json (뉴스·공포탐욕·일정) 을 읽는다
  2) 보기 좋은 글로 정리한다 (기사 제목은 누르면 기사로 이동)
  3) 텔레그램 봇으로 내 채팅에 보낸다

필요한 것 (GitHub 금고에 저장, 이 파일에 직접 쓰지 않는다)
  - TELEGRAM_BOT_TOKEN : BotFather가 준 봇 토큰
  - TELEGRAM_CHAT_ID   : 내 채팅 번호
"""
import datetime as dt
import html
from urllib.parse import quote
import json
import time
import os
import re
import sys

import requests

KST = dt.timezone(dt.timedelta(hours=9))
WEEKDAYS = "월화수목금토일"
LIMIT = 3800  # 텔레그램 한 번에 보낼 수 있는 글자(4096)보다 조금 작게
LEVEL_MARK = {"high": "🔴", "mid": "🟡", "low": "⚪"}
RATING_KO = {
    "extreme fear": "극단적 공포", "fear": "공포", "neutral": "중립",
    "greed": "탐욕", "extreme greed": "극단적 탐욕",
}
NEWS_ORDER = ["보유", "월가", "테크", "대형주", "AI", "피지컬AI", "상장·공급", "이슈", "금", "지정학", "정치", "코인", "보고서", "주요 뉴스"]


def esc(s):
    return html.escape(str(s), quote=False)


def fmt_number(v, unit="", digits=None):
    if digits is not None:
        return f"{v:,.{digits}f}"
    if unit == "조 달러":
        return f"{v:,.3f}"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if abs(v) >= 100:
        return f"{v:,.1f}"
    return f"{v:,.2f}"


def short_date(s):
    # 2026-10-07 -> 10/07
    return f"{s[5:7]}/{s[8:10]}"


def is_old(row, now):
    try:
        d = dt.datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=KST)
    except (KeyError, ValueError):
        return False
    return (now - d).days > 4


def line(row, now):
    unit = row["unit"]
    text = f"• {row['name']} {fmt_number(row['value'], unit, row.get('digits'))}"
    if unit:
        text += unit if unit in ("%", "%p") else f" {unit}"
    if row.get("label"):
        text += f" ({row['label']})"
    if row["change"] is not None:
        sign = "+" if row["change"] >= 0 else ""
        if row["change_kind"] == "pct":
            text += f" ({sign}{row['change']:.2f}%)"
        else:
            digits = row.get("digits") or (3 if unit == "조 달러" else 2)
            text += f" ({sign}{row['change']:.{digits}f})"
    if row["freq"] != "일간" or is_old(row, now):
        text += f" · {short_date(row['date'])} 기준"
    out = esc(text)
    if row.get("note"):
        out += "".join("\n   ↳ " + esc(ln) for ln in row["note"].split("\n"))
    if row.get("hint"):
        tail = ""
        eff = row.get("effect")
        if eff == "flat":
            tail = " · 이번엔 큰 변화 없음"
        elif eff and row["change"] is not None:
            arrow = "⬆ 늘어남" if row["change"] > 0 else "⬇ 줄어듦"
            tail = f" · 이번엔 {arrow} → {row.get('target', '위험자산')} {'우호적' if eff == '우호' else '부담'}"
        out += "\n   ↳ " + esc(row["hint"] + tail)
    return out


def fear_greed_lines(rows, extras):
    out = []
    fg = (extras or {}).get("stock_fg")
    if fg:
        rating = RATING_KO.get(str(fg["rating"]).lower(), fg["rating"])
        out.append(
            f"• 주식 공포탐욕(CNN) {fg['score']:.0f} {esc(rating)} "
            f"(전일 {fg['prev_close']:.0f} · 1주 전 {fg['week']:.0f} · 1달 전 {fg['month']:.0f})"
        )
    cmc = (extras or {}).get("coin_fg_cmc")
    if cmc:
        label = RATING_KO.get(str(cmc.get("label", "")).lower(), cmc.get("label", ""))
        out.append(f"• 코인 공포탐욕(코인마켓캡) {cmc['value']:.0f} {esc(label)}".rstrip())
    else:
        for r in rows:
            if "공포탐욕" in r["name"]:
                text = f"• 코인 공포탐욕(alternative.me) {r['value']:.0f}"
                if r.get("label"):
                    text += f" ({esc(r['label'])})"
                if r["change"] is not None:
                    text += f" ({'+' if r['change'] >= 0 else ''}{r['change']:.0f})"
                out.append(text)
    return out


def holdings_block(extras):
    q = (extras or {}).get("holdings")
    if not q:
        return None
    def asof(kr):
        ds = sorted({h["date"] for h in q if bool(h.get("kr")) == kr})
        return f"{ds[-1][5:7]}/{ds[-1][8:10]}" if ds else ""

    us, kr = asof(False), asof(True)
    basis = " · ".join(x for x in (f"미국 {us} 종가" if us else "", f"한국 {kr} 종가" if kr else "") if x)
    lines = [f"<b>[내 보유 종목]</b> 직전 거래일 대비 ({esc(basis)} 기준)"]
    parts = []
    for h in q:
        mark = "🟢" if h["change"] > 0 else ("🔴" if h["change"] < 0 else "⚪")
        parts.append(f"{mark}{esc(h['name'])} {h['change']:+.1f}%")
    for i in range(0, len(parts), 4):
        lines.append(" ".join(parts[i:i + 4]))
    for h in q:
        if not h.get("big"):
            continue
        lines.append(f"\n⚠ {esc(h['name'])} {h['change']:+.1f}% 큰 변동 — 관련 기사")
        for n in h.get("news") or []:
            url = html.escape(n["url"], quote=True)
            lines.append(f'• <a href="{url}">{esc(n["title"])}</a>')
        if not h.get("news"):
            lines.append("• 관련 기사를 찾지 못했습니다")
    return "\n".join(lines)


def alert_block(extras):
    """대사관 인력 철수·자국민 대피·여행경보 같은 안전 신호. 없으면 '없음'이라고 알려준다."""
    news = (extras or {}).get("news")
    if not news:
        return None
    alerts = [it for it in news.get("items", []) if it.get("alert")]
    lines = ["<b>🚨 지정학 안전 신호</b> 대사관 인력 철수·자국민 대피·여행경보"]
    if not alerts:
        lines.append("• 새로 확인된 소식 없음")
    for it in alerts:
        url = html.escape(it["url"], quote=True)
        lines.append(f'• <a href="{url}">{esc(it["title"])}</a> ({esc(it.get("source", ""))})')
    return "\n".join(lines)


def news_blocks(extras):
    news = (extras or {}).get("news")
    if not news or not news.get("items"):
        return []
    blocks = []
    if news.get("summary"):
        blocks.append("<b>한 줄 요약</b>\n" + esc(news["summary"]))
    by_cat = {}
    for it in news["items"]:
        if it.get("alert"):
            continue  # 안전 신호는 위 별도 칸에 보여준다
        by_cat.setdefault(it["category"], []).append(it)
    lines = ["<b>[밤사이 핵심 뉴스]</b> 🔴 꼭 확인 · 🟡 참고 · ⚪ 단기 소음 가능"]
    cats = [c for c in NEWS_ORDER if c in by_cat] + [c for c in by_cat if c not in NEWS_ORDER]
    for c in cats:
        lines.append(f"\n<b>{esc(c)}</b>")
        for it in by_cat[c]:
            mark = LEVEL_MARK.get(it["level"], "🟡")
            url = html.escape(it["url"], quote=True)
            lines.append(f'{mark} <a href="{url}">{esc(it["title"])}</a>')
            if it.get("implication"):
                lines.append("   ↳ " + esc(it["implication"]))
    if (extras or {}).get("holdings") and "보유" not in by_cat:
        lines.insert(1, "\n<b>보유</b>\n• 보유 종목 관련 새 주요 소식 없음")
    if news.get("mode") == "raw":
        lines.append("\n※ AI 선별이 안 돼 제목만 모았습니다.")
    blocks.append("\n".join(lines))
    return blocks


KIND_MARK = {"지표": "📊", "정책": "🏛", "발언": "🎤", "실적": "💼", "이벤트": "📌", "코인": "🪙"}
MAX_EARN_WEEK = 30  # 이번 주 칸에 보여줄 실적 발표 최대 개수 (너무 길어지지 않게)
# 개수가 넘칠 때 먼저 남겨 둘 주요 기업 (나머지는 날짜순으로 채움)
PRIORITY_EARN = {"NVDA", "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "TSM", "AMD", "INTC", "MU",
                 "ORCL", "PLTR", "NFLX", "CRM", "JPM", "GS", "WMT", "HD", "COST", "LLY", "UNH", "XOM", "BA",
                 "COIN", "MSTR", "ASML", "QCOM", "ANET", "SMCI", "VRT", "CRDO", "ARM", "ALAB", "COHR",
                 "CEG", "VST", "BE", "GLW"}


def pick_earnings(earn, limit):
    """넘치면 주요 기업을 먼저 남기고, 남는 자리를 날짜 빠른 순으로 채운다."""
    if len(earn) <= limit:
        return earn
    def is_top(e):
        return e.get("symbol") in PRIORITY_EARN or e.get("held")

    top = [e for e in earn if is_top(e)][:limit]
    rest = [e for e in earn if not is_top(e)][:limit - len(top)]
    keep = {id(e) for e in top + rest}
    return [e for e in earn if id(e) in keep]


def ymd_label(ymd):
    d = dt.datetime.strptime(ymd, "%Y-%m-%d")
    return f"{d:%m/%d}({WEEKDAYS[d.weekday()]})"


def event_text(e, star=True):
    kind = e.get("kind", "지표")
    text = f"{KIND_MARK.get(kind, '📌')} {esc(e['title'])}"
    if e.get("time"):
        text = f"{e['time']} " + text
    if kind == "지표":
        extra = []
        if e.get("forecast"):
            extra.append(f"예상 {esc(e['forecast'])}")
        if e.get("previous"):
            extra.append(f"이전 {esc(e['previous'])}")
        if extra:
            text += " · " + " / ".join(extra)
    if star and e.get("impact") == "High":
        text += " ⭐"
    return text


def earn_text(e):
    text = f"{KIND_MARK['실적']} {esc(e['name'])} 실적 · {esc(e['when'])}"
    if e.get("eps_estimate") is not None:
        text += f" · 예상 EPS {e['eps_estimate']:.2f}"
    if e.get("held"):
        text += " ⭐보유"
    return text


def group_lines(items):
    """[(ymd, 정렬용 시간, 글)] -> 날짜별로 묶은 줄 목록"""
    lines, cur = [], None
    for ymd, _, text in sorted(items, key=lambda x: (x[0], x[1] or "~")):
        if ymd != cur:
            cur = ymd
            lines.append(ymd_label(ymd))
        lines.append("• " + text)
    return lines


def today_block(extras):
    if not extras:
        return None
    today = extras.get("today_ymd", "")
    items = [(e["ymd"], e["time"], event_text(e)) for e in extras.get("today_events") or []]
    items += [(m["ymd"], "", event_text(m)) for m in extras.get("manual_events") or [] if m["ymd"] == today]
    items += [(e["ymd"], "", earn_text(e)) for e in extras.get("earnings") or [] if e["ymd"] == today]
    lines = ["<b>[오늘 주요 일정]</b> 한국시간 기준 앞으로 24시간 (⭐=중요도 높음, 실적은 미국 날짜)"]
    lines += group_lines(items)
    failed = {f["name"] for f in (extras.get("failures") or [])}
    if "경제지표 일정" in failed:
        lines.append("• 경제지표 일정을 가져오지 못했습니다.")
    if "실적 일정" in failed:
        lines.append("• 실적 일정을 가져오지 못했습니다.")
    if len(lines) == 1:
        lines.append("• 확인된 주요 일정이 없습니다.")
    return "\n".join(lines)


def week_block(extras):
    if not extras:
        return None
    today = extras.get("today_ymd", "")
    items = [(e["ymd"], e["time"], event_text(e, star=False)) for e in extras.get("week_events") or []]
    items += [(m["ymd"], "", event_text(m)) for m in extras.get("manual_events") or [] if m["ymd"] > today]
    earn = [e for e in extras.get("earnings") or [] if e["ymd"] > today]
    items += [(e["ymd"], "", earn_text(e)) for e in pick_earnings(earn, MAX_EARN_WEEK)]
    lines = ["<b>[이번 주·다음 주 체크]</b> 향후 10일, 중요 일정만 (지표·정책·발언은 한국시간, 실적은 미국 날짜)"]
    lines += group_lines(items)
    if len(earn) > MAX_EARN_WEEK:
        lines.append(f"※ 실적 발표가 많아 주요 기업 위주로 {MAX_EARN_WEEK}개까지만 표시")
    if len(lines) == 1:
        lines.append("• 확인된 일정이 없습니다.")
    return "\n".join(lines)


def cover_block(extras):
    c = (extras or {}).get("cover")
    if not c:
        return None
    lines = ["<b>[이코노미스트 표지지표]</b> 표지 주제와 반대로 맞는 경우가 많다는 참고용 지표 (매주 목요일 발행)"]
    m = c.get("manual")
    if m:
        lines.append(f"• 표지 주제: {esc(m['topic'])} ({esc(m['date'])})")
        if m.get("reading"):
            lines.append("   ↳ " + esc(m["reading"]))
    else:
        lines.append("• 이번 주 표지 주제는 직접 확인이 필요합니다: economist.com/weeklyedition")
        lines.append("   ↳ 알려주시면 covers.json 에 적어 두고 '반대로 읽기'를 함께 보내드립니다.")
    for it in c.get("links") or []:
        url = html.escape(it["url"], quote=True)
        lines.append(f'• 관련 기사: <a href="{url}">{esc(it["title"])}</a>')
    return "\n".join(lines)


def month_block(extras):
    mp = (extras or {}).get("month_preview")
    if not mp:
        return None
    items = [(e["ymd"], "", f"{KIND_MARK.get(e['kind'], '📌')} {esc(e['title'])}") for e in mp.get("events") or []]
    items += [(e["ymd"], "", earn_text(e)) for e in mp.get("earnings") or []]
    lines = [f"<b>[다음 달({esc(mp['label'])}) 주요 일정]</b> 월말 정리 (날짜는 미국 기준, 실적은 주요 기업만)"]
    lines += group_lines(items)
    if mp.get("error"):
        lines.append(f"※ 실적 일정을 가져오지 못했습니다({esc(mp['error'])})")
    if len(lines) == 1:
        lines.append("• 등록된 일정이 없습니다. events.json 에 추가할 수 있습니다.")
    return "\n".join(lines)


def reminder_block(extras):
    items = (extras or {}).get("monthly_reminders")
    if not items:
        return None
    return "\n".join(["<b>[매달 1일 다시 보는 메모]</b>"] + [f"{i}. {esc(t)}" for i, t in enumerate(items, 1)])


def weekly_block(weekly):
    if not weekly or not weekly.get("groups"):
        return None
    d = weekly.get("as_of") or ""
    title = f"<b>[주간 특징주 요약]</b> {esc(d[5:7] + '/' + d[8:10]) if d else ''} 기준, 최근 5거래일 등락률"
    lines = [title]
    themes = weekly.get("themes") or {}
    for n, g in enumerate(weekly["groups"], 1):
        lines.append(f"\n<b>{n}. {esc(g['category'])}</b>")
        if themes.get(g["category"]):
            lines.append("💡 " + esc(themes[g["category"]]))
        for sub in g["subs"]:
            parts = []
            for r in sub["rows"]:
                mark = "🟢" if r["change"] > 0 else ("🔴" if r["change"] < 0 else "⚪")
                parts.append(f"{mark}{esc(r['symbol'])} {r['change']:+.1f}%")
            lines.append(f"{esc(sub['label'])}: " + " ".join(parts))
    lines.append("\n※ 💡 한 줄은 이번 주 기사 제목만 보고 AI가 쓴 참고용 요약입니다.")
    return "\n".join(lines)


YAHOO_CHART = "https://finance.yahoo.com/chart/"


def ma_label(r):
    n = esc(r["name"])
    url = html.escape(YAHOO_CHART + quote(r["symbol"], safe=""), quote=True)
    return f'<a href="{url}">{n}</a>'


def ma_lines(r):
    d50, d200 = r.get("d50"), r.get("d200")
    parts = []
    if d50 is not None:
        parts.append(f"50일선 {d50:+.1f}%")
    if d200 is not None:
        parts.append(f"200일선 {d200:+.1f}%")
    head = f"• {ma_label(r)}"
    if r.get("kind") == "index":
        head += f" {r['price']:,.0f}"
    head += " — " + (" · ".join(parts) if parts else "계산 불가")
    if d200 is not None and r.get("zone"):
        head += f" → {esc(r['zone'])}"
    out = [head]
    if d200 is None:
        out.append(f"   ↳ 데이터 {r.get('days', 0)}일치뿐이라 200일선 계산 불가" + (" (50일선만 표시)" if d50 is not None else ""))
        return out
    out.append(f"   ↳ 판단: {esc(r['action'])}" + (" · ⭐적극 매수 검토(-20% 이하)" if r.get("deep") else ""))
    if r.get("note"):
        out.append(f"   ↳ {esc(r['note'])}")
    arrow = {"up": "⬆", "down": "⬇", "flat": "➡"}.get(r.get("slope"), "")
    mark = "🟢" if r.get("trend") == "상승 추세" else ("🔴" if r.get("trend") == "하락 추세" else "🟡")
    out.append(f"   ↳ {mark} {esc(r['trend'])} (200일선 방향 {arrow})" + (" · 50일선 -5% 이하 눌림" if r.get("dip50") else ""))
    w = r.get("w200")
    if w:
        tail = "최후 방어선 위" if w["d"] >= 0 else "최후 방어선 이탈 — 경계"
        out.append(f"   ↳ 200주선 {w['value']:,.0f} 대비 {w['d']:+.1f}% ({tail})")
    if r.get("kind") == "index" and r.get("slope"):
        up, dn = r["slope"] == "up", r["slope"] == "down"
        ab = r.get("above200")
        out.append("   ↳ 점검: ①200일선 위 " + ("✅" if ab else "❌") + " ②방향 위로 " + ("✅" if up else "❌")
                   + " ③200일선 아래 " + ("❌" if ab else "✅") + " ④방향 아래로 " + ("✅" if dn else "❌"))
    return out


MA_LEGEND = (
    "\n※ 200일선 이격도 구간: 🟥광기 +30% 이상 · 🟧과열 +20~30% · ⬜중립 -5~+20% · 🟨불안 -10~-5% · 🟦공포 -10% 이하\n"
    "매수 기준: -15% 이하 1차 매수 시작(역발상·장기) → 0선 위로 올라올 때 2차 매수(상승장 초입) → 0~10% 매수 → 10~20% 관망(추매 X) → 20% 이상 추가 매수 보류\n"
    "종목마다 흔들리는 폭이 달라 고변동주는 구간을 넓게 봐야 합니다. 이름을 누르면 야후 파이낸스 차트가 열립니다 "
    "(차트 → Indicators → Moving Average → Period 50 또는 200 → Save)."
)


def ma_block(ma):
    if not ma or not ma.get("rows"):
        return None
    d = ma.get("as_of") or ""
    lines = [f"<b>[이동평균선 점검 · 수/토]</b> {esc(d[5:7] + '/' + d[8:10]) if d else ''} 종가 · 일봉 기준, 이동평균선 대비 몇 % 위/아래인지"]
    buy1 = [r for r in ma["rows"] if r.get("buy1")]
    if buy1:
        lines.append("\n🔔 <b>200일선 -15% 이하 — 과냉(역발상·장기 관점) 1차 매수 시작 구간</b>")
        for r in buy1:
            lines.append(f"• {ma_label(r)} {r['d200']:+.1f}%" + (" ⭐적극 매수 검토(-20% 이하)" if r.get("deep") else ""))
    else:
        lines.append("\n🔔 200일선 -15% 이하(1차 매수 구간)인 종목: 해당 없음")
    idx = [r for r in ma["rows"] if r.get("kind") == "index"]
    stk = [r for r in ma["rows"] if r.get("kind") != "index"]
    if idx:
        lines.append("\n<b>1. 지수</b>")
        for r in idx:
            lines += ma_lines(r)
    if stk:
        lines.append("\n<b>2. 내 보유 종목</b>")
        for r in stk:
            lines += ma_lines(r)
    lines.append(MA_LEGEND)
    return "\n".join(lines)


CONTRARIAN_HELP = [
    ("낙관/비관 스프레드 (주간)",
     ["미국 개인투자자협회(AAII)가 개인투자자에게 '앞으로 6개월 주식이 오를까, 내릴까'를 묻는 설문이에요. 낙관 % − 비관 % 가 스프레드이고 매주 목요일에 발표돼요.",
      "사람들이 너무 비관적이면(마이너스) 오히려 바닥 근처, 너무 낙관적이면(플러스) 고점 근처라고 보는 게 '역발상'이에요.",
      "내 기준: -20 이하 1차 매수 · -30 이하 2차 매수 · 비관론이 정점(바닥)을 찍고 올라갈 때 3차 매수(하락장이 길어질 수 있어서). 비관론 50% 이상일 때 저점인 경우가 많았어요.",
      "점수: +30 이상 5광기 · +20~30 4과열 · -20~+20 3중립 · -30~-20 2불안 · -30 이하 1공포"]),
    ("풋/콜 비율 (일간)",
     ["CBOE 주식 옵션에서 풋(하락 베팅) 거래량 ÷ 콜(상승 베팅) 거래량이에요. 높으면 하락 베팅이 우세(공포), 낮으면 상승 베팅이 우세(낙관)예요.",
      "내 기준: 1 이상(1년에 1~2번) 1차 매수 · 1.5 이상(2년에 1~2번) 2차 · 2 이상(3년에 1~2번) 3차 매수. 0.5 이하는 단기 고점 신호일 수 있지만 잘 안 맞고 상승이 생각보다 오래 가는 편이에요.",
      "점수: 0.4 이하 5광기 · 0.4~0.5 4과열 · 0.5~1 3중립 · 1~1.2 2불안 · 1.2 이상 1공포"]),
    ("빚투 = 신용융자 증가율 (월간)",
     ["증권사에서 돈을 빌려 산 주식 잔고(신용융자)가 1년 전보다 몇 % 늘었는지예요. FINRA가 한 달에 한 번, 약 한 달 늦게 발표해요.",
      "빠르게 늘면 과열, 크게 줄면 바닥 근처예요. 전년보다 늘고 있는지 줄고 있는지 방향도 같이 봐요.",
      "내 기준: -20% 이하면 매수 준비(바닥을 찍고 올라올 때 절호의 기회) · 전년 대비 +40%(지난 사이클 고점), +50%(큰 사이클 고점) 이상이면 고점 신호 → 매도 준비.",
      "점수: +40 이상 5광기 · +20~40 4과열 · -20~+20 3중립 · -30~-20 2불안 · -30 이하 1공포"]),
    ("하이일드 스프레드 (일간)",
     ["신용등급이 낮은 회사채가 국채보다 더 주는 이자예요. 뉴스에 속지 않고 시장의 '진짜 위기감'을 보게 해줘요.",
      "3% 이하 대호황 · 5% 넘으면 1차 경계(주식 15~20% 하락, 몇 주~1~2개월 요동) · 7% 넘으면 2차 경계(20% 이상 하락, 침체급 쇼크, 막판 추가 급락 가능 → 모니터링).",
      "5% 밑에서 내려오기 시작하면 가짜 위기·조정 매수, 피크를 찍고 내려올 때(몇 %든) 적극 매수, 전고점을 못 넘고 내려오면 하향 안정이에요.",
      "점수: 3% 이하 5광기 · 바닥+1% 4과열 · 3~5% 박스 3중립 · 5% 위 상승세 2불안 · 5% 상승 후 1%p 하락 1공포",
      "※ 표의 '바닥+1%'는 기준이 애매해서 '최근 1년 최저점 +0.5%p 이내'로 해석했어요."]),
    ("VIX (일간)",
     ["향후 1개월 미국 증시 변동성에 대한 기대를 나타내는 공포지수예요. 5개 중 영향력이 가장 약하고, 하루에도 많이 움직여 헷갈리는 편이에요.",
      "내 기준: 30 이상 위기 충격 대비 · 20~30 고변동 · 20 이하 안정·강세장.",
      "점수: 15 이하 5광기 · 15~20 4과열 · 20~30 3중립 · 30~40 2불안 · 40 이상 1공포"]),
]


def contrarian_caption(c):
    if not c or c.get("total") is None:
        return None
    return f"역발상 판독기 {c['as_of'][5:7]}/{c['as_of'][8:10]} 기준 · 총점 {c['total']:.1f} {c['zone']} → {c['action']} ({c['count']}/5개 지표)"


def contrarian_blocks(c):
    if not c or not c.get("items"):
        return []
    d = c.get("as_of", "")
    head = [f"<b>[역발상 판독기 · 월/수/금]</b> {esc(d[5:7] + '/' + d[8:10])} 기준 · 5가지 지표를 1점(공포)~5점(광기)으로 매겨 평균을 냅니다"]
    if c.get("total") is None:
        head.append(f"총점: 집계 불가 (자동 수집된 지표가 {c.get('count', 0)}개뿐이라 3개 이상일 때만 계산)")
    else:
        head.append(f"총점 <b>{c['total']:.1f} · {esc(c['zone'])}</b> → 증시 매매 판단: <b>{esc(c['action'])}</b> ({c['count']}/5개 지표 반영)")
    for n, it in enumerate(c["items"], 1):
        if it.get("score") is None:
            head.append(f"\n{n}. {esc(it['name'])} ({esc(it['cycle'])}) — 미수집 (자동 수집 실패)")
            continue
        dd = it.get("date") or ""
        dd = dd[5:7] + "/" + dd[8:10] if len(dd) == 10 and dd[4] == "-" else dd
        tag = " · 수동 입력값" if it.get("manual") else ""
        head.append(f"\n{n}. {esc(it['name'])} ({esc(it['cycle'])}) <b>{esc(it['value_text'])}</b> → {it['score']}점 {esc(it['label'])}"
                    + (f"  [{esc(dd)} 기준{tag}]" if dd or tag else ""))
        if it.get("extra"):
            head.append("   ↳ " + esc(it["extra"]))
    head.append("\n총점 구간: 4.2 이상 광기(분할 매도) · 3.2~4.2 과열(매도 준비) · 2.6~3.2 중립(매매 보류) · 1.8~2.6 불안(분할 매수) · 1.8 미만 공포(역발상 매수, 장기)")
    help_lines = ["<b>[역발상 판독기 쉽게 읽는 법]</b> 판독기로 시장 쏠림을 먼저 확인한 뒤, 개별 종목은 이동평균선 점검 칸으로 대응합니다"]
    for n, (title, lines) in enumerate(CONTRARIAN_HELP, 1):
        help_lines.append(f"\n<b>{n}. {esc(title)}</b>")
        help_lines += ["• " + esc(x) for x in lines]
    help_lines.append("\n※ 총점 구간 숫자(4.2/3.2/2.6/1.8)는 표의 예시(3.2점=과열)에 맞춰 정한 값입니다. 점수는 높을수록 낙관이 쏠린 상태, 낮을수록 공포가 쏠린 상태예요.")
    return ["\n".join(head), "\n".join(help_lines)]


def watch_block(w):
    """목요일 흐름 체크: 그룹별 평균과 종목별 5거래일·선거 이후 등락."""
    if not w or not w.get("groups"):
        return None

    def pc(v):
        return "-" if v is None else f"{v:+.1f}%"

    def dot(v):
        return "⚪" if v is None else ("🟢" if v > 0 else ("🔴" if v < 0 else "⚪"))

    e = str(w.get("election_date", ""))[5:].replace("-", "/")
    head = [f"<b>[주간 흐름 체크 · 목]</b> {esc(str(w.get('as_of', ''))[5:].replace('-', '/'))} 종가 기준 · "
            f"최근 5거래일 / 선거({esc(e)} 종가) 이후 / 200일선 대비 %"]
    sm = w.get("summary") or {}
    if sm.get("strong") and sm.get("weak"):
        st = " · ".join(f"{x['emoji']}{esc(x['name'])} {pc(x['avg'])}" for x in sm["strong"])
        wk = " · ".join(f"{x['emoji']}{esc(x['name'])} {pc(x['avg'])}" for x in sm["weak"])
        head.append(f"강한 그룹: {st}")
        head.append(f"약한 그룹: {wk}")
    if sm.get("top") and sm.get("bottom"):
        head.append("가장 오른 종목: " + " · ".join(f"{esc(x['name'])} {pc(x['w'])}" for x in sm["top"]))
        head.append("가장 내린 종목: " + " · ".join(f"{esc(x['name'])} {pc(x['w'])}" for x in sm["bottom"]))
    if sm.get("cold"):
        head.append("🔔 200일선 -15% 이하(과냉·1차 매수 검토 구간): " +
                    " · ".join(f"{esc(x['name'])} {pc(x['d200'])}" for x in sm["cold"]))
    blocks = ["\n".join(head)]
    for g in w["groups"]:
        lines = [f"<b>{g['emoji']} {esc(g['name'])}</b> 평균 {pc(g['avg_w'])} / 선거 후 {pc(g['avg_e'])}",
                 f"   ↳ {esc(g['note'])}"]
        for i in g["items"]:
            star = " ⭐보유" if i.get("held") else ""
            d2 = i.get("d200")
            tail = f" / 200일선 {pc(d2)} {i.get('zone', '')}" if d2 is not None else " / 200일선 -"
            lines.append(f"• {dot(i['w'])} {esc(i['symbol'])} {esc(i['name'])}{star}  {pc(i['w'])} / {pc(i['e'])}{tail}")
        blocks.append("\n".join(lines))
    blocks.append("※ 그룹 분류와 설명은 참고용이에요. 같은 종목이라도 한 그룹에만 넣어 계산했어요. "
                  "'5거래일'은 일주일 전 종가 대비, '선거 이후'는 11/03 종가 대비 등락률이에요. "
                  "200일선은 일봉 200일 평균 대비 위치예요 (⬜중립 · 🟧과열 · 🟥광기 · 🟨불안 · 🟦공포).")
    return "\n\n".join(blocks)


def build_blocks(data, extras, weekly=None, ma=None, contrarian=None, watch=None):
    now = dt.datetime.now(KST)
    blocks = [f"<b>아침 시황 | {now.month}월 {now.day}일({WEEKDAYS[now.weekday()]})</b>\n{now:%H:%M} KST 기준으로 모은 정보입니다."]

    rows = data["rows"]
    ab = alert_block(extras)
    if ab:
        blocks.append(ab)
    nb = news_blocks(extras)  # 요약이 있으면 [요약, 뉴스 목록], 없으면 [뉴스 목록]
    if len(nb) == 2:
        blocks.append(nb[0])

    hb = holdings_block(extras)
    if hb:
        blocks.append(hb)
    fg = fear_greed_lines(rows, extras)
    if fg:
        blocks.append("<b>[시장 심리]</b>\n" + "\n".join(fg))

    groups, order = {}, []
    for row in rows:
        if "공포탐욕" in row["name"]:
            continue
        if row["group"] not in groups:
            groups[row["group"]] = []
            order.append(row["group"])
        groups[row["group"]].append(row)
    notes = {"유동성": "대부분 주 1회 발표라 며칠 전 숫자입니다", "금리": "장단기금리차는 하루 늦게 반영됩니다"}
    # 판독기가 오는 날(판독기에 하이일드 점수가 있을 때)은 하이일드 스프레드를 한 줄로 줄인다
    con_hy = any("하이일드" in str(it.get("name", "")) and it.get("score") is not None
                 for it in ((contrarian or {}).get("items") or []))
    for g in order:
        title = f"<b>[{esc(g)}]</b>" + (f" {notes[g]}" if g in notes else "")
        lines_g = []
        for r in groups[g]:
            ln = line(r, now)
            if con_hy and "하이일드" in r["name"]:
                ln = ln.split("\n")[0] + esc(" · 상세는 아래 판독기 참고")
            lines_g.append(ln)
        blocks.append("\n".join([title] + lines_g))

    # 요약은 위에서 이미 넣었으므로 뉴스 목록만 붙인다
    if nb:
        blocks.append(nb[-1])
    for b in (today_block(extras), week_block(extras), weekly_block(weekly), watch_block(watch), ma_block(ma), *contrarian_blocks(contrarian), cover_block(extras),
              month_block(extras), reminder_block(extras)):
        if b:
            blocks.append(b)

    # 숫자 쪽 오류 글에는 주소(=키)가 섞일 수 있어 이름만, 뉴스·일정 쪽은 오류 종류까지 보여준다
    missing = [f["name"] for f in (data.get("failures") or [])]
    missing += [f"{f['name']}({f['error']})" for f in ((extras or {}).get("failures") or [])]
    missing += [f"{f['name']}({f['error']})" for f in ((weekly or {}).get("failures") or [])]
    missing += [f"{f['name']}({f['error']})" for f in ((ma or {}).get("failures") or [])]
    missing += [f"{f['name']}({f['error']})" for f in ((contrarian or {}).get("failures") or [])]
    missing += [f"{f['name']}({f['error']})" for f in ((watch or {}).get("failures") or [])]
    if missing:
        blocks.append("가져오지 못한 항목: " + esc(", ".join(missing)))
    blocks.append("AI가 정리한 참고용입니다. 투자 판단은 본인이 합니다.")
    return blocks


def split_blocks(blocks):
    """블록(문단) 단위로 묶어 LIMIT 을 넘지 않게 나눈다. 링크 태그가 잘리지 않도록 줄 중간에서는 자르지 않는다."""
    chunks, cur = [], ""
    for b in blocks:
        pieces = [b]
        if len(b) > LIMIT:
            pieces, buf = [], ""
            for ln in b.split("\n"):
                if buf and len(buf) + len(ln) + 1 > LIMIT:
                    pieces.append(buf)
                    buf = ""
                buf += ln + "\n"
            if buf.strip():
                pieces.append(buf)
        for p in pieces:
            if cur and len(cur) + len(p) + 2 > LIMIT:
                chunks.append(cur)
                cur = ""
            cur += ("\n\n" if cur else "") + p
    if cur.strip():
        chunks.append(cur)
    return chunks


def post(token, payload):
    return requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=30)


def send(token, chat_id, text, html_mode=True, depth=0):
    """한 덩어리를 보낸다. 성공하면 True, 끝내 실패하면 False (멈추지 않고 다음 덩어리를 계속 보낸다).
    - 너무 자주 보냈다는 오류(429)·서버 오류(5xx)·인터넷 끊김은 잠깐 쉬고 최대 4번까지 다시 시도한다.
    - HTML 모양 오류면 태그를 지우고 일반 글로, 그래도 안 되면(글이 너무 길다 등) 반으로 쪼개 다시 보낸다."""
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if html_mode:
        payload["parse_mode"] = "HTML"
    r, reason = None, ""
    for attempt in range(4):
        try:
            r = post(token, payload)
        except requests.RequestException as e:
            # 오류 글에 주소(=토큰)가 섞일 수 있어서 종류만 출력한다
            print(f"전송 실패: 인터넷 연결 문제 ({type(e).__name__}) - 다시 시도 {attempt + 1}/4")
            time.sleep(3 * (attempt + 1))
            r = None
            continue
        if r.ok:
            return True
        try:
            js = r.json()
            reason = js.get("description", "")
            wait = int(js.get("parameters", {}).get("retry_after", 0))
        except (ValueError, AttributeError, TypeError):
            reason, wait = "", 0
        if r.status_code == 429 or r.status_code >= 500:
            wait = min(max(wait, 3), 60)
            print(f"전송 지연: HTTP {r.status_code} - {wait}초 쉬고 다시 시도 {attempt + 1}/4")
            time.sleep(wait)
            continue
        break
    if r is None:
        print("전송 실패: 인터넷 연결이 계속 안 됩니다.")
        return False
    if r.ok:
        return True
    if html_mode and r.status_code == 400:
        # 글 모양(HTML) 문제 등 → 태그를 지우고 일반 글로 다시 보낸다
        print(f"HTML 모양 오류({reason[:60]}) -> 일반 글로 다시 보냅니다.")
        plain = html.unescape(re.sub(r"<[^>]+>", "", text))
        return send(token, chat_id, plain, html_mode=False, depth=depth)
    if r.status_code == 400 and depth < 3 and "\n" in text.strip():
        # 글이 너무 길다 등 → 줄 단위로 반으로 쪼개 보낸다
        lines = text.strip().split("\n")
        mid = len(lines) // 2
        print(f"글을 반으로 나눠 다시 보냅니다. ({reason[:60]})")
        ok1 = send(token, chat_id, "\n".join(lines[:mid]), html_mode=html_mode, depth=depth + 1)
        ok2 = send(token, chat_id, "\n".join(lines[mid:]), html_mode=html_mode, depth=depth + 1)
        return ok1 and ok2
    print(f"전송 실패: HTTP {r.status_code} {reason}")
    if r.status_code in (400, 403):
        print("힌트: 채팅 번호가 맞는지, 봇에게 먼저 /start 를 보냈는지 확인하세요.")
    if r.status_code in (401, 404):
        print("힌트: 봇 토큰이 맞는지 확인하세요.")
    return False


def send_photo(token, chat_id, path, caption):
    """사진을 보낸다. 실패해도 멈추지 않는다(글은 그대로 나간다)."""
    try:
        with open(path, "rb") as fp:
            r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                              data={"chat_id": chat_id, "caption": caption[:1000]}, files={"photo": fp}, timeout=60)
        if not r.ok:
            print(f"그림 전송 실패: HTTP {r.status_code}")
    except (requests.RequestException, OSError) as e:
        print(f"그림 전송 실패: {type(e).__name__}")


def load(path):
    try:
        with open(path, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, ValueError):
        return None


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 가 GitHub 금고에 없습니다.")
        sys.exit(1)

    if "--test" in sys.argv:
        send(token, chat_id, "테스트 메시지입니다. 이 글이 보이면 연결 성공입니다.", html_mode=False)
        print("테스트 메시지를 보냈습니다.")
        return

    data = load("data.json")
    if data is None:
        print("data.json 이 없습니다. collect_data.py 가 먼저 돌아야 합니다.")
        sys.exit(1)
    extras = load("extras.json")
    weekly = load("weekly.json")
    today = dt.datetime.now(KST).strftime("%Y-%m-%d")
    if weekly and str(weekly.get("generated_at", ""))[:10] != today:
        weekly = None  # 오늘 만든 것만 쓴다
    ma = load("ma.json")
    if ma and str(ma.get("generated_at", ""))[:10] != today:
        ma = None
    con = load("contrarian.json")
    if con and str(con.get("generated_at", ""))[:10] != today:
        con = None
    wt = load("watch.json")
    if wt and str(wt.get("generated_at", ""))[:10] != today:
        wt = None
    cap = contrarian_caption(con)
    if con and cap and con.get("image") and os.path.exists(con["image"]):
        send_photo(token, chat_id, con["image"], cap)  # 월·수·금요일: 판독기 그림을 먼저 보낸다
    chunks = split_blocks(build_blocks(data, extras, weekly, ma, con, wt))
    failed = 0
    for i, chunk in enumerate(chunks):
        if i:
            time.sleep(1.5)  # 너무 빨리 연달아 보내면 텔레그램이 막는다
        if not send(token, chat_id, chunk):
            failed += 1
    print(f"텔레그램 전송: {len(chunks)}개 중 {len(chunks) - failed}개 성공")
    if failed:
        print(f"{failed}개 덩어리를 끝내 못 보냈습니다.")
        sys.exit(1)
    print("텔레그램으로 보냈습니다.")


if __name__ == "__main__":
    main()
