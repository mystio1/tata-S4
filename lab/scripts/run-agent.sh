#!/usr/bin/env bash
# Start the QoSense laboratory agent.
#
# Runs on the privileged Ubuntu host only. Binds to the loopback address, so
# the agent is reachable from this machine and from nothing else.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${EUID}" -ne 0 ]]; then
  echo "The laboratory agent needs network-administration capability. Run it with sudo." >&2
  exit 1
fi

if [[ -z "${QOSENSE_LAB_AGENT_TOKEN:-}" && "${QOSENSE_LAB_AGENT_ALLOW_NO_TOKEN:-}" != "yes" ]]; then
  echo "Set QOSENSE_LAB_AGENT_TOKEN, or set QOSENSE_LAB_AGENT_ALLOW_NO_TOKEN=yes for local development." >&2
  exit 1
fi

HOST="${QOSENSE_LAB_AGENT_HOST:-127.0.0.1}"
PORT="${QOSENSE_LAB_AGENT_PORT:-8100}"

exec python3 -m uvicorn agent.service:app --host "${HOST}" --port "${PORT}" --log-level info
