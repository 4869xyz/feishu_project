#!/usr/bin/env bash
# Start the frozen meeting-minutes bot in the background.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROGRAM_ROOT="${PACKAGE_ROOT}/程序"
BIN_PATH="${PROGRAM_ROOT}/MeetingMinutesBot"
LOGS_ROOT="${PROGRAM_ROOT}/logs/meeting_minutes"
PID_PATH="${LOGS_ROOT}/meeting_minutes_bot.pid"
STDOUT_PATH="${LOGS_ROOT}/startup_stdout.log"
STDERR_PATH="${LOGS_ROOT}/startup_stderr.log"

if [[ ! -x "${BIN_PATH}" && ! -f "${BIN_PATH}" ]]; then
  echo "启动失败：找不到程序文件 ${BIN_PATH}，请确认发布包完整。" >&2
  exit 1
fi
chmod +x "${BIN_PATH}" || true
mkdir -p "${LOGS_ROOT}"

if [[ -f "${PID_PATH}" ]]; then
  old_pid="$(tr -d '[:space:]' < "${PID_PATH}" || true)"
  if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
    if [[ -r "/proc/${old_pid}/cmdline" ]] && grep -aq "MeetingMinutesBot" "/proc/${old_pid}/cmdline" 2>/dev/null; then
      echo "机器人已在运行（PID ${old_pid}）。"
      exit 0
    fi
  fi
  rm -f "${PID_PATH}"
fi

cd "${PROGRAM_ROOT}"
nohup "${BIN_PATH}" >"${STDOUT_PATH}" 2>"${STDERR_PATH}" &
new_pid=$!
echo "${new_pid}" >"${PID_PATH}"

# First start loads OCR models; wait longer than a typical process probe.
sleep 10
if ! kill -0 "${new_pid}" 2>/dev/null; then
  rm -f "${PID_PATH}"
  echo "启动失败：进程已退出。最近日志：" >&2
  tail -n 20 "${STDERR_PATH}" "${STDOUT_PATH}" 2>/dev/null || true
  exit 1
fi

echo "启动成功：PID ${new_pid}"
echo "日志目录：${LOGS_ROOT}"
echo "可用「查看运行日志.sh」查看输出；生产环境建议改用 systemd（见 meeting-minutes-bot.service.example）。"
