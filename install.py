#!/usr/bin/env python3
"""Authenticated Baiyi Numa installer for macOS and Windows.

Uses local distribution files only. No account login, dependency download,
licensed application, paid API, bundled script or MCP program is executed.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import unicodedata
from urllib.parse import unquote, urlsplit
import uuid
import zipfile

MAGIC = b"MMCMAX1"
MARKET = "baiyi-numa-invite"
PLUGIN = "nuphus"
SPEC = PLUGIN + "@" + MARKET
PREFIX = "plugins/nuphus/"
MAX_BLOB = 512 * 1024 * 1024
MAX_TOTAL = 2 * 1024 * 1024 * 1024
MAX_ENTRIES = 20_000
EXPECTED_SKILLS = 129
OVERLAY_DEST = PREFIX + ".mcp.json"
OVERLAY_SOURCE = PREFIX + "platform/macos.mcp.json"
DARWIN_BINARIES = {PREFIX + "bin/darwin-arm64/nuphus-mcp", PREFIX + "bin/darwin-x64/nuphus-mcp"}
ESSENTIAL = [
    ".agents/plugins/marketplace.json", PREFIX + ".codex-plugin/plugin.json",
    PREFIX + ".mcp.json", PREFIX + "THIRD_PARTY_NOTICES.md", PREFIX + "scripts/start-mcp.cmd",
    PREFIX + "bin/win32-x64/nuphus-mcp.exe", PREFIX + "bin/win32-x64/onnxruntime.dll",
    PREFIX + "bin/win32-x64/onnxruntime_providers_shared.dll",
    PREFIX + "models/ch_PP-OCR_keys_v1.txt", PREFIX + "models/ch_PP-OCRv4_det.onnx",
    PREFIX + "models/ch_PP-OCRv4_rec.onnx", PREFIX + "models/icon_detect.onnx",
    PREFIX + "skills/baiyi-numa/SKILL.md",
    PREFIX + "skills/image-blaster/assets/workspace-manifest.json",
    PREFIX + "skills/image-blaster/scripts/prepare_workspace.py",
]


class InstallError(RuntimeError):
    pass


def require_python():
    if platform.python_implementation() != "CPython" or sys.version_info < (3, 9):
        raise InstallError("CPython 3.9 or newer is required. Install a supported Python from python.org, then rerun install.sh.")


def supported_platform():
    system = platform.system()
    if system not in {"Darwin", "Windows"}:
        raise InstallError("This installer supports macOS and Windows. This release does not supply a Linux runtime.")
    if system == "Windows" and (struct.calcsize("P") != 8 or platform.machine().lower() not in {"amd64", "x86_64"}):
        raise InstallError("The Windows release requires a 64-bit Python process on Windows x64.")
    if system == "Darwin" and platform.machine().lower() not in {"arm64", "aarch64", "x86_64", "amd64"}:
        raise InstallError("This macOS installer supports Intel and Apple Silicon.")
    return system


def strict_json(raw, label):
    def pairs(items):
        obj = {}
        for key, value in items:
            if key in obj:
                raise InstallError(label + " contains a duplicate JSON property.")
            obj[key] = value
        return obj
    try:
        value = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw,
                           object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(InstallError(label + " contains non-finite JSON.")))
    except (UnicodeError, ValueError) as exc:
        raise InstallError(label + " is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise InstallError(label + " must be a JSON object.")
    return value


def absolute_path(value):
    path = Path(os.path.abspath(os.path.expanduser(str(value))))
    # macOS standard aliases are not user-created installation redirects.
    if platform.system() == "Darwin":
        for alias in ("/var", "/tmp", "/etc"):
            text = str(path)
            if text == alias or text.startswith(alias + "/"):
                path = Path("/private" + text)
                break
    if path == Path(path.anchor) or (os.name == "nt" and str(path).startswith("\\\\")):
        raise InstallError("Use a dedicated local directory, not a drive root or UNC path.")
    return path


def assert_no_links(path):
    for part in (path, *path.parents):
        try:
            mode = part.lstat()
        except (FileNotFoundError, NotADirectoryError):
            continue
        if stat.S_ISLNK(mode.st_mode) or getattr(mode, "st_file_attributes", 0) & 0x400:
            raise InstallError("A symbolic link or junction was found in an installation/input path.")


def read_regular(path, limit=16 * 1024 * 1024):
    assert_no_links(path)
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
        raise InstallError("A required file is not a regular file or exceeds its size limit: " + path.name)
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise InstallError("A required file grew beyond its allowed size.")
    return data


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise InstallError(label + " must be an integer within the release limits.")
    return value


def checked_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", value):
        raise InstallError("A release SHA-256 is malformed.")
    return value.lower()


def resolve_invitation(invite_url=None, invite_code=None, invite_code_file=None):
    values = []
    if invite_url:
        try:
            url = urlsplit(invite_url.strip())
            if url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None:
                raise ValueError()
            # Preserve literal + in existing fragments; this is not a form submission.
            for component in (url.fragment, url.query):
                for item in component.split("&"):
                    pair = item.split("=", 1)
                    if len(pair) == 2 and pair[0] == "invite" and unquote(pair[1]).strip():
                        values.append(unquote(pair[1]).strip())
        except ValueError as exc:
            raise InstallError("Supply a complete HTTPS invitation URL without embedded user information.") from exc
    if invite_code:
        values.append(invite_code.strip())
    if invite_code_file:
        try:
            values.append(read_regular(absolute_path(invite_code_file), 4096).decode("utf-8-sig").strip())
        except (OSError, UnicodeError) as exc:
            raise InstallError("The private invitation file must be a readable UTF-8 text file.") from exc
    if not values:
        raise InstallError("Supply --invite-code-file, --invite-url with #invite=, or --invite-code. Never publish the invitation file.")
    code = values[0]
    if any(not hmac.compare_digest(code.encode(), value.encode()) for value in values[1:]):
        raise InstallError("The invitation sources contain conflicting values.")
    if not 32 <= len(code) <= 1024 or len(set(code)) < 16 or any(c.isspace() or ord(c) < 32 for c in code):
        raise InstallError("The invitation credential has an invalid format.")
    return code


def read_payload(distribution):
    root = distribution / "payload"
    index = strict_json(read_regular(root / "parts.json", 1024 * 1024), "parts.json")
    if index.get("schema_version") != 1 or index.get("format") != "MMCMAX1":
        raise InstallError("Unsupported payload index schema or format.")
    size = integer(index.get("size"), 71, MAX_BLOB, "Payload size")
    expected = checked_hash(index.get("sha256"))
    parts = index.get("parts")
    if not isinstance(parts, list) or not 1 <= len(parts) <= 256:
        raise InstallError("parts.json must contain an ordered nonempty parts array.")
    seen = set()
    output = io.BytesIO()
    for part in parts:
        if not isinstance(part, dict):
            raise InstallError("Invalid payload part entry.")
        name = part.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name):
            raise InstallError("Payload names must be safe basenames.")
        safe_parts(name)
        if name.casefold() in seen:
            raise InstallError("Duplicate payload part name.")
        seen.add(name.casefold())
        part_size = integer(part.get("size"), 1, MAX_BLOB, "Part size")
        data = read_regular(root / name, part_size)
        if len(data) != part_size or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), checked_hash(part.get("sha256"))):
            raise InstallError("Missing, damaged, or wrong-sized payload part: " + name)
        if output.tell() + len(data) > size:
            raise InstallError("Payload parts exceed the declared aggregate size.")
        output.write(data)
    blob = output.getvalue()
    if len(blob) != size or not hmac.compare_digest(hashlib.sha256(blob).hexdigest(), expected):
        raise InstallError("Combined payload size or checksum failed.")
    return blob, {"payload_sha256": expected, "payload_bytes": size, "payload_parts": len(parts)}


def authenticate_blob(blob, code):
    if len(blob) < 71 or blob[:7] != MAGIC or (len(blob) - 55) % 16:
        raise InstallError("Invalid MMCMAX1 authenticated payload format.")
    auth_key = hashlib.sha256(("auth:" + code).encode("utf-8")).digest()
    actual = hmac.new(auth_key, memoryview(blob)[:-32], hashlib.sha256).digest()
    if not hmac.compare_digest(actual, blob[-32:]):
        raise InstallError("Invitation authentication failed: invalid code or damaged payload.")
    return hashlib.sha256(code.encode("utf-8")).digest(), blob[7:23], blob[23:-32]


def openssl_candidates():
    paths = []
    if platform.system() == "Darwin":
        paths.append(Path("/usr/bin/openssl"))
    found = shutil.which("openssl")
    if found:
        paths.append(Path(found))
    return list(dict.fromkeys(str(path) for path in paths if path.is_file()))


def decrypt_openssl(cipher, code, iv, executable):
    # EVP_BytesToKey with SHA-256, no salt, count=1 yields SHA256(password)
    # as its 32-byte AES key. The explicit public IV replaces the derived IV.
    # Thus this matches MMCMAX1 without passing the invitation or AES key in argv.
    with tempfile.TemporaryDirectory(prefix="baiyi-cipher-") as folder:
        ciphertext_path = Path(folder).resolve() / "ciphertext.bin"
        ciphertext_path.write_bytes(cipher)  # encrypted bytes only, never a secret key or plaintext ZIP
        try:
            result = subprocess.run(
                [executable, "enc", "-d", "-aes-256-cbc", "-nosalt", "-md", "sha256",
                 "-pass", "stdin", "-iv", iv.hex(), "-in", str(ciphertext_path)],
                input=code.encode("utf-8") + b"\n", capture_output=True, timeout=180, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError("The local OpenSSL program could not decrypt the authenticated payload.") from exc
    if result.returncode != 0:
        raise InstallError("The local OpenSSL program rejected the authenticated ciphertext.")
    return result.stdout


def decrypt_cryptography(cipher, key, iv):
    try:
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise InstallError("The optional cryptography package is not installed in this Python interpreter.") from exc
    try:
        decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        padded = decryptor.update(cipher) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as exc:
        raise InstallError("The authenticated ciphertext failed AES padding verification.") from exc


def decrypt_blob(blob, code, backend="auto"):
    key, iv, cipher = authenticate_blob(blob, code)
    order = [backend] if backend != "auto" else (["openssl", "cryptography"] if platform.system() == "Darwin" else ["cryptography", "openssl"])
    failures = []
    for name in order:
        try:
            if name == "openssl":
                candidates = openssl_candidates()
                if not candidates:
                    raise InstallError("No local OpenSSL executable is available.")
                for executable in candidates:
                    try:
                        return decrypt_openssl(cipher, code, iv, executable), "openssl"
                    except InstallError as exc:
                        failures.append(str(exc))
                continue
            return decrypt_cryptography(cipher, key, iv), "cryptography"
        except InstallError as exc:
            failures.append(str(exc))
    raise InstallError("No usable local AES backend. Use macOS /usr/bin/openssl, or create a private Python virtual environment and install cryptography from its official package distribution before rerunning with that interpreter. Nothing was automatically downloaded. " + " ".join(failures))


def safe_parts(name):
    if not isinstance(name, str) or not name or "\\" in name or name.startswith("/") or ":" in name:
        raise InstallError("Unsafe ZIP or manifest path.")
    parts = name.rstrip("/").split("/")
    if name.endswith("//") or any(part in {"", ".", ".."} for part in parts):
        raise InstallError("Unsafe ZIP path traversal or empty component.")
    for part in parts:
        if part.endswith((".", " ")) or any(ord(c) < 32 or c in '<>"|?*' for c in part) or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)", part, re.I):
            raise InstallError("A ZIP or manifest path is not portable.")
    return tuple(parts)


def path_key(name):
    return unicodedata.normalize("NFD", name.rstrip("/")).casefold()


def inspect_zip(raw):
    end = -1
    for offset in range(len(raw) - 22, max(-1, len(raw) - 65558), -1):
        if raw[offset:offset + 4] == b"PK\x05\x06" and offset + 22 + struct.unpack_from("<H", raw, offset + 20)[0] == len(raw):
            end = offset
            break
    if end < 0:
        raise InstallError("Invalid ZIP end directory.")
    disk, cd_disk, disk_count, count, cd_size, cd_start = struct.unpack_from("<4H2I", raw, end + 4)
    if disk or cd_disk or disk_count != count or not 1 <= count <= MAX_ENTRIES or cd_start + cd_size != end:
        raise InstallError("Invalid ZIP directory or unsupported multi-disk/ZIP64 archive.")
    archive = zipfile.ZipFile(io.BytesIO(raw), "r")
    infos = archive.infolist()
    if len(infos) != count:
        raise InstallError("ZIP parsers disagree on the entry count.")
    seen, files, ranges = set(), set(), []
    total = 0
    for info in infos:
        safe_parts(info.orig_filename)
        if info.orig_filename != info.filename:
            raise InstallError("Original ZIP member names must not require normalization.")
        key = path_key(info.orig_filename)
        if key in seen:
            raise InstallError("Duplicate, case-colliding or Unicode-colliding ZIP paths.")
        seen.add(key)
        mode = (info.external_attr >> 16) & 0xFFFF
        kind = stat.S_IFMT(mode)
        if kind not in {0, stat.S_IFREG, stat.S_IFDIR} or info.external_attr & 0x400 or (kind == stat.S_IFDIR and not info.is_dir()) or (kind == stat.S_IFREG and info.is_dir()):
            raise InstallError("ZIP links, reparse points and special files are forbidden.")
        if info.flag_bits & 0x2041 or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise InstallError("Unsupported ZIP encryption or compression.")
        integer(info.file_size, 0, MAX_BLOB, "ZIP member size")
        integer(info.compress_size, 0, MAX_BLOB, "ZIP compressed size")
        if info.is_dir() and info.file_size:
            raise InstallError("ZIP directory has nonempty content.")
        total += info.file_size
        if total > MAX_TOTAL:
            raise InstallError("ZIP exceeds its expanded size limit.")
        local = info.header_offset
        if local < 0 or local + 30 > cd_start or raw[local:local + 4] != b"PK\x03\x04":
            raise InstallError("Invalid ZIP local header.")
        flags, method = struct.unpack_from("<HH", raw, local + 6)
        name_len, extra_len = struct.unpack_from("<HH", raw, local + 26)
        data_end = local + 30 + name_len + extra_len + info.compress_size
        try:
            local_name = raw[local + 30:local + 30 + name_len].decode("utf-8" if flags & 0x800 else "cp437")
        except UnicodeError as exc:
            raise InstallError("Invalid ZIP local name encoding.") from exc
        if flags != info.flag_bits or method != info.compress_type or local_name != info.orig_filename or data_end > cd_start:
            raise InstallError("ZIP local and central metadata disagree.")
        ranges.append((local, data_end))
        if not info.is_dir():
            files.add(key)
    intervals = sorted(ranges)
    if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
        raise InstallError("Overlapping ZIP members are forbidden.")
    for info in infos:
        parts = safe_parts(info.orig_filename)
        if any(path_key("/".join(parts[:n])) in files for n in range(1, len(parts))):
            raise InstallError("ZIP file and parent-directory paths conflict.")
    return archive, infos


def parse_release(raw):
    document = strict_json(raw, "release-manifest.json")
    if document.get("schema_version") != 1 or document.get("plugin") != PLUGIN or document.get("marketplace") != MARKET:
        raise InstallError("Unexpected release manifest version or identity.")
    entries = document.get("files")
    if not isinstance(entries, list) or not entries:
        raise InstallError("Release files must be a nonempty array.")
    files, seen = {}, set()
    for item in entries:
        if not isinstance(item, dict):
            raise InstallError("Invalid release file entry.")
        name = item.get("path")
        safe_parts(name)
        if name.endswith("/") or not (name == ".agents/plugins/marketplace.json" or name.startswith(PREFIX)) or path_key(name) in seen:
            raise InstallError("Unexpected or duplicate release file path.")
        seen.add(path_key(name))
        files[name] = {"path": name, "size": integer(item.get("size"), 0, MAX_BLOB, "Manifest file size"), "sha256": checked_hash(item.get("sha256"))}
    if any(name not in files for name in ESSENTIAL):
        raise InstallError("The release is missing a required complete-plugin component.")
    skills = sum(bool(re.fullmatch(r"plugins/nuphus/skills/[^/]+/SKILL\.md", name)) for name in files)
    if skills != EXPECTED_SKILLS:
        raise InstallError("This release must retain all 129 skill entries; found " + str(skills) + ".")
    overlays = document.get("platform_overlays", {})
    if not isinstance(overlays, dict) or set(overlays) - {"darwin"}:
        raise InstallError("Only the allowlisted Darwin platform overlay is supported.")
    darwin = overlays.get("darwin", {})
    if not isinstance(darwin, dict) or set(darwin) - {OVERLAY_DEST}:
        raise InstallError("The platform overlay destination is not allowlisted.")
    for destination, item in darwin.items():
        if not isinstance(item, dict) or item.get("source") != OVERLAY_SOURCE or destination not in files or OVERLAY_SOURCE not in files:
            raise InstallError("The Darwin overlay must use its authenticated macos.mcp.json source.")
        source = files[OVERLAY_SOURCE]
        if item.get("size") != source["size"] or checked_hash(item.get("sha256")) != source["sha256"]:
            raise InstallError("The platform overlay disagrees with its authenticated source file.")
    executables = document.get("executables", [])
    if not isinstance(executables, list) or any(not isinstance(name, str) or name not in files or not name.startswith(PREFIX) for name in executables) or len(executables) != len(set(executables)):
        raise InstallError("Executable permissions must refer to unique authenticated plugin files.")
    return {"document": document, "files": files, "original_files": copy.deepcopy(files), "manifest_sha256": hashlib.sha256(raw).hexdigest(), "skill_count": skills, "executables": executables, "applied_overlays": []}


def check_executable_content(release, read_prefix):
    for name in release["executables"]:
        if name not in DARWIN_BINARIES and read_prefix(name, 2) != b"#!":
            raise InstallError("An executable declaration is neither an authenticated shebang script nor an allowlisted Darwin binary: " + name)


def verify_archive(archive, infos, progress=None):
    entries = {info.filename: info for info in infos if not info.is_dir()}
    manifest = entries.get("release-manifest.json")
    if manifest is None or manifest.file_size > 16 * 1024 * 1024:
        raise InstallError("The authenticated archive has no valid release manifest.")
    release = parse_release(archive.read(manifest))
    if set(entries) != set(release["files"]) | {"release-manifest.json"}:
        raise InstallError("The release manifest must list every ZIP file except itself, with no extras.")
    for index, (name, expected) in enumerate(release["files"].items(), 1):
        info = entries[name]
        if info.file_size != expected["size"]:
            raise InstallError("Authenticated archive file size differs: " + name)
        digest, total = hashlib.sha256(), 0
        with archive.open(info) as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                total += len(chunk)
                if total > expected["size"]:
                    raise InstallError("Archive member expanded beyond its declared size.")
                digest.update(chunk)
        if total != expected["size"] or digest.hexdigest() != expected["sha256"]:
            raise InstallError("Authenticated archive file checksum differs: " + name)
        if progress and index % 1000 == 0:
            progress("Checked archive files: %d / %d" % (index, len(entries) - 1))
    def prefix(name, count):
        with archive.open(name) as handle:
            return handle.read(count)
    check_executable_content(release, prefix)
    marketplace = strict_json(archive.read(".agents/plugins/marketplace.json"), "marketplace.json")
    check_marketplace(marketplace)
    plugin = strict_json(archive.read(PREFIX + ".codex-plugin/plugin.json"), "plugin.json")
    version = plugin.get("version")
    if plugin.get("name") != PLUGIN or plugin.get("mcpServers") != "./.mcp.json" or not isinstance(version, str) or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,127}", version):
        raise InstallError("The plugin manifest has an invalid identity, version or MCP reference.")
    release["version"] = version
    return release


def check_marketplace(marketplace):
    entries = marketplace.get("plugins")
    if marketplace.get("name") != MARKET or not isinstance(entries, list) or len(entries) != 1 or not isinstance(entries[0], dict) or entries[0].get("name") != PLUGIN or entries[0].get("source") != {"source": "local", "path": "./plugins/nuphus"}:
        raise InstallError("The invited marketplace must contain only the expected local nuphus plugin.")


def derive_platform(release, system):
    effective = copy.deepcopy(release)
    if system == "Darwin":
        overlay = release["document"].get("platform_overlays", {}).get("darwin", {})
        if set(overlay) != {OVERLAY_DEST}:
            raise InstallError("This downloaded release lacks the authenticated macOS configuration. Download the current invitation repository; do not attempt to run the Windows MCP command on macOS.")
        source = release["files"][OVERLAY_SOURCE]
        effective["files"][OVERLAY_DEST] = {"path": OVERLAY_DEST, "size": source["size"], "sha256": source["sha256"]}
        effective["applied_overlays"] = [{"destination": OVERLAY_DEST, "source": OVERLAY_SOURCE, "size": source["size"], "sha256": source["sha256"]}]
    effective["recipient_platform"] = system
    return effective


def extract_archive(archive, infos, root, progress=None, on_created=None):
    assert_no_links(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    if on_created:
        on_created()
    for index, info in enumerate(infos, 1):
        target = root.joinpath(*safe_parts(info.filename))
        assert_no_links(target)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        count = 0
        with archive.open(info) as source, target.open("xb") as output:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                count += len(chunk)
                if count > info.file_size:
                    raise InstallError("Extracted file exceeds its declared size.")
                output.write(chunk)
        if count != info.file_size:
            raise InstallError("Extracted file has an unexpected size.")
        if os.name != "nt":
            target.chmod(0o644)  # execution is granted only from the authenticated allowlist
        if progress and index % 1000 == 0:
            progress("Extracted files: %d / %d" % (index, len(infos)))


def tree_inventory(root):
    assert_no_links(root)
    if not root.is_dir():
        raise InstallError("The expected installation/cache directory is missing.")
    found = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        assert_no_links(directory)
        with os.scandir(directory) as scan:
            for entry in scan:
                mode = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(mode.st_mode) or getattr(mode, "st_file_attributes", 0) & 0x400:
                    raise InstallError("A link or reparse point was found in the installed tree.")
                path = Path(entry.path)
                if stat.S_ISDIR(mode.st_mode):
                    pending.append(path)
                elif stat.S_ISREG(mode.st_mode):
                    relative = path.relative_to(root).as_posix()
                    key = path_key(relative)
                    if key in found:
                        raise InstallError("Case or Unicode path collision in the installed tree.")
                    found[key] = (relative, path, mode.st_size)
                else:
                    raise InstallError("Special files are not allowed in the installed tree.")
    return found


def verify_tree(root, expected, extra=None, progress=None):
    complete = dict(expected)
    complete.update(extra or {})
    actual = tree_inventory(root)
    expected_keys = {path_key(name): name for name in complete}
    missing = set(expected_keys) - set(actual)
    surplus = set(actual) - set(expected_keys)
    if missing:
        name = expected_keys[sorted(missing)[0]]
        raise InstallError("Installed file is missing: %s (full path length %d). On Windows retry with a shorter --codex-home; do not change system policy." % (name, len(str(root / name))))
    if surplus:
        raise InstallError("Installed tree contains an unlisted extra file: " + actual[sorted(surplus)[0]][0])
    for index, (name, item) in enumerate(complete.items(), 1):
        _, path, size = actual[path_key(name)]
        assert_no_links(path)
        if size != item["size"] or digest_file(path) != item["sha256"]:
            raise InstallError("Installed file size or SHA-256 differs: " + name)
        if progress and index % 1000 == 0:
            progress("Verified installed files: %d / %d" % (index, len(complete)))
    return len(complete)


def original_manifest_expected(release, root):
    path = root / "release-manifest.json"
    return {"release-manifest.json": {"size": path.stat().st_size, "sha256": release["manifest_sha256"]}}


def apply_overlay(release, root):
    for overlay in release["applied_overlays"]:
        source = root.joinpath(*safe_parts(overlay["source"]))
        destination = root.joinpath(*safe_parts(overlay["destination"]))
        data = read_regular(source)
        if len(data) != overlay["size"] or hashlib.sha256(data).hexdigest() != overlay["sha256"]:
            raise InstallError("The authenticated overlay source changed before platform preparation.")
        assert_no_links(destination)
        with destination.open("wb") as handle:
            handle.write(data)


def executable_permissions(release, root, cache=False):
    if release["recipient_platform"] != "Darwin":
        return {"status": "NOT_REQUIRED_ON_WINDOWS", "files": 0}
    for name in release["executables"]:
        relative = name[len(PREFIX):] if cache else name
        path = root.joinpath(*safe_parts(relative))
        assert_no_links(path)
        if name not in DARWIN_BINARIES:
            with path.open("rb") as handle:
                if handle.read(2) != b"#!":
                    raise InstallError("The executable allowlist file lost its authenticated shebang.")
        path.chmod(0o755)
        if not os.access(path, os.X_OK):
            raise InstallError("The authenticated executable could not be made runnable: " + relative)
    return {"status": "ALLOWLIST_EXECUTABLE_BITS_VERIFIED", "files": len(release["executables"]), "scripts_executed": False}


def darwin_architecture():
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "arm64", False
    try:
        result = subprocess.run(["/usr/sbin/sysctl", "-n", "hw.optional.arm64"], capture_output=True, text=True, timeout=10, check=False)
        if result.returncode == 0 and result.stdout.strip() == "1":
            return "arm64", True
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "x64", False


def runtime_status(release):
    result = {"platform": release["recipient_platform"], "coreRuntimeUnavailable": False,
              "mcp_execution": "NOT_RUN", "ocr_execution": "NOT_RUN", "permissions": "NOT_GRANTED_BY_INSTALLER",
              "commercial_software_and_paid_apis": "NOT_RUN", "dependency_installation": "NOT_RUN"}
    if release["recipient_platform"] == "Windows":
        system = Path(os.environ.get("SystemRoot", "C:\\Windows")) / "System32"
        missing = [name for name in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll", "msvcp140_1.dll", "ucrtbase.dll") if not (system / name).is_file()]
        result.update(missing_libraries=missing, coreRuntimeUnavailable=bool(missing), status="requires_vc_runtime" if missing else "files_present_not_executed")
        return result
    arch, rosetta = darwin_architecture()
    version = platform.mac_ver()[0]
    try:
        major = int(version.split(".")[0])
    except ValueError:
        major = 0
    needed = [PREFIX + "scripts/start-mcp.sh", PREFIX + "bin/darwin-" + arch + "/nuphus-mcp", PREFIX + "bin/darwin-" + arch + "/libonnxruntime.dylib"]
    missing = [name for name in needed if name not in release["files"]]
    status = "unsupported_arch" if missing and arch == "x64" else ("runtime_files_missing" if missing else ("requires_macos14" if major < 14 else "files_present_not_executed"))
    result.update(architecture=arch, python_under_rosetta=rosetta, macos_version=version,
                  minimum_macos="14.0", missing_files=missing, status=status,
                  coreRuntimeUnavailable=bool(missing or major < 14),
                  action="Skills remain installed. A compatible native MCP build and macOS 14+ are required for the desktop/OCR tools. Grant Accessibility, Screen Recording and Automation only through macOS when needed; this installer does not bypass Gatekeeper, quarantine or TCC.")
    return result


def find_codex(explicit=None):
    if explicit:
        candidates = [Path(explicit).expanduser()]
    else:
        candidates = []
        found = shutil.which("codex.exe" if os.name == "nt" else "codex")
        if found:
            candidates.append(Path(found))
        if platform.system() == "Darwin":
            for app in (Path("/Applications/Codex.app"), Path.home() / "Applications/Codex.app"):
                candidates.append(app / "Contents/Resources/codex")
            candidates += [Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex")]
        elif os.name == "nt":
            app_bin = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
            if app_bin.is_dir():
                candidates += sorted(app_bin.glob("*/codex.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
            wrapper = shutil.which("codex.cmd")
            if wrapper:
                package = Path(wrapper).parent / "node_modules/@openai"
                if package.is_dir():
                    candidates += [p for p in package.rglob("codex.exe") if "win32-x64" in str(p) or "x86_64-pc-windows" in str(p)]
    for candidate in candidates:
        # Executable symlinks from npm/Homebrew are normal; resolve this program
        # only. Installation/output trees retain the strict no-link policy.
        if candidate.is_file() and (os.name == "nt" and candidate.name.lower() == "codex.exe" or os.name != "nt" and os.access(candidate, os.X_OK)):
            return str(candidate.resolve())
    raise InstallError("Codex CLI was not found. Install/open the official Codex app or official @openai/codex CLI, then pass --codex-path to its actual executable. It must support plugin marketplace and plugin add commands.")


class CodexCLI:
    def __init__(self, executable, home=None, runner=subprocess.run):
        self.executable, self.runner = executable, runner
        self.env = os.environ.copy()
        if home is not None:
            self.env["CODEX_HOME"] = str(home)

    def run(self, args, timeout=180, checked=True):
        try:
            result = self.runner([self.executable, *args], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", env=self.env,
                                 timeout=timeout, check=False,
                                 **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}))
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InstallError("Codex CLI could not finish the required operation: " + " ".join(args[:3])) from exc
        if checked and result.returncode:
            raise InstallError("Codex CLI failed during %s (exit %s). Check CLI compatibility and directory permissions. Raw output is suppressed." % (" ".join(args[:3]), result.returncode))
        return result

    def data(self, args, timeout=180):
        return strict_json(self.run(args, timeout).stdout, "Codex JSON result")


def plugin_expected(release):
    return {name[len(PREFIX):]: item for name, item in release["files"].items() if name.startswith(PREFIX)}


def verify_cache(release, cache, progress=None):
    root = absolute_path(cache)
    count = verify_tree(root, plugin_expected(release), progress=progress)
    modes = executable_permissions(release, root, cache=True)
    return {"status": "FULL_FILE_PARITY_VERIFIED", "path": str(root), "files": count, "executable_permissions": modes}


def installed_entry(cli, version=None):
    data = cli.data(["plugin", "list", "--marketplace", MARKET, "--json"])
    entries = data.get("installed")
    if not isinstance(entries, list) or len(entries) > 1 or any(not isinstance(item, dict) or item.get("pluginId") != SPEC for item in entries):
        raise InstallError("The invited marketplace contains an unexpected installed-plugin state.")
    if version is not None and (len(entries) != 1 or entries[0].get("installed") is not True or entries[0].get("enabled") is not True or entries[0].get("version") != version):
        raise InstallError("Codex did not confirm the expected installed, enabled plugin version.")
    return entries[0] if entries else None


def restore_release(root, system):
    raw = read_regular(root / "release-manifest.json")
    release = parse_release(raw)
    plugin = strict_json(read_regular(root / "plugins/nuphus/.codex-plugin/plugin.json"), "previous plugin.json")
    release["version"] = plugin.get("version")
    release = derive_platform(release, system)
    extras = original_manifest_expected(release, root)
    for name in ("recipient-manifest.json", "installation-state.json"):
        path = root / name
        if path.is_file():
            extras[name] = {"size": path.stat().st_size, "sha256": digest_file(path)}
    verify_tree(root, release["files"], extras)
    return release


def registered_marketplace_root(cli):
    listing = cli.data(["plugin", "marketplace", "list", "--json"])
    markets = listing.get("marketplaces")
    if not isinstance(markets, list) or any(not isinstance(item, dict) for item in markets):
        raise InstallError("Codex did not return a valid marketplace list.")
    matches = [item for item in markets if item.get("name") == MARKET]
    if len(matches) > 1:
        raise InstallError("Duplicate invited marketplaces prevent safe replacement.")
    if matches:
        if not isinstance(matches[0].get("root"), str):
            raise InstallError("The previous marketplace has no recoverable root.")
        return absolute_path(matches[0]["root"])
    return None


def register_plugin(root, release, cli, receipt, progress=None):
    cli.run(["--version"], 30)
    previous, old_plugin = registered_marketplace_root(cli), None
    if previous:
        check_marketplace(strict_json(read_regular(previous / ".agents/plugins/marketplace.json"), "previous marketplace.json"))
        old_plugin = installed_entry(cli)
        if old_plugin and (old_plugin.get("installed") is not True or old_plugin.get("enabled") is not True):
            raise InstallError("The previous invited plugin is disabled or unreadable. This CLI cannot preserve a disabled state when reinstalling; enable it in Codex first if you want this update. No marketplace was changed.")
        if old_plugin:
            if progress:
                progress("Checking that the previous release can be restored before changing registration...")
            previous_release = restore_release(previous, release["recipient_platform"])
            if previous_release["version"] != old_plugin.get("version"):
                raise InstallError("The previous source version differs from the installed version; safe rollback cannot be guaranteed. No marketplace was changed.")
    remove_attempted = add_attempted = False
    try:
        if previous:
            remove_attempted = True
            cli.run(["plugin", "marketplace", "remove", MARKET])
        add_attempted = True
        cli.run(["plugin", "marketplace", "add", str(root)])
        data = cli.data(["plugin", "add", SPEC, "--json"], 300)
        cache = data.get("installedPath")
        if not isinstance(cache, str) or not cache:
            raise InstallError("Codex did not report its actual installedPath; cache completeness cannot be confirmed.")
        receipt["installed_cache_path"] = cache
        if progress:
            progress("Checking all files in the actual Codex plugin cache...")
        verified = verify_cache(release, cache, progress)
        installed_entry(cli, release["version"])
        return {"status": "INSTALLED_CACHE_VERIFIED_AND_LISTED", "cache_verification": verified,
                "previous_marketplace_root": str(previous) if previous else None,
                "previous_marketplace_replaced": remove_attempted, "previous_plugin_installed": bool(old_plugin)}
    except (InstallError, OSError, ValueError) as exc:
        restored = False
        cleaned = False
        try:
            # A failed/timeout CLI call may already have changed registration.
            # Inspect actual state before cleanup; never touch a concurrent root.
            current = registered_marketplace_root(cli)
            if current not in (None, root, previous):
                raise InstallError("The marketplace was concurrently changed to another root; rollback stopped.")
            if current == root or (current is None and add_attempted):
                cli.run(["plugin", "remove", SPEC], checked=False)
                if current == root:
                    cli.run(["plugin", "marketplace", "remove", MARKET], checked=False)
                current = registered_marketplace_root(cli)
                if current == root:
                    raise InstallError("The failed marketplace could not be removed.")
            if previous and (remove_attempted or add_attempted):
                if current is None:
                    cli.run(["plugin", "marketplace", "add", str(previous)])
                elif current != previous:
                    raise InstallError("The previous marketplace cannot safely be restored.")
                if old_plugin:
                    previous_release = restore_release(previous, release["recipient_platform"])
                    result = cli.data(["plugin", "add", SPEC, "--json"], 300)
                    verify_cache(previous_release, result.get("installedPath", ""), progress)
                    installed_entry(cli, old_plugin.get("version"))
                restored = registered_marketplace_root(cli) == previous
            else:
                cleaned = current is None
        except (InstallError, OSError, ValueError):
            restored = False
        receipt["registration"] = {"status": "FAILED", "rollback_attempted": remove_attempted or add_attempted,
                                    "previous_marketplace_restored": restored,
                                    "failed_first_install_registration_removed": cleaned,
                                    "previous_marketplace_root": str(previous) if previous else None}
        detail = str(exc) if isinstance(exc, InstallError) else "A filesystem or cache-validation operation failed (" + type(exc).__name__ + ")."
        raise InstallError(detail + " Previous invited marketplace restored: " + str(restored) + ".") from exc


def write_json(path, value, new=False):
    assert_no_links(path)
    with path.open("x" if new else "w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--invite-url")
    parser.add_argument("--invite-code")
    parser.add_argument("--invite-code-file", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--install-root", type=Path)
    parser.add_argument("--codex-home", type=Path)
    parser.add_argument("--codex-path")
    parser.add_argument("--crypto-backend", choices=("auto", "openssl", "cryptography"), default="auto")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    root, archive, receipt = None, None, None
    created = False
    stage = "platform"
    def progress(message):
        if not args.verify_only:
            print(message, file=sys.stderr, flush=True)
    try:
        require_python()
        system = supported_platform()
        stage = "invitation"
        code = resolve_invitation(args.invite_url, args.invite_code, args.invite_code_file)
        stage = "payload-checksums"
        progress("Checking encrypted payload parts...")
        blob, payload = read_payload(Path(__file__).resolve().parent)
        stage = "payload-authentication"
        progress("Authenticating and decrypting the complete release...")
        raw, backend = decrypt_blob(blob, code, args.crypto_backend)
        code = args.invite_code = args.invite_url = None
        del blob
        stage = "archive-validation"
        progress("Checking original archive paths, complete inventory and all hashes...")
        archive, infos = inspect_zip(raw)
        canonical = verify_archive(archive, infos, progress)
        release = derive_platform(canonical, system)
        runtime = runtime_status(release)
        receipt = {"schema_version": 1, "status": "VERIFIED", "plugin": PLUGIN, "marketplace": MARKET,
                   "version": release["version"], "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                   **payload, "crypto_backend": backend, "release_manifest_sha256": release["manifest_sha256"],
                   "verified_files": len(release["files"]), "plugin_files": len(plugin_expected(release)),
                   "skill_entries": release["skill_count"], "recipient_platform": system,
                   "applied_overlays": release["applied_overlays"], "plugin_installed": False,
                   "extraction_verified": False, "cache_verified": False, "registration": "NOT_RUN", "runtime": runtime}
        if args.verify_only:
            print(json.dumps(receipt, ensure_ascii=False, indent=2))
            return 0
        stage = "installation-paths"
        root = absolute_path(args.install_root or Path.home() / "baiyi-invited" / uuid.uuid4().hex[:12])
        assert_no_links(root)
        if root.exists():
            raise InstallError("Install root already exists. Choose a new directory; no existing files are overwritten.")
        home = absolute_path(args.codex_home) if args.codex_home else None
        if home:
            assert_no_links(home)
            if home == root or home in root.parents or root in home.parents:
                raise InstallError("Codex home and install root must be separate, non-nested directories.")
            home.mkdir(mode=0o700, parents=True, exist_ok=True)
        stage = "codex-discovery"
        executable = find_codex(args.codex_path)
        stage = "extraction"
        progress("Extracting all authenticated files into the new installation directory...")
        # Mark ownership only after our exclusive mkdir succeeds. A competing
        # creator must never cause us to write a receipt in somebody else's tree.
        def record_created():
            nonlocal created
            created = True
        extract_archive(archive, infos, root, progress, record_created)
        stage = "original-source-parity"
        verify_tree(root, canonical["files"], original_manifest_expected(canonical, root), progress)
        stage = "platform-preparation"
        progress("Preparing only the authenticated platform overlay and executable permissions...")
        apply_overlay(release, root)
        verify_tree(root, release["files"], original_manifest_expected(release, root), progress)
        modes = executable_permissions(release, root)
        recipient = {"schema_version": 1, "platform": system, "source_manifest_sha256": release["manifest_sha256"],
                     "applied_overlays": release["applied_overlays"], "executables": release["executables"],
                     "files": list(release["files"].values())}
        write_json(root / "recipient-manifest.json", recipient, new=True)
        receipt.update(marketplace_root=str(root), extraction_verified=True, executable_permissions=modes,
                       recipient_manifest_sha256=digest_file(root / "recipient-manifest.json"),
                       codex_home_override=str(home) if home else None, codex_executable=executable)
        write_json(root / "installation-state.json", receipt, new=True)
        stage = "codex-registration-and-cache-verification"
        progress("Registering the invited marketplace and checking the final Codex cache...")
        receipt["registration"] = register_plugin(root, release, CodexCLI(executable, home), receipt, progress)
        receipt.update(plugin_installed=True, cache_verified=True,
                       status="INSTALLED_RUNTIME_INCOMPLETE" if runtime["coreRuntimeUnavailable"] else "INSTALLED")
        write_json(root / "installation-state.json", receipt)
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        progress("Receipt: " + str(root / "installation-state.json") + ". Restart Codex and open a new ordinary chat.")
        return 2 if runtime["coreRuntimeUnavailable"] else 0
    except (InstallError, OSError, ValueError, RuntimeError, zipfile.BadZipFile) as exc:
        if created and root is not None and receipt is not None:
            receipt.update(status="FAILED", failed_stage=stage)
            try:
                write_json(root / "installation-state.json", receipt)
            except (OSError, InstallError):
                pass
        # No tracebacks, credential values, URLs or raw provider/CLI logs are printed.
        message = str(exc) if isinstance(exc, InstallError) else "Filesystem, archive or local-program operation failed (" + type(exc).__name__ + "). Check file permissions and path lengths."
        print("Installation failed at " + stage + ": " + message, file=sys.stderr)
        if created:
            print("The new installation directory is retained for diagnosis: " + str(root), file=sys.stderr)
        return 1
    finally:
        if archive is not None:
            archive.close()


if __name__ == "__main__":
    raise SystemExit(main())
