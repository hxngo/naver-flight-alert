#!/usr/bin/env python3
"""
네이버 항공 특정 노선/날짜에 항공권이 풀리는 순간을 감지해 텔레그램으로 알림.

기본 감시 대상: 서울(GMP+ICN) -> 제주(CJU) / 편도 / 성인 1명 / 2026-09-24
표가 아직 안 풀린 상태(flights=[], 최저가 0원)에서 처음 채워지는 순간 알림 1회 발송.

환경변수:
  TELEGRAM_BOT_TOKEN  (필수) 텔레그램 봇 토큰
  TELEGRAM_CHAT_ID    (필수) 알림 받을 chat id
  WATCH_DATE          (선택) YYYYMMDD, 기본 20260924
  WATCH_DEP           (선택) 콤마구분 출발공항, 기본 "GMP,ICN"
  WATCH_ARR           (선택) 도착공항, 기본 "CJU"
  WATCH_ADULT         (선택) 성인 수, 기본 1
"""
import json
import os
import sys
import time
from pathlib import Path

from curl_cffi import requests

DATE = os.environ.get("WATCH_DATE", "20260924")
DEP_AIRPORTS = [a.strip() for a in os.environ.get("WATCH_DEP", "GMP,ICN").split(",") if a.strip()]
ARR = os.environ.get("WATCH_ARR", "CJU")
ADULT = int(os.environ.get("WATCH_ADULT", "1"))

STATE_FILE = Path(__file__).with_name("state.json")
API = "https://flight-api.naver.com/flight/domestic/searchFlights"


def booking_url(dep: str) -> str:
    return f"https://flight.naver.com/flights/domestic/{dep}-{ARR}-{DATE}?adult={ADULT}"


def search(dep: str) -> dict:
    """네이버 국내선 검색 API 호출 (SSE 응답의 마지막 완료 이벤트를 파싱)."""
    body = {
        "type": "domestic", "device": "pc", "fareType": "YC",
        "itineraries": [{"departureAirport": dep, "arrivalAirport": ARR, "departureDate": DATE}],
        "person": {"adult": ADULT, "child": 0, "infant": 0}, "tripType": "OW",
        "flightFilter": {"filter": {"type": "departure"}, "limit": 50, "skip": 0,
                         "sort": {"segment.departure.time": 1, "minFare": 1}},
        "initialRequest": True,
    }
    headers = {
        "content-type": "application/json", "accept": "text/event-stream",
        "origin": "https://flight.naver.com", "referer": booking_url(dep),
    }
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(API, json=body, headers=headers, impersonate="chrome", timeout=45)
            if r.status_code not in (200, 201):
                last_err = f"HTTP {r.status_code}"
                time.sleep(3)
                continue
            events = [ln[6:] for ln in r.text.splitlines() if ln.startswith("data: ")]
            if not events:
                last_err = "empty SSE"
                time.sleep(3)
                continue
            return json.loads(events[-1])
        except Exception as e:  # noqa: BLE001
            last_err = repr(e)
            time.sleep(3)
    raise RuntimeError(f"{dep}->{ARR} search failed: {last_err}")


def collect() -> dict:
    """모든 출발공항을 조회해 종합. 전부 실패하면 inconclusive=True."""
    total = 0
    min_price = None
    airlines = set()
    per_dep = {}
    errors = 0
    for dep in DEP_AIRPORTS:
        try:
            d = search(dep)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] {e}", file=sys.stderr)
            per_dep[dep] = {"error": str(e)}
            errors += 1
            continue
        st = d.get("status", {}) or {}
        dep_st = st.get("departure", {}) or {}
        price = dep_st.get("price", {}) or {}
        codemap = st.get("airlinesCodeMap", {}) or {}
        flights = d.get("flights", []) or []
        n = len(flights)
        mn = price.get("min") or 0
        total += n
        if mn and (min_price is None or mn < min_price):
            min_price = mn
        for code in dep_st.get("airlines", []) or []:
            airlines.add(codemap.get(code, code))
        per_dep[dep] = {"count": n, "min": mn}
        time.sleep(0.5)
    inconclusive = errors == len(DEP_AIRPORTS)
    return {
        "available": total > 0,
        "total": total,
        "min_price": min_price,
        "airlines": sorted(airlines),
        "per_dep": per_dep,
        "inconclusive": inconclusive,
    }


def telegram(text: str) -> None:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": False},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"[error] telegram {r.status_code}: {r.text[:300]}", file=sys.stderr)
        r.raise_for_status()


def won(n) -> str:
    return f"{n:,}원" if n else "정보없음"


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def route_label() -> str:
    return f"{'+'.join(DEP_AIRPORTS)}→{ARR}"


def date_label() -> str:
    return f"{DATE[:4]}.{DATE[4:6]}.{DATE[6:]}"


def main() -> int:
    prev = load_state()
    first_run = "available" not in prev
    cur = collect()
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    if cur["inconclusive"]:
        # 네이버 응답을 하나도 못 받음 -> 상태 갱신/알림 없이 종료 (오탐 방지)
        print(f"[{ts}] inconclusive (all requests failed) — skipping.")
        return 0

    link = booking_url(DEP_AIRPORTS[0])

    if first_run:
        # 최초 실행: 배선 확인용 시작 알림
        status_line = (
            f"현재 표 {cur['total']}편 있음 (최저 {won(cur['min_price'])})"
            if cur["available"] else "현재 표 없음 (아직 안 풀림)"
        )
        telegram(
            f"✈️ <b>항공권 감시 시작</b>\n"
            f"노선: {route_label()} · 편도 · 성인 {ADULT}\n"
            f"날짜: {date_label()}\n"
            f"{status_line}\n"
            f"표가 풀리면 바로 알려드릴게요.\n"
            f'<a href="{link}">네이버 항공에서 보기</a>'
        )
        print(f"[{ts}] first run — sent start notice. available={cur['available']}")

    elif cur["available"] and not prev.get("available"):
        # 전이: 없음 -> 있음  (표 풀림!)
        airlines = ", ".join(cur["airlines"]) if cur["airlines"] else "-"
        telegram(
            f"🎉 <b>항공권이 풀렸습니다!</b>\n"
            f"노선: {route_label()} · 편도 · 성인 {ADULT}\n"
            f"날짜: {date_label()}\n"
            f"편수: {cur['total']}편 · 최저가: <b>{won(cur['min_price'])}</b>\n"
            f"항공사: {airlines}\n"
            f'👉 <a href="{link}">지금 네이버 항공에서 예약</a>'
        )
        print(f"[{ts}] ALERT sent — tickets opened. total={cur['total']} min={cur['min_price']}")

    else:
        print(f"[{ts}] no change. available={cur['available']} total={cur['total']}")

    save_state({
        "available": cur["available"],
        "total": cur["total"],
        "min_price": cur["min_price"],
        "airlines": cur["airlines"],
        "per_dep": cur["per_dep"],
        "checked_at_utc": ts,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
