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
NEWS_ORDER = ["월가", "테크", "지정학", "정치", "코인", "주요 뉴스"]


def esc(s):
    return html.escape(str(s), quote=False)


def fmt_number(v, unit=""):
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
    text = f"• {row['name']} {fmt_number(row['value'], unit)}"
    if unit:
        text += unit if unit in ("%", "%p") else f" {unit}"
    if row.get("label"):
        text += f" ({row['label']})"
    if row["change"] is not None:
        sign = "+" if row["change"] >= 0 else ""
        if row["change_kind"] == "pct":
            text += f" ({sign}{row['change']:.2f}%)"
        else:
            digits = 3 if unit == "조 달러" else 2
            text += f" ({sign}{row['change']:.{digits}f})"
    if row["freq"] != "일간" or is_old(row, now):
        text += f" · {short_date(row['date'])} 기준"
    return esc(text)


def fear_greed_lines(rows, extras):
    out = []
    fg = (extras or {}).get("stock_fg")
    if fg:
        rating = RATING_KO.get(str(fg["rating"]).lower(), fg["rating"])
        out.append(
            f"• 주식 공포탐욕(CNN) {fg['score']:.0f} {esc(rating)} "
            f"(전일 {fg['prev_close']:.0f} · 1주 전 {fg['week']:.0f} · 1달 전 {fg['month']:.0f})"
        )
    for r in rows:
        if "공포탐욕" in r["name"]:
            text = f"• {esc(r['name'])} {r['value']:.0f}"
            if r.get("label"):
                text += f" ({esc(r['label'])})"
            if r["change"] is not None:
                text += f" ({'+' if r['change'] >= 0 else ''}{r['change']:.0f})"
            out.append(text)
    return out


def news_blocks(extras):
    news = (extras or {}).get("news")
    if not news or not news.get("items"):
        return []
    blocks = []
    if news.get("summary"):
        blocks.append("<b>한 줄 요약</b>\n" + esc(news["summary"]))
    by_cat = {}
    for it in news["items"]:
        by_cat.setdefault(it["category"], []).append(it)
    lines = ["<b>[밤사이 핵심 뉴스]</b> 🔴 꼭 확인 · 🟡 참고 · ⚪ 단기 소음 가능"]
    cats = [c for c in NEWS_ORDER if c in by_cat] + [c for c in by_cat if c not in NEWS_ORDER]
    for c in cats:
        lines.append(f"\n<b>{esc(c)}</b>")
        for it in by_cat[c]:
            mark = LEVEL_MARK.get(it["level"], "🟡")
            url = html.escape(it["url"], quote=True)
            lines.append(f'{mark} <a href="{url}">{esc(it["title"])}</a>')
    if news.get("mode") == "raw":
        lines.append("\n※ AI 선별이 안 돼 제목만 모았습니다.")
    blocks.append("\n".join(lines))
    return blocks


def event_line(e):
    text = f"• {e['time']} {esc(e['title'])}"
    extra = []
    if e.get("forecast"):
        extra.append(f"예상 {esc(e['forecast'])}")
    if e.get("previous"):
        extra.append(f"이전 {esc(e['previous'])}")
    if extra:
        text += " · " + " / ".join(extra)
    if e.get("impact") == "High":
        text += " ⭐"
    return text


def today_block(extras):
    if not extras:
        return None
    lines = ["<b>[오늘 주요 일정]</b> 한국시간, 앞으로 24시간"]
    events = extras.get("today_events") or []
    if events:
        lines.append("<b>경제지표·연준</b> (⭐=중요도 높음)")
        cur = None
        for e in events:
            if e["date"] != cur:
                cur = e["date"]
                lines.append(esc(cur))
            lines.append(event_line(e))
    earn = extras.get("earnings") or []
    if earn:
        lines.append(f"\n<b>실적 발표</b> (미국 날짜 {esc(extras.get('earnings_day', ''))})")
        for e in earn:
            text = f"• {esc(e['name'])} · {esc(e['when'])}"
            if e.get("eps_estimate") is not None:
                text += f" · 예상 EPS {e['eps_estimate']:.2f}"
            lines.append(text)
    if len(lines) == 1:
        lines.append("• 확인된 주요 일정이 없습니다.")
    return "\n".join(lines)


def week_block(extras):
    if not extras:
        return None
    events = extras.get("week_events") or []
    if not events:
        return None
    lines = ["<b>[이번 주 체크]</b> 중요도 높은 지표만, 한국시간"]
    cur = None
    for e in events:
        if e["date"] != cur:
            cur = e["date"]
            lines.append(esc(cur))
        lines.append(event_line(e).replace(" ⭐", ""))
    return "\n".join(lines)


def build_blocks(data, extras):
    now = dt.datetime.now(KST)
    blocks = [f"<b>아침 시황 | {now.month}월 {now.day}일({WEEKDAYS[now.weekday()]})</b>\n{now:%H:%M} KST 기준으로 모은 정보입니다."]

    rows = data["rows"]
    nb = news_blocks(extras)  # 요약이 있으면 [요약, 뉴스 목록], 없으면 [뉴스 목록]
    if len(nb) == 2:
        blocks.append(nb[0])

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
    notes = {"유동성": "주 1회 발표라 며칠 전 숫자입니다", "금리": "장단기금리차는 하루 늦게 반영됩니다"}
    for g in order:
        title = f"<b>[{esc(g)}]</b>" + (f" {notes[g]}" if g in notes else "")
        blocks.append("\n".join([title] + [line(r, now) for r in groups[g]]))

    # 요약은 위에서 이미 넣었으므로 뉴스 목록만 붙인다
    if nb:
        blocks.append(nb[-1])
    for b in (today_block(extras), week_block(extras)):
        if b:
            blocks.append(b)

    missing = [f["name"] for f in (data.get("failures") or [])] + [f["name"] for f in ((extras or {}).get("failures") or [])]
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
    for chunk in split_blocks(build_blocks(data, extras)):
        send(token, chat_id, chunk)
    print("텔레그램으로 보냈습니다.")


if __name__ == "__main__":
    main()
