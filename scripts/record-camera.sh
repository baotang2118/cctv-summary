#!/usr/bin/env bash

set -euo pipefail

# Crontab example:
# */20 * * * * /usr/bin/env bash /path/to/cctv-summary/scripts/record-camera.sh 'rtsp://camera/stream'

readonly RECORD_SECONDS=1200
readonly CAMERA_URL="${1:-${CAMERA_URL:-}}"
readonly OUTPUT_DIR="${2:-${OUTPUT_DIR:-${HOME:-/tmp}/cctv-recordings}}"
readonly LOCK_FILE="${LOCK_FILE:-/tmp/cctv-summary-record-camera.lock}"

if [[ -z "${CAMERA_URL}" ]]; then
    echo "usage: $0 CAMERA_URL [OUTPUT_DIR]" >&2
    exit 2
fi

# Dependency checks
for command in flock timeout cvlc; do
    if ! command -v "${command}" >/dev/null 2>&1; then
        echo "error: required command not found: ${command}" >&2
        exit 1
    fi
done

exec 9>"${LOCK_FILE}"
flock 9

mkdir -p "${OUTPUT_DIR}"

timestamp="$(date -u +'%Y%m%dT%H%M%SZ')"
# MPEG Transport Stream (.ts) file format
output_path="${OUTPUT_DIR}/camera-${timestamp}.ts"

echo "recording ${CAMERA_URL} to ${output_path}"

set +e
# After 20 minutes, timeout sends SIGINT to VLC.
# VLC gets an opportunity to close and finalize the file cleanly.
# If VLC has not exited after another 30 seconds, timeout forcefully terminates it.
timeout \
    --signal=INT \
    --kill-after=30s \
    "${RECORD_SECONDS}" \
    cvlc \
    --intf dummy \
    --no-video-title-show \
    "${CAMERA_URL}" \
    --sout "#standard{access=file,mux=ts,dst=${output_path}}" \
    vlc://quit
status=$?
set -e

if [[ "${status}" -ne 0 && "${status}" -ne 124 ]]; then
    echo "error: VLC recording failed with exit status ${status}" >&2
    exit "${status}"
fi

flock -u 9
exec 9>&-

echo "recording finished: ${output_path}"
