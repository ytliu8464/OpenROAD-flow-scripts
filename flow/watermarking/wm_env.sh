# SPDX-License-Identifier: BSD-3-Clause
#
# Shared environment resolution for every PDMarks shell entry point.
# Source it, do not execute it:
#
#     source "$(dirname "${BASH_SOURCE[0]}")/../wm_env.sh"
#
# It resolves three things and nothing else:
#
#   FLOW_HOME     ORFS flow root.  Derived from this file's own location, so it
#                 is correct wherever the tree is checked out.  Override with
#                 ORFS_FLOW_HOME.
#   OPENROAD_EXE  OpenROAD binary built with the PDMarks routing commands.
#                 Taken from the environment, else `openroad` on PATH.
#   SINGULARITY_SIF
#                 OPTIONAL.  When set, wm_exec runs the command inside that
#                 container; when unset, wm_exec runs it directly on the host.
#                 There is no site-specific default -- containers are opt-in.
#
# Nothing here hard-codes a path outside the repository.

# ---------------------------------------------------------------------------
# Roots
# ---------------------------------------------------------------------------

# This file lives at <flow>/watermarking/wm_env.sh.
_WM_ENV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export WM_HOME="${_WM_ENV_DIR}"
export FLOW_HOME="${ORFS_FLOW_HOME:-${FLOW_HOME:-$(cd "${_WM_ENV_DIR}/.." && pwd)}}"
export EXPERIMENTS_HOME="${EXPERIMENTS_HOME:-${WM_HOME}/experiments}"
export WM_RESULTS_HOME="${WM_RESULTS_HOME:-${EXPERIMENTS_HOME}/results}"
export GEN_KEY_DIR="${GEN_KEY_DIR:-${WM_HOME}/gen_key}"

# Owner identity used when auto-generating a demo key bundle.  Override for a
# real run; the default is deliberately impersonal.
export OWNER_ID="${OWNER_ID:-pdmarks-owner}"

# ---------------------------------------------------------------------------
# OpenROAD binary
# ---------------------------------------------------------------------------

if [[ -z "${OPENROAD_EXE:-}" ]]; then
  if command -v openroad >/dev/null 2>&1; then
    OPENROAD_EXE="$(command -v openroad)"
  else
    OPENROAD_EXE="openroad"   # resolved inside the container, if one is used
  fi
fi
export OPENROAD_EXE

# ---------------------------------------------------------------------------
# Optional container
# ---------------------------------------------------------------------------

# wm_exec <cmd> [args...]
#   Run a command, transparently entering ${SINGULARITY_SIF} when that variable
#   is set and we are not already inside a container.  Extra bind mounts can be
#   supplied via WM_SINGULARITY_BINDS (default: the repository root).
wm_exec() {
  if [[ -n "${SINGULARITY_SIF:-}" && -z "${SINGULARITY_NAME:-}" ]]; then
    local binds="${WM_SINGULARITY_BINDS:-${FLOW_HOME}}"
    local bind_args=()
    local b
    for b in ${binds}; do
      bind_args+=(-B "${b}")
    done
    singularity exec "${bind_args[@]}" -e "${SINGULARITY_SIF}" "$@"
  else
    "$@"
  fi
}

# wm_require_openroad
#   Fail early, with an actionable message, when no usable OpenROAD is visible.
#   Skipped when a container is configured, since the binary lives inside it.
wm_require_openroad() {
  [[ -n "${SINGULARITY_SIF:-}" ]] && return 0
  if [[ ! -x "${OPENROAD_EXE}" ]] && ! command -v "${OPENROAD_EXE}" >/dev/null 2>&1; then
    echo "ERROR: OpenROAD not found (tried '${OPENROAD_EXE}')." >&2
    echo "       Set OPENROAD_EXE to a binary built with the PDMarks routing" >&2
    echo "       commands, or set SINGULARITY_SIF to a container providing it." >&2
    return 1
  fi
  return 0
}

# wm_python
#   Echo an interpreter that satisfies the >=3.9 requirement of the analysis
#   scripts.  Override with WM_PYTHON.
wm_python() {
  if [[ -n "${WM_PYTHON:-}" ]]; then
    echo "${WM_PYTHON}"
    return 0
  fi
  local cand
  for cand in python3 python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "${cand}" >/dev/null 2>&1 && \
       "${cand}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
      echo "${cand}"
      return 0
    fi
  done
  echo "python3"
}
