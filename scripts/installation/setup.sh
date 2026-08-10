#!/usr/bin/env bash
# scripts/installation/setup.sh — first-time environment setup for the Digital
# Twin (Stage 7) verification stack: Mininet + Open vSwitch natively, ONOS as a
# single `--network host` Docker container (not docker-compose — Mininet needs
# direct kernel/netns access, so only the controller is containerized).
#
# Original: sdn-intent-framework's research/scripts/installation/setup.sh.
# Adapted: accepts Ubuntu 22.04 in addition to 24.04 (WSL2's default LTS at
# port time), no other changes.
set -Eeuo pipefail

readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly UV_VERSION="${UV_VERSION:-0.11.28}"
readonly UV_BIN="${UV_BIN:-${HOME}/.local/bin/uv}"
readonly ONOS_IMAGE="${ONOS_IMAGE:-onosproject/onos:2.7.0}"

log() { printf '[setup] %s\n' "$*"; }
die() { printf '[setup] ERROR: %s\n' "$*" >&2; exit 1; }

require_supported_host() {
  [[ -r /etc/os-release ]] || die 'Cannot identify this operating system.'
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == ubuntu ]] || die 'This setup script supports Ubuntu only.'
  case "${VERSION_ID:-}" in
    22.04|24.04) ;;
    *) die "Unsupported Ubuntu version ${VERSION_ID:-unknown} (expected 22.04 or 24.04)." ;;
  esac
  [[ "$(uname -m)" == x86_64 ]] || die 'This setup script supports x86_64 only.'
  command -v sudo >/dev/null 2>&1 || die 'sudo is required.'
}

select_docker_command() {
  if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi
}

install_system_packages() {
  log 'Refreshing Ubuntu package metadata.'
  sudo apt-get update
  log 'Installing Mininet, Open vSwitch, traffic tooling, and setup prerequisites.'
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl iproute2 iperf3 mininet openvswitch-switch
  sudo systemctl enable --now openvswitch-switch
  if ! command -v docker >/dev/null 2>&1; then
    log 'Docker is not installed; installing Ubuntu docker.io.'
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
  fi
  sudo systemctl enable --now docker
  if getent group docker >/dev/null 2>&1 && ! id -nG "$USER" | tr ' ' '\n' | grep -qx docker; then
    log "Adding ${USER} to the docker group for future sessions."
    sudo usermod -aG docker "$USER"
    log 'Group membership takes effect after logging in again; this run will use sudo.'
  fi
}

install_uv() {
  local current_version=''
  if [[ -x "$UV_BIN" ]]; then current_version="$($UV_BIN --version 2>/dev/null | awk '{print $2}')"; fi
  if [[ "$current_version" != "$UV_VERSION" ]]; then
    local installer
    installer="$(mktemp)"
    trap 'rm -f "${installer:-}"' RETURN
    log "Installing uv ${UV_VERSION} into ${HOME}/.local/bin."
    curl -LsSf "https://astral.sh/uv/${UV_VERSION}/install.sh" -o "$installer"
    env UV_INSTALL_DIR="${HOME}/.local/bin" UV_NO_MODIFY_PATH=1 sh "$installer"
    rm -f "$installer"
    trap - RETURN
  else
    log "uv ${UV_VERSION} is already installed."
  fi
  [[ -x "$UV_BIN" ]] || die "uv was not found at ${UV_BIN}."
  log 'Synchronizing the locked Python 3.11 project environment.'
  "$UV_BIN" sync --locked --project "$PROJECT_ROOT"
}

prepare_onos() {
  select_docker_command
  log "Pulling ${ONOS_IMAGE}."
  "${DOCKER[@]}" pull "$ONOS_IMAGE"
}

main() {
  require_supported_host
  sudo -v
  install_system_packages
  install_uv
  prepare_onos
  log 'Running the environment doctor.'
  "$PROJECT_ROOT/scripts/installation/doctor.sh"
  log 'Setup complete. Run ./scripts/onos.sh start, then ./scripts/smoke_test.sh.'
}
main "$@"
