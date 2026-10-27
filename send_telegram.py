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
import json
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


KIND_MARK = {"지표": "📊", "정책": "🏛", "발언": "🎤", "실적": "💼", "이벤트": "📌"}
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


def build_blocks(data, extras, weekly=None):
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
    for g in order:
        title = f"<b>[{esc(g)}]</b>" + (f" {notes[g]}" if g in notes else "")
        blocks.append("\n".join([title] + [line(r, now) for r in groups[g]]))

    # 요약은 위에서 이미 넣었으므로 뉴스 목록만 붙인다
    if nb:
        blocks.append(nb[-1])
    for b in (today_block(extras), week_block(extras), weekly_block(weekly), cover_block(extras),
              month_block(extras), reminder_block(extras)):
        if b:
            blocks.append(b)

    # 숫자 쪽 오류 글에는 주소(=키)가 섞일 수 있어 이름만, 뉴스·일정 쪽은 오류 종류까지 보여준다
    missing = [f["name"] for f in (data.get("failures") or [])]
    missing += [f"{f['name']}({f['error']})" for f in ((extras or {}).get("failures") or [])]
    missing += [f"{f['name']}({f['error']})" for f in ((weekly or {}).get("failures") or [])]
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


def send(token, chat_id, text, html_mode=True):
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if html_mode:
        payload["parse_mode"] = "HTML"
    try:
        r = post(token, payload)
    except requests.RequestException as e:
        # 오류 글에 주소(=토큰)가 섞일 수 있어서 종류만 출력한다
        print(f"전송 실패: 인터넷 연결 문제 ({type(e).__name__})")
        sys.exit(1)
    if r.ok:
        return
    try:
        reason = r.json().get("description", "")
    except ValueError:
        reason = ""
    if html_mode and r.status_code == 400 and "parse" in reason.lower():
        # 글 모양(HTML) 문제면 태그를 지우고 일반 글로 다시 보낸다
        print("HTML 모양 오류 -> 일반 글로 다시 보냅니다.")
        plain = html.unescape(re.sub(r"<[^>]+>", "", text))
        return send(token, chat_id, plain, html_mode=False)
    print(f"전송 실패: HTTP {r.status_code} {reason}")
    if r.status_code in (400, 403):
        print("힌트: 채팅 번호가 맞는지, 봇에게 먼저 /start 를 보냈는지 확인하세요.")
    if r.status_code in (401, 404):
        print("힌트: 봇 토큰이 맞는지 확인하세요.")
    sys.exit(1)


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
    for chunk in split_blocks(build_blocks(data, extras, weekly)):
        send(token, chat_id, chunk)
    print("텔레그램으로 보냈습니다.")


if __name__ == "__main__":
    main()
