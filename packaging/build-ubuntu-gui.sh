#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
output_dir="${1:-$project_root/exportToColleagueUbuntu}"
work_dir="${2:-$project_root/build/pyinstaller-ubuntu-gui}"
python_bin="${PYTHON_BIN:-python3}"

mkdir -p "$output_dir" "$work_dir"
cd "$project_root"

"$python_bin" -m PyInstaller --noconfirm --clean \
  --distpath "$output_dir" --workpath "$work_dir" \
  packaging/netconf_console2_gui_linux.spec

chmod 0755 "$output_dir/netconf-console2-gui"

self_test_report="$work_dir/gui-self-test.json"
QT_QPA_PLATFORM="${QT_QPA_PLATFORM_SELF_TEST:-offscreen}" \
  "$output_dir/netconf-console2-gui" --self-test "$self_test_report"
grep -q '"passed": true' "$self_test_report"

sum_names=("netconf-console2-gui")
if [[ -f "$output_dir/netconf-console2" ]]; then
  sum_names=("netconf-console2" "${sum_names[@]}")
fi
(cd "$output_dir" && sha256sum "${sum_names[@]}" > SHA256SUMS.txt)

printf 'Built %s\n' "$output_dir/netconf-console2-gui"
printf 'Self-test report: %s\n' "$self_test_report"
