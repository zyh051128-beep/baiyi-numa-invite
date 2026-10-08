#!/usr/bin/env python3
"""Offline synthetic tests. Never use a production invitation or execute MCP.

python test_install.py --installer /path/to/install.py [--require-openssl]
Uses existing cryptography or OpenSSL to create synthetic encryption fixtures.
"""
import argparse
import ast
import copy
import hashlib
import hmac
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

PARSER = argparse.ArgumentParser()
PARSER.add_argument('--installer', type=Path, default=Path(__file__).resolve().parents[1] / 'install.py')
PARSER.add_argument('--require-openssl', action='store_true')
PARSER.add_argument('--work-dir', type=Path)
PARSER.add_argument('-v', '--verbose', action='store_true')
ARGS = PARSER.parse_args()
SPEC = importlib.util.spec_from_file_location('baiyi_installer_test_target', ARGS.installer)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
CODE = 'SYNTHETIC-aBcD0123456789+_ZyxWVutsRQpnMLKjIHGfE'
WRONG = 'WRONG-AaBbCcDdEeFf0123456789+_WXYZzyxwvutsrqpon'
WORK = Path(tempfile.mkdtemp(prefix='baiyi-installer-tests-', dir=ARGS.work_dir)).resolve()


def jbytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def fixture(version='1.0.0', mutate=None):
    files = {name: b'synthetic fixture; never execute\n' for name in M.ESSENTIAL}
    files['.agents/plugins/marketplace.json'] = jbytes({'name': M.MARKET, 'plugins': [{'name': M.PLUGIN, 'source': {'source': 'local', 'path': './plugins/nuphus'}}]})
    files[M.PREFIX + '.codex-plugin/plugin.json'] = jbytes({'name': M.PLUGIN, 'version': version, 'mcpServers': './.mcp.json'})
    files[M.OVERLAY_DEST] = b'{"mcpServers":{"fixture":{"command":"cmd.exe"}}}'
    files[M.OVERLAY_SOURCE] = b'{"mcpServers":{"fixture":{"command":"/bin/sh"}}}'
    files[M.PREFIX + 'scripts/start-mcp.sh'] = b'#!/bin/sh\nexit 99\n'
    files[M.PREFIX + 'scripts/fixture-no-extension'] = b'#!/usr/bin/env python3\nraise SystemExit(99)\n'
    files[M.PREFIX + 'bin/darwin-arm64/nuphus-mcp'] = b'\xcf\xfa\xed\xfeSYNTHETIC NOT EXECUTABLE'
    files[M.PREFIX + 'bin/darwin-arm64/libonnxruntime.dylib'] = b'SYNTHETIC DYLIB'
    files[M.PREFIX + 'skills/image-blaster/SKILL.md'] = b'---\nname: image-blaster\ndescription: Synthetic test.\n---\n'
    for i in range(127):
        files[M.PREFIX + 'skills/fixture-%03d/SKILL.md' % i] = b'---\nname: fixture\ndescription: Synthetic.\n---\n'
    manifest = {'schema_version': 1, 'plugin': M.PLUGIN, 'marketplace': M.MARKET,
                'executables': [M.PREFIX + 'scripts/start-mcp.sh', M.PREFIX + 'scripts/fixture-no-extension', M.PREFIX + 'bin/darwin-arm64/nuphus-mcp']}
    if mutate:
        mutate(files, manifest)
    manifest['files'] = [{'path': name, 'size': len(value), 'sha256': hashlib.sha256(value).hexdigest()} for name, value in files.items()]
    overlay = next(item for item in manifest['files'] if item['path'] == M.OVERLAY_SOURCE)
    manifest.setdefault('platform_overlays', {'darwin': {M.OVERLAY_DEST: {'source': M.OVERLAY_SOURCE, 'size': overlay['size'], 'sha256': overlay['sha256']}}})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in {**files, 'release-manifest.json': jbytes(manifest)}.items():
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value)
    return buf.getvalue(), files, manifest


def archive_release(raw):
    archive, infos = M.inspect_zip(raw)
    return archive, infos, M.verify_archive(archive, infos)


