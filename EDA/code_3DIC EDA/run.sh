#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ ! -x "$ROOT/router" ]]; then bash "$ROOT/build.sh"; fi
exec "$ROOT/router" "$@"
