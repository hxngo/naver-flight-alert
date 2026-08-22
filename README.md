# naver-flight-alert

네이버 항공에서 특정 노선·날짜의 항공권이 **처음 풀리는 순간** 텔레그램으로 알려주는 감시기.

- 감시 대상(기본): **서울(GMP+ICN) → 제주(CJU) · 편도 · 성인 1명 · 2026-09-24**
- GitHub Actions가 **15분마다 클라우드에서 자동 실행** (Mac 꺼져 있어도 동작)
- 표가 없다가 생기는 순간(`flights[]`가 채워지는 순간) 1회 알림

## 동작 원리

`check_flights.py`가 네이버 국내선 검색 API(`flight-api.naver.com/flight/domestic/searchFlights`)를
크롬 TLS 지문(`curl_cffi`)으로 호출해 결과를 읽는다. `state.json`에 직전 상태를 저장해
"없음 → 있음" 전이일 때만 알림을 보내 중복 알림을 막는다.

## 셋업

1. 텔레그램 봇 생성: [@BotFather](https://t.me/BotFather) → `/newbot` → 봇 토큰 확보
2. 봇과 대화방을 열고 아무 메시지나 전송 → chat id 확인
3. 이 레포 Settings → Secrets and variables → Actions 에 추가:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. Actions 탭에서 `naver-flight-watch` → **Run workflow** 로 첫 실행(시작 알림 수신 확인)

## 감시 대상 바꾸기

`.github/workflows/watch.yml` 의 `env` 에서 `WATCH_DATE` / `WATCH_DEP` / `WATCH_ARR` / `WATCH_ADULT`
주석을 풀고 값을 수정한다. (날짜는 `YYYYMMDD`)

## 로컬 테스트

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
python check_flights.py
```
