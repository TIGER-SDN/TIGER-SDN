#!/usr/bin/env bash
# scripts/onos.sh — lifecycle management for the ONOS controller container used
# by the Digital Twin (Stage 7): start, stop, restart, status, logs.
#
# Original: sdn-intent-framework's research/scripts/onos.sh. Adapted:
# ONOS_CONTAINER default renamed safe-intent-onos -> tiger-sdn-onos (avoids
# clashing with a leftover container from the source repo on the same host);
# label renamed to match. No other changes.
set -Eeuo pipefail

readonly ONOS_CONTAINER="${ONOS_CONTAINER:-tiger-sdn-onos}"
readonly ONOS_IMAGE="${ONOS_IMAGE:-onosproject/onos:2.7.0}"
readonly ONOS_USER="${ONOS_USER:-onos}"
readonly ONOS_PASSWORD="${ONOS_PASSWORD:-rocks}"
readonly ONOS_REST_URL="${ONOS_REST_URL:-http://127.0.0.1:8181/onos/v1/applications}"
readonly ONOS_READY_TIMEOUT="${ONOS_READY_TIMEOUT:-120}"

log() { printf '[onos] %s\n' "$*"; }
die() { printf '[onos] ERROR: %s\n' "$*" >&2; exit 1; }

select_docker_command() {
  command -v docker >/dev/null 2>&1 || die 'Docker is not installed. Run ./scripts/installation/setup.sh first.'
  if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi
  "${DOCKER[@]}" info >/dev/null 2>&1 || die 'The Docker daemon is unavailable.'
}
container_exists() { "${DOCKER[@]}" inspect "$ONOS_CONTAINER" >/dev/null 2>&1; }
container_running() { [[ "$("${DOCKER[@]}" inspect -f '{{.State.Running}}' "$ONOS_CONTAINER" 2>/dev/null || true)" == true ]]; }
rest_ready() { curl --fail --silent --user "${ONOS_USER}:${ONOS_PASSWORD}" --max-time 3 "$ONOS_REST_URL" >/dev/null 2>&1; }

wait_until_ready() {
  local deadline=$((SECONDS + ONOS_READY_TIMEOUT))
  while (( SECONDS < deadline )); do
    rest_ready && return 0
    container_running || return 1
    sleep 2
  done
  return 1
}

port_is_listening() { ss -lntH 2>/dev/null | awk '{print $4}' | grep -Eq "(^|[.:])${1}$"; }
assert_ports_available() {
  local port
  for port in 6653 8101 8181; do port_is_listening "$port" && die "TCP port ${port} is already in use."; done
  return 0
}
activate_openflow() {
  "${DOCKER[@]}" exec "$ONOS_CONTAINER" /root/onos/bin/onos-app localhost activate org.onosproject.openflow >/dev/null
}

start_onos() {
  if container_running; then
    log "${ONOS_CONTAINER} is already running."
    wait_until_ready || die 'The running ONOS container is not ready.'
    activate_openflow
    return
  fi
  container_exists && "${DOCKER[@]}" rm "$ONOS_CONTAINER" >/dev/null
  assert_ports_available
  log "Starting ${ONOS_IMAGE} as ${ONOS_CONTAINER}."
  "${DOCKER[@]}" run -d --name "$ONOS_CONTAINER" --network host \
    --label tiger-sdn.component=controller "$ONOS_IMAGE" >/dev/null
  if ! wait_until_ready; then
    log 'ONOS did not become ready. Recent logs:' >&2
    "${DOCKER[@]}" logs --tail 100 "$ONOS_CONTAINER" >&2 || true
    return 1
  fi
  activate_openflow
  log 'ONOS is ready; the OpenFlow app is active.'
}

stop_onos() {
  if ! container_exists; then log "${ONOS_CONTAINER} does not exist; nothing to stop."; return; fi
  if ! container_running; then log "${ONOS_CONTAINER} is already stopped."; return; fi
  "${DOCKER[@]}" stop "$ONOS_CONTAINER" >/dev/null
  log 'ONOS stopped.'
}

status_onos() {
  if ! container_exists; then log "${ONOS_CONTAINER} does not exist."; return 1; fi
  "${DOCKER[@]}" ps -a --filter "name=^/${ONOS_CONTAINER}$" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
  if container_running && rest_ready; then log 'REST API is ready.'; else log 'REST API is not ready.' >&2; return 1; fi
}
show_logs() { container_exists || die "${ONOS_CONTAINER} does not exist."; "${DOCKER[@]}" logs --tail "${ONOS_LOG_TAIL:-200}" "$ONOS_CONTAINER"; }
usage() { printf 'Usage: %s {start|stop|restart|status|logs}\n' "$0" >&2; exit 2; }

main() {
  [[ $# -eq 1 ]] || usage
  select_docker_command
  case "$1" in
    start) start_onos ;;
    stop) stop_onos ;;
    restart) stop_onos; start_onos ;;
    status) status_onos ;;
    logs) show_logs ;;
    *) usage ;;
  esac
}
main "$@"