def encrypted(raw):
    iv = bytes(range(16))
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        candidates = M.openssl_candidates()
        if not candidates:
            raise RuntimeError('Synthetic tests need existing cryptography or OpenSSL')
        with tempfile.TemporaryDirectory(dir=WORK) as folder:
            plain = Path(folder) / 'synthetic.zip'
            plain.write_bytes(raw)
            result = subprocess.run([candidates[0], 'enc', '-e', '-aes-256-cbc', '-nosalt', '-md', 'sha256', '-pass', 'stdin', '-iv', iv.hex(), '-in', str(plain)], input=CODE.encode() + b'\n', capture_output=True, check=True)
            cipher = result.stdout
    else:
        padder = padding.PKCS7(128).padder()
        data = padder.update(raw) + padder.finalize()
        encryptor = Cipher(algorithms.AES(hashlib.sha256(CODE.encode()).digest()), modes.CBC(iv)).encryptor()
        cipher = encryptor.update(data) + encryptor.finalize()
    body = M.MAGIC + iv + cipher
    return body + hmac.new(hashlib.sha256(('auth:' + CODE).encode()).digest(), body, hashlib.sha256).digest()


def distribution(raw, folder):
    folder.mkdir()
    blob = encrypted(raw)
    payload = folder / 'payload'
    payload.mkdir()
    parts = []
    chunks = [blob[:len(blob)//2], blob[len(blob)//2:]]
    for i, data in enumerate(chunks, 1):
        name = 'part-%04d.bin' % i
        (payload / name).write_bytes(data)
        parts.append({'name': name, 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    (payload / 'parts.json').write_bytes(jbytes({'schema_version': 1, 'format': 'MMCMAX1', 'parts': parts, 'size': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()}))
    (folder / 'private-test-code.txt').write_text(CODE, encoding='utf-8')
    shutil.copyfile(ARGS.installer, folder / 'install.py')
    return blob


def source_tree(folder, version='1.0.0', system='Windows'):
    raw, _, _ = fixture(version)
    archive, infos, canonical = archive_release(raw)
    M.extract_archive(archive, infos, folder)
    release = M.derive_platform(canonical, system)
    M.apply_overlay(release, folder)
    return release


class FakeCLI:
    def __init__(self, cache, previous=None, installed=True, enabled=True, old_cache=None):
        self.calls = []
        self.cache, self.previous, self.installed, self.enabled, self.old_cache = cache, previous, installed, enabled, old_cache
        self.active_root = previous

    def run(self, args, timeout=180, checked=True):
        self.calls.append(args)
        if args[:3] == ['plugin', 'marketplace', 'add']:
            self.active_root = Path(args[3])
        elif args[:3] == ['plugin', 'marketplace', 'remove']:
            self.active_root = None
        return SimpleNamespace(returncode=0, stdout='{}', stderr='')

    def data(self, args, timeout=180):
        self.calls.append(args)
        if args[:3] == ['plugin', 'marketplace', 'list']:
            return {'marketplaces': [{'name': M.MARKET, 'root': str(self.active_root)}] if self.active_root else []}
        if args[:2] == ['plugin', 'list']:
            if self.active_root == self.previous and not self.installed:
                return {'installed': []}
            version = '0.9.0' if self.previous is not None and self.active_root == self.previous else '1.0.0'
            return {'installed': [{'pluginId': M.SPEC, 'installed': True, 'enabled': self.enabled, 'version': version}]}
        if args[:2] == ['plugin', 'add']:
            path = self.old_cache if self.previous is not None and self.active_root == self.previous else self.cache
            return {'installedPath': str(path)}
        raise AssertionError(args)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.folder = WORK / self._testMethodName
        self.folder.mkdir()

    def test_python_39_syntax(self):
        ast.parse(ARGS.installer.read_text('utf-8'), feature_version=(3, 9))

    def test_invitation_plus_and_file(self):
        file = self.folder / 'private-test-code.txt'
        file.write_text(CODE, encoding='utf-8-sig')
        self.assertEqual(CODE, M.resolve_invitation('https://example.invalid/#invite=' + CODE, invite_code_file=file))
        with self.assertRaises(M.InstallError):
            M.resolve_invitation(invite_code=WRONG, invite_code_file=file)
        with self.assertRaises(M.InstallError):
            M.resolve_invitation('https://user:pass@example.invalid/#invite=' + CODE)

    def test_encrypted_roundtrip(self):
        raw, _, _ = fixture()
        data = encrypted(raw)
        decoded, backend = M.decrypt_blob(data, CODE, 'auto')
        self.assertEqual(raw, decoded)
        self.assertIn(backend, ('cryptography', 'openssl'))
        with self.assertRaises(M.InstallError):
            M.decrypt_blob(data, WRONG, 'auto')

    def test_openssl_actual_or_explicit_skip(self):
        candidates = M.openssl_candidates()
        if not candidates:
            if ARGS.require_openssl:
                self.fail('No OpenSSL available for required test')
            self.skipTest('Local OpenSSL unavailable; not claimed as exercised')
        # Independent ciphertext created with cryptography 50.0.1, not OpenSSL.
        raw = b'Baiyi MMCMAX1 independent OpenSSL compatibility vector.\x00\xff'
        iv = bytes(range(16))
        cipher = bytes.fromhex('6e7233a0fe959b485a25b924c8ff62925b8840dac51db520278180db8cf279f45d7ed6b644d9d8dc20d5378a0eb73e10b0a7fa212ead7722c8d1aee1fdabf474')
        self.assertEqual(raw, M.decrypt_openssl(cipher, CODE, iv, candidates[0]))

    def test_openssl_no_secret_argv(self):
        cipher = b'\x01' * 32
        def runner(argv, **kw):
            self.assertNotIn(CODE, repr(argv))
            self.assertNotIn(hashlib.sha256(CODE.encode()).hexdigest(), repr(argv))
            self.assertEqual(CODE.encode() + b'\n', kw['input'])
            self.assertEqual(cipher, Path(argv[-1]).read_bytes())
            self.assertIn('stdin', argv)
            return SimpleNamespace(returncode=0, stdout=b'fixture')
        with patch.object(M.subprocess, 'run', runner):
            self.assertEqual(b'fixture', M.decrypt_openssl(cipher, CODE, bytes(16), '/usr/bin/openssl'))

    def test_parts_corruption(self):
        raw, _, _ = fixture()
        folder = self.folder / 'dist'
        blob = distribution(raw, folder)
        self.assertEqual(blob, M.read_payload(folder)[0])
        first = folder / 'payload/part-0001.bin'
        data = bytearray(first.read_bytes())
        data[4] ^= 1
        first.write_bytes(data)
        with self.assertRaises(M.InstallError):
            M.read_payload(folder)

    def test_whole_cli_five_cases(self):
        valid, _, _ = fixture()
        missing, _, _ = fixture(mutate=lambda files, doc: files.pop(M.PREFIX + 'skills/fixture-000/SKILL.md'))
        slip, _, _ = fixture(mutate=lambda files, doc: files.update({'../escape.txt': b'no'}))
        for name, raw in [('valid', valid), ('wrong', valid), ('corrupt', valid), ('slip', slip), ('missing', missing)]:
            with self.subTest(case=name):
                folder = self.folder / name
                distribution(raw, folder)
                if name == 'wrong':
                    (folder / 'private-test-code.txt').write_text(WRONG)
                elif name == 'corrupt':
                    (folder / 'payload/part-0001.bin').write_bytes(b'damaged')
                result = subprocess.run([sys.executable, '-I', str(folder / 'install.py'), '--invite-code-file', str(folder / 'private-test-code.txt'), '--verify-only', '--crypto-backend', 'auto'], capture_output=True, text=True, encoding='utf-8', timeout=60)
                self.assertEqual(0 if name == 'valid' else 1, result.returncode, result.stderr)
                self.assertNotIn(CODE, result.stdout + result.stderr)
                self.assertNotIn(WRONG, result.stdout + result.stderr)
                if name == 'valid':
                    value = json.loads(result.stdout)
                    self.assertEqual(129, value['skill_entries'])
                    self.assertFalse(value['plugin_installed'])
                else:
                    self.assertFalse(result.stdout)

    def test_zip_links_duplicates_and_traversal(self):
        for name in ('../bad', '/bad', 'a\\b', 'C:/bad', 'a/../bad', 'a/NUL.txt'):
            with self.subTest(name=name), self.assertRaises(M.InstallError):
                M.safe_parts(name)
        for names in (['x', 'X'], ['caf\u00e9.txt', 'cafe\u0301.txt']):
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, 'w') as archive:
                for name in names:
                    archive.writestr(name, b'x')
            with self.assertRaises(M.InstallError):
                M.inspect_zip(buf.getvalue())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as archive:
            info = zipfile.ZipInfo('link')
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, 'elsewhere')
        with self.assertRaises(M.InstallError):
            M.inspect_zip(buf.getvalue())

    def test_overlay_only_authenticated_destination(self):
        raw, _, doc = fixture()
        for mutation in (
            lambda x: x['platform_overlays']['darwin'].update({'plugins/nuphus/evil': x['platform_overlays']['darwin'][M.OVERLAY_DEST]}),
            lambda x: x['platform_overlays']['darwin'][M.OVERLAY_DEST].update({'source': M.PREFIX + '.codex-plugin/plugin.json'}),
            lambda x: x['platform_overlays']['darwin'][M.OVERLAY_DEST].update({'sha256': 'f' * 64}),
        ):
            altered = copy.deepcopy(doc)
            mutation(altered)
            with self.assertRaises(M.InstallError):
                M.parse_release(jbytes(altered))
        archive, infos, canonical = archive_release(raw)
        effective = M.derive_platform(canonical, 'Darwin')
        self.assertNotEqual(canonical['files'][M.OVERLAY_DEST]['sha256'], effective['files'][M.OVERLAY_DEST]['sha256'])
        root = self.folder / 'source'
        M.extract_archive(archive, infos, root)
        M.verify_tree(root, canonical['files'], M.original_manifest_expected(canonical, root))
        M.apply_overlay(effective, root)
        M.verify_tree(root, effective['files'], M.original_manifest_expected(effective, root))
        self.assertEqual((root / M.OVERLAY_SOURCE).read_bytes(), (root / M.OVERLAY_DEST).read_bytes())

    def test_executable_allowlist_and_permissions(self):
        raw, _, _ = fixture(mutate=lambda files, doc: doc['executables'].append(M.PREFIX + '.mcp.json'))
        with self.assertRaises(M.InstallError):
            archive_release(raw)
        release = source_tree(self.folder / 'source', system='Darwin')
        result = M.executable_permissions(release, self.folder / 'source')
        self.assertEqual(3, result['files'])
        self.assertFalse(result['scripts_executed'])
        if os.name != 'nt':
            for name in release['executables']:
                self.assertEqual(0o755, stat.S_IMODE((self.folder / 'source' / name).stat().st_mode))

    def test_final_cache_all_files_no_extras(self):
        release = source_tree(self.folder / 'source')
        for kind in ('good', 'missing', 'corrupt', 'extra'):
            cache = self.folder / kind
            shutil.copytree(self.folder / 'source/plugins/nuphus', cache)
            chosen = cache / 'skills/fixture-000/SKILL.md'
            if kind == 'missing':
                chosen.unlink()
            elif kind == 'corrupt':
                chosen.write_bytes(b'changed')
            elif kind == 'extra':
                (cache / 'extra.txt').write_bytes(b'not listed')
            if kind == 'good':
                self.assertEqual(len(M.plugin_expected(release)), M.verify_cache(release, str(cache))['files'])
            else:
                with self.assertRaises(M.InstallError):
                    M.verify_cache(release, str(cache))

    def test_registration_success_and_cache_failure_cleanup(self):
        root = self.folder / 'source'
        release = source_tree(root)
        cache = self.folder / 'cache'
        shutil.copytree(root / 'plugins/nuphus', cache)
        cli = FakeCLI(cache)
        self.assertEqual('INSTALLED_CACHE_VERIFIED_AND_LISTED', M.register_plugin(root, release, cli, {})['status'])
        (cache / 'skills/fixture-001/SKILL.md').unlink()
        cli = FakeCLI(cache)
        receipt = {}
        with self.assertRaises(M.InstallError):
            M.register_plugin(root, release, cli, receipt)
        self.assertIn(['plugin', 'remove', M.SPEC], cli.calls)
        self.assertIn(['plugin', 'marketplace', 'remove', M.MARKET], cli.calls)
        self.assertEqual('FAILED', receipt['registration']['status'])

    def test_registration_oserror_also_rolls_back(self):
        root = self.folder / 'source'
        release = source_tree(root)
        cli = FakeCLI(self.folder / 'cache')
        with patch.object(M, 'verify_cache', side_effect=PermissionError('not printed')), self.assertRaises(M.InstallError):
            M.register_plugin(root, release, cli, {})
        self.assertIn(['plugin', 'remove', M.SPEC], cli.calls)

    def test_disabled_previous_state_is_preserved(self):
        old = self.folder / 'old'
        source_tree(old, '0.9.0')
        root = self.folder / 'new'
        release = source_tree(root)
        cli = FakeCLI(self.folder / 'cache', old, enabled=False)
        with self.assertRaises(M.InstallError):
            M.register_plugin(root, release, cli, {})
        self.assertFalse(any(call[:3] in (['plugin', 'marketplace', 'remove'], ['plugin', 'marketplace', 'add']) for call in cli.calls))

    def test_existing_update_failure_restores_old(self):
        old = self.folder / 'old'
        source_tree(old, '0.9.0')
        old_cache = self.folder / 'old-cache'
        shutil.copytree(old / 'plugins/nuphus', old_cache)
        root = self.folder / 'new'
        release = source_tree(root)
        cli = FakeCLI(self.folder / 'missing-cache', old, old_cache=old_cache)
        receipt = {}
        with self.assertRaises(M.InstallError):
            M.register_plugin(root, release, cli, receipt)
        self.assertTrue(receipt['registration']['previous_marketplace_restored'])
        self.assertIn(['plugin', 'marketplace', 'add', str(old)], cli.calls)

    def test_existing_uninstalled_not_enabled_on_rollback(self):
        old = self.folder / 'old'
        source_tree(old, '0.9.0')
        root = self.folder / 'new'
        release = source_tree(root)
        cli = FakeCLI(self.folder / 'missing-cache', old, installed=False)
        receipt = {}
        with self.assertRaises(M.InstallError):
            M.register_plugin(root, release, cli, receipt)
        self.assertTrue(receipt['registration']['previous_marketplace_restored'])
        self.assertEqual(1, sum(call[:2] == ['plugin', 'add'] for call in cli.calls))

    def test_mutation_then_cli_failure_is_rolled_back(self):
        for failed_op in ('remove', 'add'):
            with self.subTest(operation=failed_op):
                case = self.folder / failed_op
                case.mkdir()
                old = case / 'old'
                source_tree(old, '0.9.0')
                old_cache = case / 'old-cache'
                shutil.copytree(old / 'plugins/nuphus', old_cache)
                root = case / 'new'
                release = source_tree(root)
                cli = FakeCLI(case / 'missing-cache', old, old_cache=old_cache)
                original = cli.run
                failed = []
                def run(args, *a, **kw):
                    output = original(args, *a, **kw)
                    if args[:3] == ['plugin', 'marketplace', failed_op] and not failed:
                        failed.append(True)
                        raise M.InstallError('Synthetic timeout after mutation')
                    return output
                cli.run = run
                receipt = {}
                with self.assertRaises(M.InstallError):
                    M.register_plugin(root, release, cli, receipt)
                self.assertTrue(receipt['registration']['previous_marketplace_restored'])
                self.assertEqual(old, cli.active_root)

    def test_corrupt_previous_source_blocks_before_mutation(self):
        old = self.folder / 'old'
        source_tree(old, '0.9.0')
        (old / M.PREFIX / 'skills/fixture-001/SKILL.md').unlink()
        root = self.folder / 'new'
        release = source_tree(root)
        cli = FakeCLI(self.folder / 'cache', old)
        with self.assertRaises(M.InstallError):
            M.register_plugin(root, release, cli, {})
        self.assertFalse(any(call[:3] in (['plugin', 'marketplace', 'remove'], ['plugin', 'marketplace', 'add']) for call in cli.calls))

    def test_child_codex_home_never_changes_parent(self):
        before = os.environ.get('CODEX_HOME')
        observed = []
        def runner(argv, **kwargs):
            observed.append(kwargs['env']['CODEX_HOME'])
            return SimpleNamespace(returncode=0, stdout='{}')
        cli = M.CodexCLI('codex', self.folder / 'home', runner=runner)
        cli.run(['--version'])
        self.assertEqual(str(self.folder / 'home'), observed[0])
        self.assertEqual(before, os.environ.get('CODEX_HOME'))

    def test_runtime_limits_arm_intel_old_os_and_rosetta(self):
        raw, _, _ = fixture()
        _, _, release = archive_release(raw)
        release = M.derive_platform(release, 'Darwin')
        for arch, version, expected in [('arm64', '14.0', 'files_present_not_executed'), ('x64', '14.0', 'unsupported_arch'), ('arm64', '13.0', 'requires_macos14')]:
            with patch.object(M, 'darwin_architecture', return_value=(arch, False)), patch.object(M.platform, 'mac_ver', return_value=(version, (), '')):
                result = M.runtime_status(release)
            self.assertEqual(expected, result['status'])
            self.assertEqual(expected != 'files_present_not_executed', result['coreRuntimeUnavailable'])
        with patch.object(M.platform, 'machine', return_value='x86_64'), patch.object(M.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='1\n')):
            self.assertEqual(('arm64', True), M.darwin_architecture())

    def test_existing_root_not_claimed(self):
        root = self.folder / 'existing'
        root.mkdir()
        called = []
        raw, _, _ = fixture()
        archive, infos, _ = archive_release(raw)
        with self.assertRaises(FileExistsError):
            M.extract_archive(archive, infos, root, on_created=lambda: called.append(True))
        self.assertFalse(called)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(InstallerTests))
    report = {'tests': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': [reason for test, reason in result.skipped],
              'installer_sha256': hashlib.sha256(ARGS.installer.read_bytes()).hexdigest(), 'work': str(WORK), 'platform': sys.platform,
              'mcp_or_commercial_software_executed': False, 'paid_api_calls': False, 'real_codex_registration': False}
    (WORK / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if result.wasSuccessful() else 1)
