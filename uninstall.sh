#!/usr/bin/env bash
# session-explorer plain uninstall (non-marketplace).
# Thin wrapper over `session-explorer uninstall` so the teardown logic lives in
# one place (bin/_pkg/uninstall.py). Idempotent; safe to re-run.
#
# Pass --purge to also delete the session index and folder store.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "${REPO_DIR}/bin/session-explorer" uninstall "$@"
