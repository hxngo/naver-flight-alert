#!/usr/bin/env bash
# 취소표 스나이퍼 VM 설치 스크립트 (Ubuntu 22.04/24.04 기준, root 또는 sudo로 실행)
#
# 사용법 (VM에서):
#   TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... \
#   TARGETS="GMP:20260924,ICN:20260924" POLL_INTERVAL=25 \
#   sudo -E bash install.sh
#
# 이후 관리:
#   systemctl status naver-sniper      # 상태
#   journalctl -u naver-sniper -f      # 실시간 로그
#   systemctl restart naver-sniper     # 재시작
#   systemctl disable --now naver-sniper  # 중지
set -euo pipefail

: "${TELEGRAM_BOT_TOKEN:?TELEGRAM_BOT_TOKEN 필요}"
: "${TELEGRAM_CHAT_ID:?TELEGRAM_CHAT_ID 필요}"
TARGETS="${TARGETS:-GMP:20260924,ICN:20260924}"
POLL_INTERVAL="${POLL_INTERVAL:-25}"
PRICE_MAX="${PRICE_MAX:-0}"
HEARTBEAT_HOURS="${HEARTBEAT_HOURS:-12}"
REALERT_MINUTES="${REALERT_MINUTES:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/naver-sniper

echo "[1/5] apt deps"
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip >/dev/null

echo "[2/5] app dir + venv"
mkdir -p "$APP_DIR"
cp "$SCRIPT_DIR/sniper.py" "$APP_DIR/sniper.py"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

echo "[3/5] env file (/etc/naver-sniper.env)"
umask 077
cat > /etc/naver-sniper.env <<EOF
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
TARGETS=${TARGETS}
ARR=CJU
ADULT=1
POLL_INTERVAL=${POLL_INTERVAL}
PRICE_MAX=${PRICE_MAX}
HEARTBEAT_HOURS=${HEARTBEAT_HOURS}
REALERT_MINUTES=${REALERT_MINUTES}
STATE_FILE=${APP_DIR}/state.json
EOF

echo "[4/5] systemd service"
cp "$SCRIPT_DIR/naver-sniper.service" /etc/systemd/system/naver-sniper.service
systemctl daemon-reload
systemctl enable naver-sniper >/dev/null 2>&1 || true
systemctl restart naver-sniper

echo "[5/5] done"
sleep 2
systemctl --no-pager status naver-sniper | head -12 || true
echo
echo "실시간 로그:  journalctl -u naver-sniper -f"
