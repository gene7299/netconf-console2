#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
output_dir="${1:-$project_root/exportToColleagueUbuntu}"
work_dir="${2:-$project_root/build/pyinstaller-ubuntu}"

python_bin="${PYTHON_BIN:-python3}"
mkdir -p "$output_dir" "$work_dir"

cd "$project_root"
"$python_bin" -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$output_dir" \
  --workpath "$work_dir" \
  packaging/netconf_console2_linux.spec

chmod 0755 "$output_dir/netconf-console2"
sum_names=("netconf-console2")
if [[ -f "$output_dir/netconf-console2-gui" ]]; then
  sum_names+=("netconf-console2-gui")
fi
(
  cd "$output_dir"
  sha256sum "${sum_names[@]}" > SHA256SUMS.txt
)
printf 'Built %s\n' "$output_dir/netconf-console2"
