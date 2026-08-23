#!/usr/bin/env python3
"""
네이버 항공 다중 노선/날짜(+시간대) 감시 — GitHub Actions에서 주기 실행(백업용, 느림).
각 타겟이 매진(0편)→좌석 등장(N편)으로 바뀌는 순간만 텔레그램 알림. 중복 방지는 state.json.

빠른 취소표 사냥은 vm/sniper.py(초 단위 데몬)가 담당. 이 파일은 VM이 죽어도 돌아가는 느린 백업.

환경변수:
  TELEGRAM_BOT_TOKEN  (필수)
  TELEGRAM_CHAT_ID    (필수)
  TARGETS  (선택) 콤마구분. "출발:날짜" 또는 "출발:날짜:시작-끝(HHMM)".
           예) "GMP:20260924,ICN:20260924,GMP:20260923:1700-2359,ICN:20260923:1700-2359"
           기본 "GMP:20260924,ICN:20260924"
  ARR      (선택) 기본 CJU
  ADULT    (선택) 기본 1
"""
import json
import os
import sys
import time
from pathlib import Path

from curl_cffi import requests

ARR = os.environ.get("ARR", "CJU")
ADULT = int(os.environ.get("ADULT", "1"))
STATE_FILE = Path(__file__).with_name("state.json")
API = "https://flight-api.naver.com/flight/domestic/searchFlights"


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
    return win[0] <= time_str <= win[1]


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
    last_err = None
    for _ in range(3):
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
    print(f"[warn] {key(t)} failed: {last_err}", file=sys.stderr)
    return None


def summarize(d: dict, t: dict) -> dict:
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
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=30,
    )
    if r.status_code != 200:
        print(f"[error] telegram {r.status_code}: {r.text[:300]}", file=sys.stderr)
        r.raise_for_status()


def alert_seat(t: dict, s: dict) -> None:
    lines = [f"🚨 <b>취소표 발견!</b> {target_label(t)}",
             f"{s['count']}편 · 최저 <b>{won(s['min'])}</b>"]
    for it in s["items"][:5]:
        seat = f" (잔여 {it['seats']})" if it.get("seats") is not None else ""
        lines.append(f"• {it['time'][:2]}:{it['time'][2:]} {it['airline']} {it['flightno']} {won(it['fare'])}{seat}")
    lines.append(f'👉 <a href="{booking_url(t)}">지금 예약</a>')
    telegram("\n".join(lines))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    state = load_state()
    first_run = not state
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    results = {}
    for t in TARGETS:
        d = search(t)
        if d is None:
            continue
        results[key(t)] = (t, summarize(d, t))
        time.sleep(0.5)

    if not results:
        print(f"[{ts}] inconclusive (all requests failed) — skipping.")
        return 0

    if first_run:
        rows = []
        for t in TARGETS:
            r = results.get(key(t))
            if not r:
                rows.append(f"• {target_label(t)}: 조회실패")
                continue
            s = r[1]
            rows.append(f"• {target_label(t)}: " + (f"{s['count']}편 최저 {won(s['min'])}" if s["count"] else "표없음"))
        telegram("✈️ <b>항공권 감시 시작</b> (백업·15분 주기)\n" + "\n".join(rows) +
                 "\n표가 뜨면 알립니다. (초단위 감시는 VM 스나이퍼)")
        print(f"[{ts}] first run — sent start notice.")

    for k, (t, s) in results.items():
        prev = state.get(k, {})
        eff_avail = s["count"] > 0
        if eff_avail and not prev.get("available") and not first_run:
            alert_seat(t, s)
            print(f"[{ts}] ALERT {k} count={s['count']} min={s['min']}")
        state[k] = {"available": eff_avail, "count": s["count"], "min": s["min"], "checked_at_utc": ts}

    save_state(state)
    print(f"[{ts}] checked {len(results)}/{len(TARGETS)} targets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
