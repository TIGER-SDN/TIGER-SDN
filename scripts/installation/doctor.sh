#!/usr/bin/env bash
# scripts/installation/doctor.sh — diagnose the Digital Twin (Stage 7)
# verification stack: toolchain, services, ONOS image/container, and the three
# experiment ports (6653 OpenFlow, 8101 Karaf, 8181 REST).
#
# Original: sdn-intent-framework's research/scripts/installation/doctor.sh.
# Adapted: ONOS_CONTAINER default renamed tiger-sdn-onos (see scripts/onos.sh);
# no other changes.
set -uo pipefail
readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly ONOS_CONTAINER="${ONOS_CONTAINER:-tiger-sdn-onos}"
readonly ONOS_IMAGE="${ONOS_IMAGE:-onosproject/onos:2.7.0}"
readonly ONOS_USER="${ONOS_USER:-onos}"
readonly ONOS_PASSWORD="${ONOS_PASSWORD:-rocks}"
failures=0

check_command() {
  local name="$1" version_command="$2"
  if command -v "$name" >/dev/null 2>&1; then
    printf 'PASS  %-18s %s\n' "$name" "$(bash -c "$version_command" 2>/dev/null | head -n 1)"
  else printf 'FAIL  %-18s not found\n' "$name"; failures=$((failures + 1)); fi
}
check_service() {
  if systemctl is-active --quiet "$1"; then printf 'PASS  service:%-10s active\n' "$1";
  else printf 'FAIL  service:%-10s inactive\n' "$1"; failures=$((failures + 1)); fi
}
doctor_report() {
  printf 'TIGER-SDN Digital Twin environment report\n'
  printf 'generated_at: %s\n' "$(date --iso-8601=seconds)"
  printf 'project_root: %s\n' "$PROJECT_ROOT"
  printf 'os: %s\n' "$(source /etc/os-release 2>/dev/null && printf '%s' "${PRETTY_NAME:-unknown}")"
  printf 'kernel: %s\n' "$(uname -srmo)"
  printf 'cpu_count: %s\n' "$(nproc)"
  printf 'memory: %s\n' "$(free -h | awk '/^Mem:/ {print $2}')"
  printf '\nToolchain\n'
  check_command python3 'python3 --version'
  if [[ -x "${HOME}/.local/bin/uv" ]]; then printf 'PASS  %-18s %s\n' uv "$("${HOME}/.local/bin/uv" --version)"; else check_command uv 'uv --version'; fi
  check_command mn 'mn --version'
  check_command ovs-vsctl 'ovs-vsctl --version'
  check_command docker 'docker --version'
  check_command curl 'curl --version'
  check_command iperf3 'iperf3 --version'
  check_command ss 'ss --version'
  if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
    local project_python
    project_python="$("${PROJECT_ROOT}/.venv/bin/python" --version 2>&1)"
    if [[ "$project_python" == 'Python 3.11.'* ]]; then printf 'PASS  %-18s %s\n' project-python "$project_python";
    else printf 'FAIL  %-18s %s (expected Python 3.11.x)\n' project-python "$project_python"; failures=$((failures + 1)); fi
  else printf 'FAIL  %-18s .venv not found\n' project-python; failures=$((failures + 1)); fi
  printf '\nServices and ONOS\n'
  check_service openvswitch-switch
  check_service docker
  local docker_command=(docker)
  docker info >/dev/null 2>&1 || docker_command=(sudo -n docker)
  if "${docker_command[@]}" info >/dev/null 2>&1; then
    printf 'PASS  docker-daemon      reachable\n'
    if "${docker_command[@]}" image inspect "$ONOS_IMAGE" >/dev/null 2>&1; then printf 'PASS  onos-image         %s\n' "$ONOS_IMAGE"; else printf 'FAIL  onos-image         %s not present\n' "$ONOS_IMAGE"; failures=$((failures + 1)); fi
    if "${docker_command[@]}" inspect "$ONOS_CONTAINER" >/dev/null 2>&1; then
      printf 'INFO  onos-container     %s\n' "$("${docker_command[@]}" inspect -f '{{.State.Status}}' "$ONOS_CONTAINER")"
      if curl --fail --silent --user "${ONOS_USER}:${ONOS_PASSWORD}" --max-time 3 http://127.0.0.1:8181/onos/v1/applications >/dev/null 2>&1; then printf 'INFO  onos-rest          ready\n'; else printf 'INFO  onos-rest          not running or not ready\n'; fi
    else printf 'INFO  onos-container     not created\n'; fi
  else printf 'FAIL  docker-daemon      unreachable\n'; failures=$((failures + 1)); fi
  printf '\nListening experiment ports\n'
  local port
  for port in 6653 8101 8181; do
    if ss -lntH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|[.:])${port}$"; then printf 'INFO  tcp:%-14s listening\n' "$port"; else printf 'INFO  tcp:%-14s available\n' "$port"; fi
  done
  printf '\nresult: %s (%d required check failure(s))\n' "$([[ $failures -eq 0 ]] && printf PASS || printf FAIL)" "$failures"
  [[ $failures -eq 0 ]]
}
main() {
  if [[ "${1:-}" == --no-write ]]; then doctor_report
  elif [[ $# -eq 0 ]]; then
    local report_dir="${PROJECT_ROOT}/logs/setup" report_file report_status
    report_file="${report_dir}/environment-$(date +%Y%m%d-%H%M%S).txt"
    mkdir -p "$report_dir"
    doctor_report | tee "$report_file"
    report_status="${PIPESTATUS[0]}"
    printf '\nReport saved to %s\n' "$report_file"
    return "$report_status"
  else printf 'Usage: %s [--no-write]\n' "$0" >&2; return 2; fi
  [[ $failures -eq 0 ]]
}
main "$@"
