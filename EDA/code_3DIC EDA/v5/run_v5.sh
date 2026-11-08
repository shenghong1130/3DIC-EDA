#!/usr/bin/env bash
set -euo pipefail
v5_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
python3 "$v5_dir/verify_source.py"
set +e
bash "$v5_dir/engine/run.sh" "$@"
engine_status=$?
set -e
python3 "$v5_dir/verify_source.py"
exit "$engine_status"
