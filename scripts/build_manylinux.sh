#!/usr/bin/env bash
set -euo pipefail

# 这是显式容器入口的环境准备，不在宿主安装或改写工具链。
abi="$1"
rust_version="$2"
maturin_version="$3"
archive="$4"
export CARGO_HOME=/tmp/rivlet-cargo
export RUSTUP_HOME=/tmp/rivlet-rustup
export CARGO_BUILD_JOBS=4
export PATH="$CARGO_HOME/bin:$PATH"
curl --proto '=https' --tlsv1.2 --connect-timeout 20 --max-time 180 -sSf \
    https://sh.rustup.rs -o /tmp/rivlet-rustup.sh
sh /tmp/rivlet-rustup.sh -y --profile minimal --default-toolchain "$rust_version"
python="/opt/python/$abi-$abi/bin/python"
"$python" -m pip install "maturin==$maturin_version"
exec "$python" /workflow/scripts/build_dist.py manylinux \
    "/input/$archive" /output --interpreter "$python"
