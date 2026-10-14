"""
아침 시황 - 텔레그램으로 보내기

하는 일
  1) collect_data.py 가 만든 data.json 을 읽는다
  2) 보기 좋은 글로 정리한다
  3) 텔레그램 봇으로 내 채팅에 보낸다

필요한 것 (GitHub 금고에 저장, 이 파일에 직접 쓰지 않는다)
  - TELEGRAM_BOT_TOKEN : BotFather가 준 봇 토큰
  - TELEGRAM_CHAT_ID   : 내 채팅 번호
"""
import datetime as dt
import json
import os
import sys

import requests

KST = dt.timezone(dt.timedelta(hours=9))
WEEKDAYS = "월화수목금토일"
LIMIT = 4000  # 텔레그램 한 번에 보낼 수 있는 글자(4096)보다 조금 작게


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


def line(row):
    unit = row["unit"]
    if "공포탐욕" in row["name"]:  # 0~100 지수는 정수로 보여준다
        text = f"• {row['name']} {row['value']:.0f}"
        if row.get("label"):
            text += f" ({row['label']})"
        if row["change"] is not None:
            text += f" ({'+' if row['change'] >= 0 else ''}{row['change']:.0f})"
        return text
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
    if row["freq"] != "일간":
        text += f" · {short_date(row['date'])} 기준"
    return text


def build_message(data):
    now = dt.datetime.now(KST)
    head = f"아침 시황 | {now.month}월 {now.day}일({WEEKDAYS[now.weekday()]})"
    parts = [head, f"{now:%H:%M} KST 기준으로 모은 숫자입니다."]

    groups, order = {}, []
    for row in data["rows"]:
        if row["group"] not in groups:
            groups[row["group"]] = []
            order.append(row["group"])
        groups[row["group"]].append(row)

    notes = {"유동성": "주 1회 발표라 며칠 전 숫자입니다", "금리": "하루 늦게 반영됩니다"}
    for g in order:
        title = f"\n[{g}]" + (f" {notes[g]}" if g in notes else "")
        parts.append(title)
        parts.extend(line(r) for r in groups[g])

    failures = data.get("failures") or []
    if failures:
        parts.append("\n가져오지 못한 항목: " + ", ".join(f["name"] for f in failures))

    parts.append("\n숫자만 정리한 버전입니다. 투자 판단은 본인이 합니다.")
    return "\n".join(parts)


def split_message(text):
    """4000자를 넘으면 줄 단위로 나눠 여러 번에 보낸다."""
    if len(text) <= LIMIT:
        return [text]
    chunks, cur = [], ""
    for ln in text.split("\n"):
        if len(cur) + len(ln) + 1 > LIMIT:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur.strip():
        chunks.append(cur)
    return chunks


def send(token, chat_id, text):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
    except requests.RequestException as e:
        # 오류 글에 주소(=토큰)가 섞일 수 있어서 종류만 출력한다
        print(f"전송 실패: 인터넷 연결 문제 ({type(e).__name__})")
        sys.exit(1)
    if not r.ok:
        try:
            reason = r.json().get("description", "")
        except ValueError:
            reason = ""
        print(f"전송 실패: HTTP {r.status_code} {reason}")
        if r.status_code in (400, 403):
            print("힌트: 채팅 번호가 맞는지, 봇에게 먼저 /start 를 보냈는지 확인하세요.")
        if r.status_code in (401, 404):
            print("힌트: 봇 토큰이 맞는지 확인하세요.")
        sys.exit(1)


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 가 GitHub 금고에 없습니다.")
        sys.exit(1)

    if "--test" in sys.argv:
        send(token, chat_id, "테스트 메시지입니다. 이 글이 보이면 연결 성공입니다.")
        print("테스트 메시지를 보냈습니다.")
        return

    with open("data.json", encoding="utf-8") as fp:
        data = json.load(fp)
    for chunk in split_message(build_message(data)):
        send(token, chat_id, chunk)
    print("텔레그램으로 보냈습니다.")


if __name__ == "__main__":
    main()
