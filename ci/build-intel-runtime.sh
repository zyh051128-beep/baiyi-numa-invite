#!/bin/bash
# Install this alongside verify-intel-runtime.py under ci/ in the CI repository.
# Builds pinned upstream source on an ephemeral Intel macOS runner; does not publish.
set -euo pipefail
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = x86_64 ] || { echo 'Intel macOS runner required' >&2; exit 1; }
ROOT=$(pwd -P)
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P)
BUILD_ROOT="$ROOT/intel-runtime-build"
OUT="$ROOT/intel-runtime-output"
NUPHUS_COMMIT=b2d6ca972b7203ff57f42d5fa993bf4332677637
ORT_COMMIT=8f0278c77bf44b0cc83c098c6c722b92a36ac4b5
[ ! -e "$BUILD_ROOT" ] && [ ! -e "$OUT" ] || { echo 'Build/output path already exists; refusing to overwrite' >&2; exit 1; }
mkdir -p "$BUILD_ROOT" "$OUT/bin/darwin-x64"
command -v cmake
command -v python3
command -v rustup
export MACOSX_DEPLOYMENT_TARGET=14.0
export CMAKE_BUILD_PARALLEL_LEVEL=3
export CARGO_BUILD_JOBS=3
export NUPHUS_MCP_NO_ORT_DOWNLOAD=1
export NUPHUS_MCP_NO_MODEL_DOWNLOAD=1
export NUPHUS_MCP_HUD=off
fetch_source() {
  local url=$1 commit=$2 path=$3
  git init "$path"
  git -C "$path" remote add origin "$url"
  git -C "$path" fetch --depth 1 origin "$commit"
  git -C "$path" checkout --detach FETCH_HEAD
  [ "$(git -C "$path" rev-parse HEAD)" = "$commit" ]
}
fetch_source https://github.com/microsoft/onnxruntime.git "$ORT_COMMIT" "$BUILD_ROOT/onnxruntime"
fetch_source https://github.com/mrpulor-gh/nuphus-mcp.git "$NUPHUS_COMMIT" "$BUILD_ROOT/nuphus-mcp"
git -C "$BUILD_ROOT/onnxruntime" submodule update --init --recursive --depth 1
rustup toolchain install stable --profile minimal
rustup target add --toolchain stable x86_64-apple-darwin
(
  cd "$BUILD_ROOT/onnxruntime"
  # CPU shared runtime; retain every operator and API24. No reduced-op build.
  # Full upstream unit suites are omitted to respect the bounded CI budget;
  # the separate runtime check below tests loading, API24, initialize and tools/list.
  ./build.sh --config Release --update --build --build_shared_lib --parallel 3 \
    --skip_tests --skip_submodule_sync --compile_no_warning_as_error \
    --osx_arch x86_64 --apple_deploy_target 14.0 \
    --cmake_extra_defines onnxruntime_BUILD_UNIT_TESTS=OFF
)
python3 - "$BUILD_ROOT/onnxruntime/build" "$OUT/bin/darwin-x64/libonnxruntime.dylib" <<'PY'
import pathlib, shutil, sys
root, dest = map(pathlib.Path, sys.argv[1:])
candidates = [p for p in root.rglob('libonnxruntime.dylib') if p.is_file()]
if len(candidates) != 1:
    raise SystemExit(f'Expected one built ORT dylib, found {len(candidates)}')
shutil.copy2(candidates[0].resolve(), dest)
PY
(
  cd "$BUILD_ROOT/nuphus-mcp"
  cargo +stable build --locked --release --target x86_64-apple-darwin -p nuphus-mcp
)
cp "$BUILD_ROOT/nuphus-mcp/target/x86_64-apple-darwin/release/nuphus-mcp" "$OUT/bin/darwin-x64/nuphus-mcp"
chmod 755 "$OUT/bin/darwin-x64/nuphus-mcp"
export ORT_DYLIB_PATH="$OUT/bin/darwin-x64/libonnxruntime.dylib"
mkdir "$BUILD_ROOT/empty-models"
export NUPHUS_MODELS_DIR="$BUILD_ROOT/empty-models"
python3 "$SCRIPT_DIR/verify-intel-runtime.py" "$OUT" "$BUILD_ROOT"
echo 'Intel runtime built and headless checks passed. GUI permissions/OCR/model checks are separate.'
