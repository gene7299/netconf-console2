#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd -- "$script_dir/.." && pwd)"
output_dir="${1:-$project_root/exportToColleagueUbuntu}"
work_dir="${2:-$project_root/build/pyinstaller-ubuntu}"

mkdir -p "$output_dir" "$work_dir"

cd "$project_root"
if ! command -v uv >/dev/null 2>&1; then
  printf 'uv is required for the locked build; install the version documented in CI.\n' >&2
  exit 2
fi
if ! command -v objdump >/dev/null 2>&1; then
  printf 'objdump is required by PyInstaller; install the distro binutils package.\n' >&2
  exit 2
fi
uv sync --locked --no-default-groups --group build
uv run --no-sync python -m PyInstaller \
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

# Keep a stable, human-invokable path for test311.  It is copied from the same
# reviewed bundle, not built from a second independently resolved environment.
cli_dir="${NETCONF_CONSOLE2_CLI_DIR:-$project_root/netconf-console2-cli}"
mkdir -p "$cli_dir"
install -m 0755 "$output_dir/netconf-console2" "$cli_dir/netconf-console2"
(
  cd "$cli_dir"
  sha256sum netconf-console2 > SHA256SUMS.txt
)
printf 'Copied headless test client to %s\n' "$cli_dir/netconf-console2"
