#!/usr/bin/env bash
# Build the Ubuntu x64 portable package for the meeting-minutes bot.
# Must run on Linux (Ubuntu 24.04 recommended); cannot cross-build from Windows.
set -euo pipefail

SKIP_TESTS=0
if [[ "${1:-}" == "--skip-tests" ]]; then
  SKIP_TESTS=1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
ENV_PATH="${PROJECT_ROOT}/.env.meeting-minutes"
SPEC_PATH="${SCRIPT_DIR}/MeetingMinutesBot.spec"
DIST_ROOT="${PROJECT_ROOT}/dist"
WORK_ROOT="${PROJECT_ROOT}/build/pyinstaller-meeting-linux"
RELEASE_ROOT="${PROJECT_ROOT}/release"
PACKAGE_NAME="周例会纪要机器人"
PACKAGE_ROOT="${RELEASE_ROOT}/${PACKAGE_NAME}"
PROGRAM_ROOT="${PACKAGE_ROOT}/程序"
TAR_PATH="${RELEASE_ROOT}/${PACKAGE_NAME}-Linux-x64.tar.gz"
DOCS_ROOT="${PROJECT_ROOT}/docs/meeting_minutes"

if [[ ! -x "${PYTHON}" ]]; then
  echo "ERROR: project virtualenv not found: ${PYTHON}" >&2
  exit 1
fi
if [[ ! -f "${ENV_PATH}" ]]; then
  echo "ERROR: missing ${ENV_PATH}" >&2
  exit 1
fi

os_name="$(uname -s || true)"
if [[ "${os_name}" != "Linux" ]]; then
  echo "ERROR: this script must run on Linux (got ${os_name})." >&2
  exit 1
fi

