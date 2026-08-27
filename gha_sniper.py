#!/usr/bin/env python3
"""
GitHub Actions 자기반복 스나이퍼 러너.

한 번 실행되면 RUN_SECONDS 동안 vm/sniper.py 의 감시 로직을 그대로 돌린다
(0→N 전이 시 텔레그램 즉시 알림). 시간이 다 되면 정상 종료하고,
워크플로우가 스스로를 다시 트리거해 무한 지속한다.

state는 gha_state.json 에 저장하고 워크플로우가 커밋으로 지속시킨다.

환경변수: vm/sniper.py 와 동일 + RUN_SECONDS(기본 20700 = 5h45m)
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vm"))

# state 파일을 레포 루트로 지정 (워크플로우가 커밋)
os.environ.setdefault("STATE_FILE", os.path.join(os.path.dirname(__file__), "gha_state.json"))
# GHA에선 시작 배너를 매 재실행마다 보내면 시끄러우므로 억제 플래그 사용
os.environ.setdefault("SUPPRESS_START_BANNER", "1")

import sniper  # noqa: E402  (vm/sniper.py)

RUN_SECONDS = int(os.environ.get("RUN_SECONDS", "20700"))  # 5h45m


def main() -> int:
    deadline = time.time() + RUN_SECONDS
    state = sniper.load_state()

    # 시작 배너: 최초(state 비었을 때)만 1회. 재실행 땐 조용.
    first_ever = not state
    if first_ever and os.environ.get("SUPPRESS_START_BANNER") != "1":
        pass  # (억제됨)
    if first_ever:
        targets_str = " / ".join(sniper.target_label(t) for t in sniper.TARGETS)
        sniper.telegram(
            f"🎯 <b>취소표 스나이퍼 가동 (GitHub 클라우드)</b>\n"
            f"감시: {targets_str}\n간격: {sniper.POLL_INTERVAL}초\n"
            f"좌석(취소표) 뜨면 즉시 알립니다."
        )

    print(f"[gha] start; run for {RUN_SECONDS}s; targets={len(sniper.TARGETS)}", flush=True)
    polls = 0
    while time.time() < deadline:
        for t in sniper.TARGETS:
            k = sniper.key(t)
            d = sniper.search(t)
            if d is None:
                continue
            s = sniper.summarize(d, t)
            prev = state.get(k, {})
            prev_avail = prev.get("available", False)
            eff = s["count"] > 0 and (sniper.PRICE_MAX == 0 or (s["min"] and s["min"] <= sniper.PRICE_MAX))
            now = time.time()
            fire = (eff and not prev_avail)
            if eff and prev_avail and sniper.REALERT_MINUTES > 0 and now - prev.get("last_alert", 0) >= sniper.REALERT_MINUTES * 60:
                fire = True
            if fire:
                sniper.alert_seat(t, s)
                print(f"[{time.strftime('%H:%M:%S')}] ALERT {k} count={s['count']} min={s['min']}", flush=True)
            state[k] = {"available": eff, "last_alert": now if fire else prev.get("last_alert", 0),
                        "count": s["count"], "min": s["min"]}
            sniper.save_state(state)
            time.sleep(0.4)
        polls += 1
        if polls % 20 == 0:
            print(f"[gha] {polls} polls; {int(deadline - time.time())}s left", flush=True)
        time.sleep(sniper.POLL_INTERVAL)

    print(f"[gha] window done after {polls} polls; will re-trigger.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
