#!/usr/bin/env bash
# scripts/smoke_test.sh — base connectivity check: Mininet + OVS + OpenFlow 1.3
# to a real ONOS, independent of any TIGER-SDN pipeline code. Run this before
# scripts/twin_smoke_test.sh to isolate environment problems from twin_verifier
# problems.
#
# Original: sdn-intent-framework's research/scripts/smoke_test.sh. No changes
# beyond calling this repo's scripts/onos.sh.
set -Eeuo pipefail
readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly ONOS_CONTAINER="${ONOS_CONTAINER:-tiger-sdn-onos}"
readonly ONOS_USER="${ONOS_USER:-onos}"
readonly ONOS_PASSWORD="${ONOS_PASSWORD:-rocks}"

select_docker_command() { if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi; }
cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  set +e
  "${DOCKER[@]}" exec "$ONOS_CONTAINER" /root/onos/bin/onos-app localhost deactivate org.onosproject.fwd >/dev/null 2>&1
  sudo mn -c >/dev/null 2>&1
  [[ $exit_code -eq 0 ]] || printf '[smoke] FAILED (exit %d)\n' "$exit_code" >&2
  exit "$exit_code"
}
main() {
  command -v mn >/dev/null 2>&1 || { printf '[smoke] ERROR: Run ./scripts/installation/setup.sh first.\n' >&2; return 1; }
  select_docker_command
  "$PROJECT_ROOT/scripts/onos.sh" start
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  printf '[smoke] Activating reactive forwarding for this test only.\n'
  "${DOCKER[@]}" exec "$ONOS_CONTAINER" /root/onos/bin/onos-app localhost activate org.onosproject.fwd >/dev/null
  sudo mn -c >/dev/null
  printf '[smoke] Running the OpenFlow 1.3 single-switch/three-host ping test.\n'
  local output
  output="$(sudo mn --topo single,3 --controller remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13 --test pingall 2>&1)"
  printf '%s\n' "$output"
  grep -Eq 'Results: 0% dropped|0% dropped' <<<"$output" || { printf '[smoke] ERROR: pingall did not report 0%% packet loss.\n' >&2; return 1; }
  printf '[smoke] Verifying that ONOS discovered at least one device.\n'
  curl --fail --silent --show-error --user "${ONOS_USER}:${ONOS_PASSWORD}" --max-time 5 http://127.0.0.1:8181/onos/v1/devices |
    python3 -c 'import json, sys; data=json.load(sys.stdin); sys.exit(0 if data.get("devices") else 1)'
  printf '[smoke] PASS: connectivity and ONOS device discovery succeeded.\n'
}
main "$@"
