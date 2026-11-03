#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
test -f configs/v4_2.json
tmp="$(mktemp .router_v4_2.XXXXXX)"
trap 'rm -f -- "$tmp" "$tmp.sources"' EXIT
flags=(-std=c++17 -O2 -I include -DGR_V3_FINAL -DGR_V3_5 -DGR_V3_6 -DGR_V4 -DGR_V4_2)
"${CXX:-g++}" --version | head -n 1
printf 'Building v4_2 with flags: %s\n' "${flags[*]}"
"${CXX:-g++}" "${flags[@]}" src/main.cpp src/parser.cpp src/router.cpp -o "$tmp"
./"$tmp" --version | grep -q '^router v4_2 CANDIDATE '
find src include -type f \( -name '*.cpp' -o -name '*.hpp' \) -print0 |
  sort -z | xargs -0 sha256sum > "$tmp.sources"
sha256sum configs/v4_2.json build.sh run.sh >> "$tmp.sources"
mv -- "$tmp" router
mv -- "$tmp.sources" router.source.sha256
sha256sum router > router.sha256
printf 'v4_2\n' > router.version
printf 'Built '; cat router.sha256
