#!/usr/bin/env bash
# Return the laboratory to a known baseline, then prove it forwards traffic.
#
# Safe to run at any time. It only ever touches namespaces and interfaces
# carrying the qos- prefix; the host's own networking is never referenced.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "${EUID}" -ne 0 ]]; then
  echo "Resetting the laboratory needs network-administration capability. Run it with sudo." >&2
  exit 1
fi

python3 - <<'PY'
import logging
logging.basicConfig(level=logging.INFO, format="%(message)s")
from agent import enforcement, measurement, topology

measurement.stop_generators()
profile = topology.reset()
enforcement.clear_shaping()
check = topology.connectivity_check()
print(f"link: {profile.download_mbps:g} Mbps down, {profile.upload_mbps:g} Mbps up, "
      f"{profile.base_rtt_ms:g} ms base round-trip time")
print("forwarding:", "yes" if check["reachable"] else "no")
raise SystemExit(0 if check["reachable"] else 1)
PY
