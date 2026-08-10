#!/usr/bin/env bash
# scripts/twin_smoke_test.sh — runs scripts/twin_smoke.py: compiles two small
# rules with the Stage 5 compiler and verifies them with the Stage 7
# TwinVerifier against a real ONOS + Mininet. Run scripts/smoke_test.sh first
# to confirm the base environment (Mininet+OVS+ONOS) works before blaming
# twin_verifier for a failure here.
#
# Requires Linux + root + Mininet + iperf3 + a running ONOS (scripts/onos.sh
# start). Run with `sudo ./scripts/twin_smoke_test.sh` (resolves uv via
# SUDO_USER's home when sudo's secure_path strips it from PATH), or
# `sudo -E env "PATH=$PATH" ./scripts/twin_smoke_test.sh`.
#
# Pattern original: sdn-intent-framework's research/scripts/e3_twin_smoke.sh
# (uv resolution under sudo). The verification body itself is new — see
# scripts/twin_smoke.py.
set -Eeuo pipefail
readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

log() { printf '[twin-smoke] %s\n' "$*"; }
die() { printf '[twin-smoke] ERROR: %s\n' "$*" >&2; exit 1; }

resolve_uv() {
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    return
  fi
  local invoker_home
  if [[ -n "${SUDO_USER:-}" ]]; then
    invoker_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    if [[ -x "${invoker_home}/.local/bin/uv" ]]; then
      UV_BIN="${invoker_home}/.local/bin/uv"
      return
    fi
  fi
  if [[ -x "${HOME}/.local/bin/uv" ]]; then
    UV_BIN="${HOME}/.local/bin/uv"
    return
  fi
  die "uv not found (sudo's secure_path strips ~/.local/bin from PATH). Install with scripts/installation/setup.sh, or run: sudo -E env \"PATH=\$PATH\" $0"
}

[[ "$(uname -s)" == Linux ]] || die "Linux only."
[[ "${EUID}" -eq 0 ]] || die "root required (run with sudo ./scripts/twin_smoke_test.sh)."
command -v mn >/dev/null 2>&1 || die "Mininet (mn) not found -- run scripts/installation/setup.sh."
command -v iperf3 >/dev/null 2>&1 || die "iperf3 not found -- run scripts/installation/setup.sh."
resolve_uv
log "Using uv at ${UV_BIN}."

log "Ensuring ONOS is running."
"$PROJECT_ROOT/scripts/onos.sh" start

sudo mn -c >/dev/null 2>&1 || true
"$UV_BIN" run --project "$PROJECT_ROOT" python "$PROJECT_ROOT/scripts/twin_smoke.py"
