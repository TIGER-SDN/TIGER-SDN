#!/usr/bin/env bash
# scripts/dev_server.sh — single-command Stage 9 dev environment (issue #31):
# starts ONOS, then the API+UI server, so a browser at localhost:8000 is the
# only remaining step. `./scripts/onos.sh start` + `uvicorn` are one command.
#
# Digital Twin verification needs root (Mininet manipulates kernel network
# namespaces) — TwinVerifier already degrades gracefully without it
# (twin/twin_verifier.py's _check_platform() returns status="skipped", not a
# crash), so running this without sudo is fine for parsing/compiling/static
# validation; only twin checks will show "skipped (no root privileges)".
#
# For a real Digital Twin pass, run under sudo. A bare `sudo` strips PATH/
# UV_PROJECT_ENVIRONMENT (see scripts/twin_smoke_test.sh, same issue) so use:
#   sudo -E env "PATH=$PATH" ./scripts/dev_server.sh
#
# No original — new for Stage 9 (issue #31). Reuses scripts/onos.sh as-is and
# mirrors scripts/twin_smoke_test.sh's uv-under-sudo resolution so the two
# entry points behave the same way.
set -Eeuo pipefail
readonly PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly PORT="${PORT:-8000}"

log() { printf '[dev-server] %s\n' "$*"; }
die() { printf '[dev-server] ERROR: %s\n' "$*" >&2; exit 1; }

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
  # Same rationale as scripts/twin_smoke_test.sh: without UV_PROJECT_ENVIRONMENT,
  # `uv run` defaults to <repo>/.venv, which under sudo on a mounted drive
  # (WSL2's /mnt/*) can be a Windows-built venv that root would silently
  # rebuild with Linux binaries.
  #
  # `return` (bare) inherits the exit status of the failed test that
  # triggered it via `||` -- under `set -e`, a bare top-level call to this
  # function would then kill the whole script on the common "nothing to do"
  # path. Always return 0 explicitly here; only `die` should end the script.
  [[ "${EUID}" -eq 0 ]] || return 0
  [[ -n "${UV_PROJECT_ENVIRONMENT:-}" ]] && return 0
  [[ "$PROJECT_ROOT" == /mnt/* ]] || return 0
  die "UV_PROJECT_ENVIRONMENT is not set and PROJECT_ROOT ($PROJECT_ROOT) is on a mounted drive -- 'uv run' would default to <repo>/.venv and can destroy a host-built venv living at that same path. Set it explicitly: sudo -E env \"PATH=\$PATH\" UV_PROJECT_ENVIRONMENT=\"\$HOME/.venvs/tiger-sdn\" $0"
}

resolve_uv
require_project_environment

if [[ "${EUID}" -eq 0 ]]; then
  log "Running as root — Digital Twin verification will run for real."
else
  log "Running as non-root — Digital Twin checks will report status=skipped (no root privileges). Re-run with sudo -E env \"PATH=\$PATH\" $0 for a real twin pass."
fi

log "Ensuring ONOS is running."
"$PROJECT_ROOT/scripts/onos.sh" start

log "Starting API+UI server on http://127.0.0.1:${PORT} (Ctrl-C to stop; ONOS keeps running — 'scripts/onos.sh stop' to stop it too)."
exec "$UV_BIN" run --project "$PROJECT_ROOT" uvicorn tiger_sdn.api.app:app --reload --port "$PORT"
