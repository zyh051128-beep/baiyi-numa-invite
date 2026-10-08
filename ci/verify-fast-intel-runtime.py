#!/usr/bin/env python3
"""Bounded headless check of the CI-built runtime; no screenshots or model downloads."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
from datetime import datetime, timezone

out, build = map(lambda s: Path(s).resolve(), sys.argv[1:])
native = out / 'bin' / 'darwin-x64'
binary, dylib = native / 'nuphus-mcp', native / 'libonnxruntime.dylib'
def command(*args):
    return subprocess.check_output(args, text=True, timeout=30).strip()
dependencies = {}
for p in (binary, dylib):
    assert command('/usr/bin/lipo', '-archs', str(p)) == 'x86_64', f'Wrong architecture: {p.name}'
    lines = command('/usr/bin/otool', '-L', str(p)).splitlines()[1:]
    deps = [line.strip().split(' (')[0] for line in lines]
    own_id = None
    if p == dylib:
        ids = command('/usr/bin/otool', '-D', str(p)).splitlines()[1:]
        assert len(ids) == 1, 'Expected one dylib install-name'
        own_id = ids[0].strip()
    bad = [d for d in deps if d != own_id and not d.startswith(('/usr/lib/', '/System/Library/'))]
    assert not bad, f'Unbundled runtime dependencies: {bad}'
    dependencies[p.name] = deps
ort = ctypes.CDLL(str(dylib))
get_api_t = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_uint32)
version_t = ctypes.CFUNCTYPE(ctypes.c_char_p)
class OrtApiBase(ctypes.Structure):
    _fields_ = [('GetApi', get_api_t), ('GetVersionString', version_t)]
ort.OrtGetApiBase.restype = ctypes.POINTER(OrtApiBase)
base = ort.OrtGetApiBase().contents
ort_version = base.GetVersionString().decode('ascii')
assert ort_version.startswith('1.23.2'), ort_version
assert base.GetApi(23), 'ORT API23 unavailable'
env = os.environ.copy()
env.update(ORT_DYLIB_PATH=str(dylib), NUPHUS_MCP_NO_MODEL_DOWNLOAD='1', NUPHUS_MCP_HUD='off')
messages = [
    {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'baiyi-numa-ci', 'version': '1'}}},
    {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
]
proc = subprocess.Popen([str(binary), '--confirm-write'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
try:
    stdout, stderr = proc.communicate(''.join(json.dumps(m) + '\n' for m in messages), timeout=45)
except subprocess.TimeoutExpired:
    # Only this explicitly created process session, never unrelated processes.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                stream.close()
    raise SystemExit('MCP initialize/tools/list timed out')
assert proc.returncode == 0, 'MCP exited unsuccessfully'
responses = {}
for line in stdout.splitlines():
    try:
        item = json.loads(line)
    except json.JSONDecodeError:
        continue
    if isinstance(item, dict) and item.get('id') in (1, 2):
        responses[item['id']] = item
assert 'result' in responses.get(1, {}), 'MCP initialize failed'
tools = responses.get(2, {}).get('result', {}).get('tools', [])
assert len(tools) == 45, f'Expected 45 tool schemas; received {len(tools)}'
files = [{'path': str(p.relative_to(out)).replace('\\', '/'), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'size': p.stat().st_size} for p in (binary, dylib)]
data = {
    'schema_version': 1,
    'platform': 'darwin-x64',
    'minimum_macos': '14.0',
    'kind': 'built_from_pinned_source_with_explicit_api23_compatibility_patch',
    'nuphus_mcp': {'version': '0.3.1', 'compatibility_variant': 'intel-api23', 'repository': 'https://github.com/mrpulor-gh/nuphus-mcp', 'commit': 'b2d6ca972b7203ff57f42d5fa993bf4332677637'},
    'onnxruntime': {'version': ort_version, 'repository': 'https://github.com/microsoft/onnxruntime', 'commit': 'a83fc4d58cb48eb68890dd689f94f28288cf2278', 'distribution': 'official Intel macOS release archive', 'api': 23, 'archive_sha256': 'd10359e16347b57d9959f7e80a225a5b4a66ed7d7e007274a15cae86836485a6', 'license_directory': 'licenses/onnxruntime-intel-1.23.2'},
    'files': files,
    'validation': {'ort_load': True, 'ort_api_23': True, 'mcp_initialize': True, 'tool_schema_count': len(tools), 'gui_tested': False, 'ocr_models_tested': False, 'upstream_unit_suites_run': False},
    'build': {'os': platform.platform(), 'rust': command('rustc', '+stable', '--version'), 'cargo': command('cargo', '+stable', '--version'), 'clang': command('clang', '--version').splitlines()[0], 'run_id': os.environ.get('GITHUB_RUN_ID'), 'commit': os.environ.get('GITHUB_SHA'), 'timestamp_utc': datetime.now(timezone.utc).isoformat()},
    'dependencies': dependencies,
    'compatibility_patch': json.loads((out / 'source-patches/compatibility-metadata.json').read_text(encoding='utf-8')),
    'source_patch_file': 'source-patches/api23-compatibility.patch',
    'source_patch_sha256': hashlib.sha256((out / 'source-patches/api23-compatibility.patch').read_bytes()).hexdigest(),
}
(native / 'runtime-provenance.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'ok': True, 'platform': data['platform'], 'ort_version': ort_version, 'tools': len(tools), 'gui_tested': False}))
