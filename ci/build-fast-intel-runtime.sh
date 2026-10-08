#!/bin/bash
# Install with verify-fast-intel-runtime.py under ci/. Never publishes or modifies a release.
set -euo pipefail
[ "$(uname -s)" = Darwin ] && [ "$(uname -m)" = x86_64 ] || { echo 'Intel macOS runner required' >&2; exit 1; }
ROOT=$(pwd -P)
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd -P)
BUILD_ROOT="$ROOT/intel-fast-runtime-build"
OUT="$ROOT/intel-fast-runtime-output"
NUPHUS_COMMIT=b2d6ca972b7203ff57f42d5fa993bf4332677637
[ ! -e "$BUILD_ROOT" ] && [ ! -e "$OUT" ] || { echo 'Build/output path already exists; refusing to overwrite' >&2; exit 1; }
mkdir -p "$BUILD_ROOT" "$OUT/bin/darwin-x64" "$OUT/licenses/onnxruntime-intel-1.23.2" "$OUT/source-patches"
export MACOSX_DEPLOYMENT_TARGET=14.0
export CARGO_BUILD_JOBS=3
export NUPHUS_MCP_NO_ORT_DOWNLOAD=1
export NUPHUS_MCP_NO_MODEL_DOWNLOAD=1
export NUPHUS_MCP_HUD=off
git init "$BUILD_ROOT/nuphus-mcp"
git -C "$BUILD_ROOT/nuphus-mcp" remote add origin https://github.com/mrpulor-gh/nuphus-mcp.git
git -C "$BUILD_ROOT/nuphus-mcp" fetch --depth 1 origin "$NUPHUS_COMMIT"
git -C "$BUILD_ROOT/nuphus-mcp" checkout --detach FETCH_HEAD
[ "$(git -C "$BUILD_ROOT/nuphus-mcp" rev-parse HEAD)" = "$NUPHUS_COMMIT" ]
cp "$BUILD_ROOT/nuphus-mcp/LICENSE" "$OUT/licenses/nuphus-mcp-LICENSE"
curl --fail --location --proto '=https' --proto-redir '=https' --retry 2 \
  'https://github.com/microsoft/onnxruntime/releases/download/v1.23.2/onnxruntime-osx-x86_64-1.23.2.tgz' \
  --output "$BUILD_ROOT/onnxruntime-osx-x86_64-1.23.2.tgz"
python3 - "$BUILD_ROOT" "$OUT" <<'PY'
import hashlib, json, pathlib, tarfile, sys
build, out = map(pathlib.Path, sys.argv[1:])
archive = build / 'onnxruntime-osx-x86_64-1.23.2.tgz'
expected = 'd10359e16347b57d9959f7e80a225a5b4a66ed7d7e007274a15cae86836485a6'
if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
    raise SystemExit('Official ORT archive digest mismatch')
members = {
    'onnxruntime-osx-x86_64-1.23.2/lib/libonnxruntime.1.23.2.dylib': out / 'bin/darwin-x64/libonnxruntime.dylib',
    'onnxruntime-osx-x86_64-1.23.2/LICENSE': out / 'licenses/onnxruntime-intel-1.23.2/LICENSE',
    'onnxruntime-osx-x86_64-1.23.2/ThirdPartyNotices.txt': out / 'licenses/onnxruntime-intel-1.23.2/ThirdPartyNotices.txt',
}
with tarfile.open(archive) as tar:
    seen = set()
    for item in tar:
        name = item.name.removeprefix('./')
        if name in members:
            if not item.isfile() or name in seen:
                raise SystemExit('Unexpected official archive entry')
            members[name].write_bytes(tar.extractfile(item).read())
            seen.add(name)
    if seen != set(members):
        raise SystemExit('Required runtime/license archive entries are missing')
cargo = build / 'nuphus-mcp/crates/desktop-api/Cargo.toml'
before = cargo.read_bytes()
if before.count(b'"api-24"') != 1 or b'version = "=2.0.0-rc.12"' not in before:
    raise SystemExit('Unexpected ort dependency: refusing to apply compatibility patch')
after = before.replace(b'"api-24"', b'"api-23"')
cargo.write_bytes(after)
(out / 'source-patches/desktop-api.Cargo.toml').write_bytes(after)
(out / 'source-patches/compatibility-metadata.json').write_text(json.dumps({
    'source_commit': 'b2d6ca972b7203ff57f42d5fa993bf4332677637',
    'path': 'crates/desktop-api/Cargo.toml',
    'before_sha256': hashlib.sha256(before).hexdigest(),
    'after_sha256': hashlib.sha256(after).hexdigest(),
    'change': 'ort 2.0.0-rc.12 feature api-24 changed to its supported api-23 feature; crate version and application source unchanged',
    'ort_archive_sha256': expected,
}, indent=2) + '\n', encoding='utf-8')
PY
git -C "$BUILD_ROOT/nuphus-mcp" diff --binary -- crates/desktop-api/Cargo.toml > "$OUT/source-patches/api23-compatibility.patch"
rustup toolchain install stable --profile minimal
rustup target add --toolchain stable x86_64-apple-darwin
(
  cd "$BUILD_ROOT/nuphus-mcp"
  cargo +stable build --locked --release --target x86_64-apple-darwin -p nuphus-mcp
)
cp "$BUILD_ROOT/nuphus-mcp/target/x86_64-apple-darwin/release/nuphus-mcp" "$OUT/bin/darwin-x64/nuphus-mcp"
chmod 755 "$OUT/bin/darwin-x64/nuphus-mcp"
export ORT_DYLIB_PATH="$OUT/bin/darwin-x64/libonnxruntime.dylib"
mkdir "$BUILD_ROOT/empty-models"
export NUPHUS_MODELS_DIR="$BUILD_ROOT/empty-models"
python3 "$SCRIPT_DIR/verify-fast-intel-runtime.py" "$OUT" "$BUILD_ROOT"
echo 'Compatibility build and protocol checks passed. Full plugin OCR acceptance is still required before release.'
