#!/usr/bin/env bash
# Stop the background meeting-minutes bot started by start_bot.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROGRAM_ROOT="${PACKAGE_ROOT}/程序"
BIN_PATH="${PROGRAM_ROOT}/MeetingMinutesBot"
PID_PATH="${PROGRAM_ROOT}/logs/meeting_minutes/meeting_minutes_bot.pid"

if [[ ! -f "${PID_PATH}" ]]; then
  echo "当前没有通过启动脚本记录的运行实例。"
  exit 0
fi

saved_pid="$(tr -d '[:space:]' < "${PID_PATH}" || true)"
if [[ -z "${saved_pid}" ]] || ! kill -0 "${saved_pid}" 2>/dev/null; then
  rm -f "${PID_PATH}"
  echo "当前没有通过启动脚本记录的运行实例。"
  exit 0
fi

if [[ -r "/proc/${saved_pid}/cmdline" ]] && ! grep -aq "MeetingMinutesBot" "/proc/${saved_pid}/cmdline" 2>/dev/null; then
  rm -f "${PID_PATH}"
  echo "停止失败：PID 文件与 MeetingMinutesBot 进程不匹配，已清理 PID 文件。" >&2
  exit 1
fi

kill "${saved_pid}" 2>/dev/null || true
for _ in 1 2 3 4 5; do
  if ! kill -0 "${saved_pid}" 2>/dev/null; then
    break
  fi
  sleep 1
done
if kill -0 "${saved_pid}" 2>/dev/null; then
  kill -9 "${saved_pid}" 2>/dev/null || true
fi
rm -f "${PID_PATH}"
echo "已停止机器人（PID ${saved_pid}）。"
