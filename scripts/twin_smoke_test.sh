#!/usr/bin/env bash
# scripts/twin_smoke_test.sh — runs scripts/twin_smoke.py: compiles two small
# rules with the Stage 5 compiler and verifies them with the Stage 7
# TwinVerifier against a real ONOS + Mininet. Run scripts/smoke_test.sh first
# to confirm the base environment (Mininet+OVS+ONOS) works before blaming
# twin_verifier for a failure here.
#
# Requires Linux + root + Mininet + iperf3 + a running ONOS (scripts/onos.sh
# start). A bare `sudo` strips the environment (PATH, UV_PROJECT_ENVIRONMENT,
# ...), so run with `sudo -E env "PATH=$PATH" ./scripts/twin_smoke_test.sh` --
# resolve_uv() below falls back to SUDO_USER's home if PATH still didn't make
# it through, but UV_PROJECT_ENVIRONMENT has no such fallback, see
# require_project_environment().
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

require_project_environment() {
  # Without UV_PROJECT_ENVIRONMENT, `uv run` defaults to <repo>/.venv. If the
  # repo lives on a mounted Windows drive (WSL2's /mnt/*), that default IS
  # the host-built venv -- and `uv run` as root will happily delete and
  # recreate it with Linux binaries (this happened once while porting this
  # script: a bare `sudo ./scripts/twin_smoke_test.sh` silently destroyed the
  # Windows .venv). A bare `sudo` strips UV_PROJECT_ENVIRONMENT even when set
  # in the invoking shell, so refuse instead of guessing.
  #
  # `return` (bare) inherits the exit status of the failed test that
  # triggered it via `||` -- under `set -e`, a bare top-level call to this
  # function then kills the whole script on the common "nothing to do" path
  # (plain `sudo ./scripts/twin_smoke_test.sh` on a non-WSL2 host, i.e. most
  # runs). Always return 0 explicitly here; only `die` should end the script.
  [[ -n "${UV_PROJECT_ENVIRONMENT:-}" ]] && return 0
  [[ "$PROJECT_ROOT" == /mnt/* ]] || return 0
  die "UV_PROJECT_ENVIRONMENT is not set and PROJECT_ROOT ($PROJECT_ROOT) is on a mounted drive -- 'uv run' would default to <repo>/.venv and can destroy a host-built venv living at that same path. Set it explicitly: sudo -E env \"PATH=\$PATH\" UV_PROJECT_ENVIRONMENT=\"\$HOME/.venvs/tiger-sdn\" $0"
}

[[ "$(uname -s)" == Linux ]] || die "Linux only."
[[ "${EUID}" -eq 0 ]] || die "root required (run with sudo ./scripts/twin_smoke_test.sh)."
command -v mn >/dev/null 2>&1 || die "Mininet (mn) not found -- run scripts/installation/setup.sh."
command -v iperf3 >/dev/null 2>&1 || die "iperf3 not found -- run scripts/installation/setup.sh."
resolve_uv
require_project_environment
log "Using uv at ${UV_BIN}."

log "Ensuring ONOS is running."
"$PROJECT_ROOT/scripts/onos.sh" start

sudo mn -c >/dev/null 2>&1 || true
"$UV_BIN" run --project "$PROJECT_ROOT" python "$PROJECT_ROOT/scripts/twin_smoke.py"
