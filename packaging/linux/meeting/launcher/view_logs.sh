#!/usr/bin/env bash
# Print recent meeting-minutes bot logs for operators.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOGS_ROOT="${PACKAGE_ROOT}/程序/logs/meeting_minutes"
MAIN_LOG="${LOGS_ROOT}/meeting_minutes_bot.log"
STDOUT_PATH="${LOGS_ROOT}/startup_stdout.log"
STDERR_PATH="${LOGS_ROOT}/startup_stderr.log"

if [[ ! -d "${LOGS_ROOT}" ]]; then
  echo "日志目录不存在：${LOGS_ROOT}"
  exit 1
fi

echo "==== 日志目录：${LOGS_ROOT} ===="
for path in "${MAIN_LOG}" "${STDERR_PATH}" "${STDOUT_PATH}"; do
  if [[ -f "${path}" ]]; then
    echo ""
    echo "---- $(basename "${path}") (尾部 80 行) ----"
    tail -n 80 "${path}" || true
  fi
done
