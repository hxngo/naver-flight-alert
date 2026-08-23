#!/usr/bin/env python3
"""
취소표 스나이퍼 — 네이버 항공 특정 노선/날짜(+시간대)를 초 단위로 감시.
매진(0편) 상태에서 좌석(취소표)이 뜨는 순간(0→N 전이) 텔레그램으로 즉시 알림.

VM에서 systemd 데몬으로 상시 실행. 내부 무한루프로 POLL_INTERVAL 초마다 폴링.

환경변수:
  TELEGRAM_BOT_TOKEN  (필수)
  TELEGRAM_CHAT_ID    (필수)
  TARGETS             (선택) 콤마구분. 각 항목은 "출발:날짜" 또는 "출발:날짜:시작-끝(HHMM)".
                      예) "GMP:20260924,ICN:20260924,GMP:20260923:1700-2359"
                      시간대를 주면 그 출발시간 범위의 편만 알림(예: 저녁). 기본 "GMP:20260924,ICN:20260924"
  ARR                 (선택) 도착공항, 기본 CJU
  ADULT               (선택) 성인 수, 기본 1
  POLL_INTERVAL       (선택) 폴링 간격(초), 기본 25
  PRICE_MAX           (선택) 이 가격(원) 이하만 알림. 0=제한없음(기본)
  HEARTBEAT_HOURS     (선택) 생존 확인 핑 주기(시간), 기본 12. 0=끔
  REALERT_MINUTES     (선택) 좌석이 계속 남아있을 때 재알림 간격(분), 기본 0(재알림 안함)
"""
import json
import os
import random
import sys
import time
from pathlib import Path

from curl_cffi import requests

ARR = os.environ.get("ARR", "CJU")
ADULT = int(os.environ.get("ADULT", "1"))
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "25"))
PRICE_MAX = int(os.environ.get("PRICE_MAX", "0"))
HEARTBEAT_HOURS = float(os.environ.get("HEARTBEAT_HOURS", "12"))
REALERT_MINUTES = float(os.environ.get("REALERT_MINUTES", "0"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "/opt/naver-sniper/state.json"))
API = "https://flight-api.naver.com/flight/domestic/searchFlights"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]


def parse_targets() -> list[dict]:
    raw = os.environ.get("TARGETS", "GMP:20260924,ICN:20260924")
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        parts = [p.strip() for p in tok.split(":")]
        dep, date = parts[0], parts[1]
        win = None
        if len(parts) >= 3 and parts[2]:
            a, b = parts[2].split("-")
            win = (a.strip(), b.strip())
        out.append({"dep": dep, "date": date, "win": win})
    return out


TARGETS = parse_targets()


def booking_url(t: dict) -> str:
    return f"https://flight.naver.com/flights/domestic/{t['dep']}-{ARR}-{t['date']}?adult={ADULT}"


def key(t: dict) -> str:
    w = f"@{t['win'][0]}-{t['win'][1]}" if t.get("win") else ""
    return f"{t['dep']}-{ARR}-{t['date']}{w}"


def target_label(t: dict) -> str:
    base = f"{t['dep']}→{ARR} {t['date'][4:6]}.{t['date'][6:]}"
    if t.get("win"):
        a, b = t["win"]
        return f"{base} {a[:2]}:{a[2:]}~{b[:2]}:{b[2:]}"
    return base


def won(n) -> str:
    return f"{n:,}원" if n else "-"


def in_window(time_str: str, win) -> bool:
    if not win:
        return True
    return win[0] <= time_str <= win[1]  # "HHMM" 고정폭이라 문자열 비교로 충분


def search(t: dict) -> dict | None:
    body = {
        "type": "domestic", "device": "pc", "fareType": "YC",
        "itineraries": [{"departureAirport": t["dep"], "arrivalAirport": ARR, "departureDate": t["date"]}],
        "person": {"adult": ADULT, "child": 0, "infant": 0}, "tripType": "OW",
        "flightFilter": {"filter": {"type": "departure"}, "limit": 50, "skip": 0,
                         "sort": {"minFare": 1, "segment.departure.time": 1}},
        "initialRequest": True,
    }
    headers = {
        "content-type": "application/json", "accept": "text/event-stream",
        "origin": "https://flight.naver.com", "referer": booking_url(t),
    }
    try:
        r = requests.post(API, json=body, headers=headers, impersonate="chrome", timeout=30)
        if r.status_code not in (200, 201):
            print(f"[warn] {key(t)} HTTP {r.status_code}", flush=True)
            return None
        events = [ln[6:] for ln in r.text.splitlines() if ln.startswith("data: ")]
        if not events:
            return None
        return json.loads(events[-1])
    except Exception as e:  # noqa: BLE001
        print(f"[warn] {key(t)} {e!r}", flush=True)
        return None


