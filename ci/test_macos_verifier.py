"""Offline verifier tests. Run with Python -B and the plugin scripts directory."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock
from types import SimpleNamespace

sys.dont_write_bytecode = True
ap = argparse.ArgumentParser()
ap.add_argument("--scripts", type=Path, required=True)
args, remaining = ap.parse_known_args()
sys.path.insert(0, str(args.scripts.resolve()))
import verify_mcp_macos as verifier

STUB = '''import json,sys,time,subprocess
mode=sys.argv[1]
if mode=='timeout':
    time.sleep(60)
if mode=='descendant':
    subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])
    time.sleep(60)
for line in sys.stdin:
    msg=json.loads(line)
    if 'id' not in msg: continue
    method=msg['method']
    if method=='initialize':
        result={'protocolVersion':'2024-11-05','serverInfo':{'name':'local-stub','version':'test'}}
    elif method=='tools/list':
        names=['desktop_screen_size','desktop_perceive']+['stub_'+str(n) for n in range(43)]
        if mode=='bad_count': names.pop()
        result={'tools':[{'name':n,'inputSchema':{'type':'object'}} for n in names]}
    elif msg['params']['name']=='desktop_screen_size':
        result={'structuredContent':{'width':1024,'height':768}}
    else:
        result={'content':[{'type':'text','text':json.dumps({'elements':[{'text':'NUPHUS TEST 12345'}]})}]}
    print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':result}),flush=True)
'''


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="baiyi verifier 中文 ")
        self.root = Path(self.temp.name).resolve()
        self.stub = self.root / "stub.py"
        self.stub.write_text(STUB, encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def check(self, mode="ok", skip=False, image=None):
        report = {}
        command = [sys.executable, "-B", str(self.stub), mode]
        if os.name == "posix":
            # Mocked group tests must never leak their OS facade into real tests.
            self.assertIs(verifier.os, os)
            self.assertNotIsInstance(verifier.os.killpg, mock.Mock)
            self.assertNotIsInstance(verifier.os.getpgrp, mock.Mock)
            verifier.check_server(command, self.root, os.environ.copy(), 1, skip, image, report)
        else:
            # Exercise real stdio/timeouts on Windows; POSIX group signalling
            # is tested separately with mocks and for real on macOS/Linux.
            original_popen = subprocess.Popen
            def popen(*a, **kw):
                kw.pop("start_new_session")
                return original_popen(*a, **kw)
            def stop(p):
                if p.stdin: p.stdin.close()
                if p.poll() is None: p.terminate()
                p.wait(timeout=2)
                return {"own_process_stopped": True, "own_process_group_stopped": True}
            subprocess_facade = SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE,
                                                TimeoutExpired=subprocess.TimeoutExpired)
            with mock.patch.object(verifier, "subprocess", subprocess_facade), \
                    mock.patch.object(verifier, "stop_owned_group", side_effect=stop):
                verifier.check_server(command, self.root, os.environ.copy(), 1, skip, image, report)
        return report

    def test_stdio_default_screen(self):
        report = self.check()
        self.assertEqual(report["tool_count"], 45)
        self.assertEqual(report["screen_size"], {"width": 1024, "height": 768})
        self.assertTrue(report["own_process_stopped"])

    def test_skip_screen_and_synthetic_ocr(self):
        report = self.check(skip=True, image=self.root / "synthetic.png")
        self.assertEqual(report["screen_check"], "skipped_explicitly")
        self.assertNotIn("screen_size", report)
        self.assertEqual(report["offline_ocr"]["expected_text_matches"], 3)

    def test_wrong_tool_count(self):
        with self.assertRaisesRegex(RuntimeError, "45 unique"):
            self.check(mode="bad_count")

    def test_timeout_and_unrelated_process_survives(self):
        sentinel = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                self.check(mode="timeout")
            self.assertLess(time.monotonic() - started, 5)
            self.assertIsNone(sentinel.poll())
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=2)

    @unittest.skipUnless(os.name == "posix", "Real POSIX session/group test needs macOS/Linux")
    def test_timeout_with_real_descendant_group(self):
        with self.assertRaises(TimeoutError):
            self.check(mode="descendant")

    def test_ocr_cannot_pass_on_echoed_path(self):
        with self.assertRaises(RuntimeError):
            verifier.ocr_summary({"content": [{"type": "text", "text":
                json.dumps({"path": "/tmp/NUPHUS TEST 12345.png", "elements": []})}]})


class CleanupTests(unittest.TestCase):
    def test_kills_only_owned_group_even_when_parent_exited(self):
        process = mock.Mock(pid=23456, returncode=0, stdin=None)
        alive = [True]
        calls = []
        def killpg(pgid, sig):
            self.assertEqual(pgid, 23456)
            calls.append(sig)
            if not alive[0]: raise ProcessLookupError()
            if sig == 9: alive[0] = False
        # Replace module references, never mutate the process-wide os/signal
        # modules which the later real subprocess tests also use.
        fake_os = SimpleNamespace(getpgrp=lambda: 111, killpg=killpg)
        fake_signal = SimpleNamespace(SIGTERM=signal.SIGTERM, SIGKILL=9)
        with mock.patch.object(verifier, "os", fake_os), \
                mock.patch.object(verifier, "signal", fake_signal):
            result = verifier.stop_owned_group(process, grace=0)
        self.assertTrue(result["own_process_group_stopped"])
        self.assertIn(signal.SIGTERM, calls)
        self.assertIn(9, calls)

    def test_refuses_callers_group(self):
        process = mock.Mock(pid=111)
        kill = mock.Mock()
        fake_os = SimpleNamespace(getpgrp=lambda: 111, killpg=kill)
        with mock.patch.object(verifier, "os", fake_os):
            with self.assertRaises(RuntimeError):
                verifier.stop_owned_group(process)
            kill.assert_not_called()


class ConfigurationTests(unittest.TestCase):
    def test_rosetta_selects_native_arm64(self):
        with mock.patch.object(verifier.platform, "machine", return_value="x86_64"), \
                mock.patch.object(verifier.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="1\n")):
            self.assertEqual(verifier.runtime_architecture(), "arm64")

    def test_provenance_hash_and_minimum_os(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            runtime = root / "bin/darwin-arm64"
            runtime.mkdir(parents=True)
            records = []
            for name in ("nuphus-mcp", "libonnxruntime.dylib"):
                path = runtime / name
                path.write_bytes(b"local static test fixture")
                path.chmod(0o755)
                records.append({"path": path.relative_to(root).as_posix(),
                    "sha256": verifier.sha256(path), "size": path.stat().st_size,
                    "executable": name == "nuphus-mcp"})
            (runtime / "runtime-provenance.json").write_text(json.dumps({
                "schema_version": 1, "platform": "darwin-arm64", "minimum_macos": "14.0",
                "files": records}), encoding="utf-8")
            (root / "models").mkdir()
            for name in verifier.MODEL_FILES:
                (root / "models" / name).write_bytes(b"synthetic fixture")
            with mock.patch.object(verifier.platform, "mac_ver", return_value=("14.0", (), "")):
                self.assertEqual(verifier.verify_runtime(root, "arm64")["runtime_sha256_verified"], 2)
                (runtime / "libonnxruntime.dylib").write_bytes(b"tampered")
                with self.assertRaisesRegex(RuntimeError, "SHA-256"):
                    verifier.verify_runtime(root, "arm64")
            with mock.patch.object(verifier.platform, "mac_ver", return_value=("13.0", (), "")):
                with self.assertRaisesRegex(RuntimeError, "macOS 14"):
                    verifier.verify_runtime(root, "arm64")

    def test_launch_uses_configured_shell_and_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "start.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            config = {"mcpServers": {verifier.SERVER_NAME: {
                "command": "/bin/sh", "args": ["start.sh"], "cwd": "."}}}
            (root / ".mcp.json").write_text(json.dumps(config), encoding="utf-8")
            command, cwd, _ = verifier.configured_macos_launch(root)
            self.assertEqual(command, ["/bin/sh", "start.sh"])
            self.assertEqual(cwd, root)
            config["mcpServers"][verifier.SERVER_NAME]["command"] = "cmd.exe"
            (root / ".mcp.json").write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "/bin/sh"):
                verifier.configured_macos_launch(root)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]] + remaining)