read_relative_setting() {
  local name="$1"
  local line value full
  line="$(grep -E "^[[:space:]]*${name}[[:space:]]*=" "${ENV_PATH}" | head -n 1 || true)"
  if [[ -z "${line}" ]]; then
    echo "ERROR: ${name} is missing from .env.meeting-minutes" >&2
    exit 1
  fi
  value="${line#*=}"
  value="$(echo "${value}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
  if [[ "${value}" = /* ]]; then
    echo "ERROR: ${name} must be relative for a portable package." >&2
    exit 1
  fi
  full="${PROJECT_ROOT}/${value}"
  if [[ ! -f "${full}" ]]; then
    echo "ERROR: configured file not found: ${full}" >&2
    exit 1
  fi
  printf '%s\t%s\n' "${value}" "${full}"
}

IFS=$'\t' read -r PEOPLE_REL PEOPLE_FULL < <(read_relative_setting "MEETING_BOT_PEOPLE_CONFIG_PATH")
IFS=$'\t' read -r TEMPLATE_REL TEMPLATE_FULL < <(read_relative_setting "MEETING_BOT_TEMPLATE_PATH")

if [[ "${SKIP_TESTS}" -eq 1 ]]; then
  echo "[1/5] Skipping tests (--skip-tests)."
else
  echo "[1/5] Running tests..."
  "${PYTHON}" -m pytest -q
fi

echo "[2/5] Preparing pinned PyInstaller..."
BUILD_REQUIREMENTS="${PROJECT_ROOT}/requirements-build.txt"
if ! "${PYTHON}" -c "import PyInstaller" >/dev/null 2>&1; then
  if ! "${PYTHON}" -m pip install --disable-pip-version-check -r "${BUILD_REQUIREMENTS}"; then
    if command -v uv >/dev/null 2>&1; then
      echo "      pip unavailable; installing with uv..."
      uv pip install --python "${PYTHON}" -r "${BUILD_REQUIREMENTS}"
    else
      echo "ERROR: PyInstaller installation failed (neither pip nor uv)." >&2
      exit 1
    fi
  fi
fi

rm -rf "${WORK_ROOT}" "${DIST_ROOT}/MeetingMinutesBot" "${PACKAGE_ROOT}" "${TAR_PATH}"

echo "[3/5] Building the portable Linux application..."
"${PYTHON}" -m PyInstaller --noconfirm --clean \
  --distpath "${DIST_ROOT}" \
  --workpath "${WORK_ROOT}" \
  "${SPEC_PATH}"

echo "[4/5] Assembling the portable directory..."
mkdir -p "${PROGRAM_ROOT}"
cp -a "${DIST_ROOT}/MeetingMinutesBot/." "${PROGRAM_ROOT}/"
cp -f "${ENV_PATH}" "${PROGRAM_ROOT}/.env.meeting-minutes"

mkdir -p "${PROGRAM_ROOT}/$(dirname "${PEOPLE_REL}")"
cp -f "${PEOPLE_FULL}" "${PROGRAM_ROOT}/${PEOPLE_REL}"
mkdir -p "${PROGRAM_ROOT}/$(dirname "${TEMPLATE_REL}")"
cp -f "${TEMPLATE_FULL}" "${PROGRAM_ROOT}/${TEMPLATE_REL}"

mkdir -p "${PROGRAM_ROOT}/data/meeting_minutes" "${PROGRAM_ROOT}/logs/meeting_minutes"

# Root helpers (Chinese names) and launcher helpers.
cp -f "${SCRIPT_DIR}/启动机器人.sh" "${PACKAGE_ROOT}/"
cp -f "${SCRIPT_DIR}/停止机器人.sh" "${PACKAGE_ROOT}/"
cp -f "${SCRIPT_DIR}/查看运行日志.sh" "${PACKAGE_ROOT}/"
cp -f "${SCRIPT_DIR}/使用说明.txt" "${PACKAGE_ROOT}/"
chmod +x "${PACKAGE_ROOT}/启动机器人.sh" "${PACKAGE_ROOT}/停止机器人.sh" "${PACKAGE_ROOT}/查看运行日志.sh"
chmod +x "${PROGRAM_ROOT}/MeetingMinutesBot" || true

mkdir -p "${PACKAGE_ROOT}/launcher"
cp -f "${SCRIPT_DIR}/launcher/"*.sh "${PACKAGE_ROOT}/launcher/"
chmod +x "${PACKAGE_ROOT}/launcher/"*.sh

cp -f "${SCRIPT_DIR}/meeting-minutes-bot.service.example" "${PACKAGE_ROOT}/"

for guide in "用户使用说明.md" "管理员使用说明.md"; do
  if [[ ! -f "${DOCS_ROOT}/${guide}" ]]; then
    echo "ERROR: packaged guide missing: ${DOCS_ROOT}/${guide}" >&2
    exit 1
  fi
  cp -f "${DOCS_ROOT}/${guide}" "${PACKAGE_ROOT}/"
done

echo "[5/5] Creating the tar.gz release..."
mkdir -p "${RELEASE_ROOT}"
tar -C "${RELEASE_ROOT}" -czf "${TAR_PATH}" "${PACKAGE_NAME}"

FILE_COUNT="$(find "${PACKAGE_ROOT}" -type f | wc -l | tr -d ' ')"
BYTE_COUNT="$(wc -c < "${TAR_PATH}" | tr -d ' ')"
if command -v sha256sum >/dev/null 2>&1; then
  SHA256="$(sha256sum "${TAR_PATH}" | awk '{print $1}')"
else
  SHA256="$(shasum -a 256 "${TAR_PATH}" | awk '{print $1}')"
fi

echo ""
echo "Portable release created:"
echo "  Directory: ${PACKAGE_ROOT}"
echo "  Archive:   ${TAR_PATH}"
echo "  Files:     ${FILE_COUNT}"
echo "  Bytes:     ${BYTE_COUNT}"
echo "  SHA-256:   ${SHA256}"
echo "WARNING: The release contains .env.meeting-minutes, App Secret and real open_id values. Keep it private."
echo "WARNING: Only one machine may run this Feishu app at a time; stop other instances before handover."