def summarize(d: dict, t: dict) -> dict:
    """응답을 요약하되 타겟의 시간대 필터를 적용."""
    st = d.get("status", {}) or {}
    cmap = st.get("airlinesCodeMap", {}) or {}
    items = []
    for f in d.get("flights", []) or []:
        seg = f.get("segment", {}) or {}
        dep = seg.get("departure", {}) or {}
        tm = dep.get("time", "----")
        if not in_window(tm, t.get("win")):
            continue
        items.append({
            "time": tm,
            "airline": cmap.get(seg.get("airlineCode"), seg.get("airlineCode", "?")),
            "flightno": seg.get("flightNumber", ""),
            "fare": f.get("minFare") or 0,
            "seats": f.get("seatCount"),
        })
    items.sort(key=lambda x: x["fare"] or 10**9)
    mn = next((it["fare"] for it in items if it["fare"]), 0)
    return {"count": len(items), "min": mn, "items": items}


def telegram(text: str) -> None:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[error] telegram {r.status_code}: {r.text[:200]}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[error] telegram send failed: {e!r}", flush=True)


def alert_seat(t: dict, s: dict) -> None:
    lines = [f"🚨 <b>취소표 발견!</b> {target_label(t)}",
             f"{s['count']}편 · 최저 <b>{won(s['min'])}</b>"]
    for it in s["items"][:5]:
        seat = f" (잔여 {it['seats']})" if it.get("seats") is not None else ""
        lines.append(f"• {it['time'][:2]}:{it['time'][2:]} {it['airline']} {it['flightno']} {won(it['fare'])}{seat}")
    lines.append(f'👉 <a href="{booking_url(t)}">지금 예약</a>')
    lines.append("⚡ 취소표는 초 단위로 사라집니다 — 바로 결제!")
    telegram("\n".join(lines))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"[warn] save_state: {e!r}", flush=True)


def main() -> int:
    state = load_state()  # key -> {"available": bool, "last_alert": epoch, ...}
    targets_str = " / ".join(target_label(t) for t in TARGETS)
    price_note = f" · {won(PRICE_MAX)} 이하만" if PRICE_MAX else ""
    telegram(
        f"🎯 <b>취소표 스나이퍼 가동</b>\n"
        f"감시: {targets_str}\n"
        f"간격: {POLL_INTERVAL}초{price_note}\n"
        f"좌석(취소표) 뜨면 즉시 알립니다."
    )
    print(f"[start] targets={targets_str} interval={POLL_INTERVAL}s price_max={PRICE_MAX}", flush=True)

    last_heartbeat = time.time()
    while True:
        for t in TARGETS:
            k = key(t)
            d = search(t)
            if d is None:
                continue  # 오류 → 상태 유지, 오탐 방지
            s = summarize(d, t)
            prev = state.get(k, {})
            prev_avail = prev.get("available", False)
            eff_avail = s["count"] > 0 and (PRICE_MAX == 0 or (s["min"] and s["min"] <= PRICE_MAX))

            now = time.time()
            fire = False
            if eff_avail and not prev_avail:
                fire = True  # 0 -> N 전이 (취소표 등장)
            elif eff_avail and prev_avail and REALERT_MINUTES > 0:
                if now - prev.get("last_alert", 0) >= REALERT_MINUTES * 60:
                    fire = True

            if fire:
                alert_seat(t, s)
                print(f"[{time.strftime('%H:%M:%S')}] ALERT {k} count={s['count']} min={s['min']}", flush=True)

            state[k] = {"available": eff_avail,
                        "last_alert": now if fire else prev.get("last_alert", 0),
                        "count": s["count"], "min": s["min"]}
            save_state(state)
            time.sleep(0.5)

        if HEARTBEAT_HOURS > 0 and time.time() - last_heartbeat >= HEARTBEAT_HOURS * 3600:
            summary = " / ".join(f"{target_label(t)}:{state.get(key(t),{}).get('count',0)}편" for t in TARGETS)
            telegram(f"💤 감시중 (이상무). {summary}")
            last_heartbeat = time.time()

        time.sleep(POLL_INTERVAL * random.uniform(0.85, 1.15))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("stopped", flush=True)
