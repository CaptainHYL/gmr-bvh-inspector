#!/usr/bin/env bash
# Short bootstrap for an existing GMR checkout. All options go to install.py.
set -euo pipefail

temp_dir=$(mktemp -d "${TMPDIR:-/tmp}/gmr-bvh-install.XXXXXXXX")
trap 'rm -rf -- "$temp_dir"' EXIT

curl -fsSL --connect-timeout 15 --max-time 120 --retry 2 \
  https://raw.githubusercontent.com/CaptainHYL/gmr-bvh-inspector/main/install.py \
  -o "$temp_dir/install.py"

if [[ -n "${BVH_INSPECTOR_PYTHON:-}" ]]; then
  "$BVH_INSPECTOR_PYTHON" "$temp_dir/install.py" "$@" </dev/null
elif command -v conda >/dev/null 2>&1; then
  conda run --no-capture-output -n "${BVH_INSPECTOR_ENV:-gmr}" \
    python "$temp_dir/install.py" "$@" </dev/null
elif [[ -n "${CONDA_EXE:-}" && -x "$CONDA_EXE" ]]; then
  "$CONDA_EXE" run --no-capture-output -n "${BVH_INSPECTOR_ENV:-gmr}" \
    python "$temp_dir/install.py" "$@" </dev/null
elif command -v python >/dev/null 2>&1; then
  python "$temp_dir/install.py" "$@" </dev/null
elif command -v python3 >/dev/null 2>&1; then
  python3 "$temp_dir/install.py" "$@" </dev/null
else
  echo '找不到 Python。请安装/激活 GMR 的 Python 3.10–3.13 环境后重试。' >&2
  exit 1
fi
