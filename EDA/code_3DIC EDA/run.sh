#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
if ! (cd -- "$ROOT" && test -x router && test -f router.sha256 &&
      test -f router.source.sha256 && sha256sum -c router.sha256 >/dev/null 2>&1 &&
      sha256sum -c router.source.sha256 >/dev/null 2>&1 &&
      ./router --version | grep -q '^router v4_2 CANDIDATE '); then
  bash "$ROOT/build.sh"
fi
test -f "$ROOT/configs/v4_2.json"
if [[ $# -eq 1 && ( "$1" == --version || "$1" == --help ) ]]; then exec "$ROOT/router" "$@"; fi
# Default the candidate config; preserve explicit caller options and budgets.
has_config=false
for arg in "$@"; do if [[ "$arg" == --config ]]; then has_config=true; fi; done
if "$has_config"; then exec "$ROOT/router" "$@"; fi
exec "$ROOT/router" --config "$ROOT/configs/v4_2.json" "$@"
