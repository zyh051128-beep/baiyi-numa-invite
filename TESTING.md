# Windows invitation installer tests

This directory is a new distribution for `nuphus@baiyi-numa-invite`. Do not run the MAXx installer against this payload. No publication is performed by `install.ps1`.

## Package contract

- Windows x64 and 64-bit PowerShell 5.1 or newer.
- `payload/parts.json`: `schema_version: 1`, `format: "MMCMAX1"`, aggregate `size` and `sha256`, and ordered `parts: [{"name":"part-0001.bin","sha256":"<64 hexadecimal digits>","size":123}]`.
- The concatenated blob is ASCII `MMCMAX1`, 16-byte IV, AES-256-CBC/PKCS7 ciphertext, then 32-byte HMAC-SHA256 over the preceding bytes. AES key is SHA256(UTF8(invitation)); HMAC key is SHA256(UTF8("auth:" + invitation)). The checksum index detects damage; HMAC authenticates the complete encrypted payload before decryption/extraction.
- Authenticated ZIP root contains `release-manifest.json`, `.agents/plugins/marketplace.json`, and `plugins/nuphus/**`. Manifest schema: `schema_version: 1`, `plugin: "nuphus"`, `marketplace: "baiyi-numa-invite"`, `files: [{"path":"plugins/nuphus/...","sha256":"<64 hexadecimal digits>","size":123}]`. It lists every file except itself. Paths use `/`.
- Marketplace name is `baiyi-numa-invite`, with exactly one plugin named `nuphus`, local source `./plugins/nuphus`. The installer requires all 129 top-level skills and the bundled MCP, OCR, and Image Blaster essentials; every manifest file is verified, including licenses and supporting assets.
- Current limits: 512 MiB encrypted payload, 20,000 ZIP entries, 512 MiB per member, 2 GiB total expanded files. No ZIP64, encrypted ZIP members, links, special files, unsafe paths, or duplicate/colliding paths.

## Secret handling

Prefer `-InviteCodeFile` with a private UTF-8 text file containing only the invitation code. It avoids putting the code or complete invitation URL in the process command line. The installer does not create this file, display its contents, store the invitation in receipts, or send it to Codex. Do not include that file in a public repository, payload, test report, or issue. `-InviteUrl` and `-InviteCode` remain supported for compatibility but their values appear in the caller's command line/history. If multiple sources are supplied, all must agree.

## Verification and isolated installation

Replace the absolute example paths with the actual local distribution, private code file, and native Codex executable. Example paths are not the publisher's installation locations.

```powershell
# Read-only authentication + full ZIP/manifest hash verification; no extraction,
# Codex invocation, registration, runtime setup, or paid requests.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\Invite\install.ps1' -InviteCodeFile 'C:\Private\baiyi-code.txt' -VerifyOnly

# A fresh disposable receiver installation, isolated from the normal Codex home.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File 'C:\Invite\install.ps1' -InviteCodeFile 'C:\Private\baiyi-code.txt' -InstallRoot 'C:\BaiyiTest\release-001' -CodexHome 'C:\BaiyiTest\codex-home' -CodexPath 'C:\Tools\Codex\codex.exe'
```

`InstallRoot` must be new and must not be a drive root or UNC path. The default is a short unique directory beneath `%USERPROFILE%\baiyi-invited`. Ancestor junctions/symlinks are rejected. Use a short root if an older PowerShell/.NET environment reports path-length failures. An explicitly supplied `CodexHome` directory is created if missing, then applied only to the spawned Codex processes; the invoking shell's environment and normal Codex configuration are not changed. `CodexHome` and `InstallRoot` must be separate, non-nested directories. `-CodexPath` accepts the real `codex.exe`; npm `.cmd` wrappers are not executed through a shell, but the installer can discover the packaged native executable beneath the wrapper's `node_modules/@openai` directory.

Exit status:

- `0`: full verification passed, or installation was confirmed by `codex plugin list`, every final cached plugin file matched the authenticated release, and required system runtime files are present.
- `1`: failed. Inspect the named stage; after extraction starts, a receipt is saved where possible in this attempt's new directory. Nothing is automatically deleted.
- `2`: plugin installation was confirmed, but a required system VC++/UCRT DLL was missing. The receipt gives the official Microsoft remedy. No runtime or license is silently installed.

The installed `installation-state.json` must say `plugin_installed: true`, `extraction_verified: true`, `cache_verified: true`, `skill_entries: 129`, and the expected payload/release hashes. `installed_cache_path` comes from the actual `codex plugin add --json` response. The final cache must contain exactly the manifest's plugin files, with matching sizes/hashes and no links or extra files; source extraction alone is insufficient. Runtime file presence is a limited check: neither exit 0 nor installation proves MCP/OCR, licensed applications, browser interactions, or paid providers were executed.

## Required release tests

1. Parse the script in Windows PowerShell 5.1 and the available PowerShell 7; reject syntax errors before testing the real payload.
2. Correct private code file: verification must succeed and report the complete builder file count; no new plaintext directory or Codex state is created.
3. Wrong code, missing code, conflicting URL/file code: nonzero exit before extraction/registration; reports must not contain any invitation values.
4. Copied test distributions with missing/reordered/corrupted parts, wrong per-part or aggregate size/hash: reject before authentication/extraction. Never alter the release's canonical payload during testing.
5. Authenticated synthetic ZIPs containing `../`, backslash/absolute/drive paths, reserved Windows names, symlinks/reparse metadata, duplicate/case-colliding paths, file/parent conflicts, or differing local/central names: reject before filesystem writes. Test fixtures require a test-only invitation, never the live invitation.
6. Authenticated synthetic manifests with wrong hashes/sizes, an unlisted extra file, a missing file/skill/runtime component, or a different plugin/marketplace identity: reject before registration.
7. Isolated real CLI installation: use both a fresh `InstallRoot` and isolated `CodexHome`; compare every extracted manifest hash, then every plugin file in the actual cache reported by `plugin add --json`, and confirm only `nuphus@baiyi-numa-invite` was added there. Also confirm the parent process `CODEX_HOME` value is unchanged. A truncated cache must fail and trigger rollback; test missing, changed, and extra cached files. Windows path limits can silently truncate a CLI copy even when installation returns success; use a shorter `CodexHome` when indicated, without changing the system long-path policy.
8. Repeat using a second new root and the same isolated Codex home: the invited marketplace should update. Failure injection into CLI registration must either preserve the old marketplace or restore it; no unrelated marketplace is removed.
9. Existing `InstallRoot`, a root with a junction ancestor, and missing/incompatible Codex CLI must fail with an actionable error. No overwrite or fallback to direct configuration editing is allowed.
10. Run recipient package tests from a fresh anonymous download after publication. Preserve only allowlisted version/hash/count/status evidence; do not publish decrypted content, invitations, local credentials, or logs containing private data.

The public installer only uses local distribution files and native Codex plugin commands. GitHub download/hosting, the release builder, and any external runtime installation are outside this script.

The native CLI has no persistent plugin-disable option. If an earlier invited installation is disabled, this installer refuses the upgrade before changing its marketplace or cache; enable it through Codex first if an update is desired. During rollback an earlier installed/enabled plugin is restored and its version/state rechecked. A previously registered but uninstalled marketplace is restored without installing a plugin into it.

## Development evidence (2026-10-07)

PowerShell 5.1 and 7 parser checks passed. Five isolated synthetic-payload checks ran under Windows PowerShell 5.1: an authenticated 129-entry fixture passed full verification; an incorrect invitation, modified payload part, `../` ZIP member, and missing skill entry were rejected at the expected stages. All five outputs were checked for accidental invitation disclosure and contained none. These fixtures contain inert dummy assets, never execute their contents, and establish installer control behavior only. They do not replace the publisher's real-release hash parity, isolated actual Codex installation/update, or post-publication anonymous download tests.

The same five checks also passed after the path-check performance update. Ancestors are still inspected every time, using .NET file attributes with only missing-file/missing-directory exceptions ignored; other failures and reparse points still stop installation. Extracted file hashes use .NET streams and were cross-checked against PowerShell SHA-256. Normal installation reports each stage and file-count progress; `-VerifyOnly` output remains JSON only. An actual receiver test showed that the Codex CLI requires the explicitly selected `CodexHome` directory to exist; the installer now creates it after path checks and before invoking Codex.

Actual receiver testing also found that a long cache path could make the Codex CLI omit two XSD files while reporting success. The installer now validates the complete final cache before reporting installation success, including during restoration of a previously installed release. After this correction, the original five payload fixtures passed again; four direct cache fixtures (matching, missing, changed, extra files) and two mock-CLI registration fixtures (successful parity and missing-file rollback) also passed under PowerShell 5.1. The mock checks did not invoke the real CLI or modify a user profile. Actual short-profile installation and anonymous update remain separate publisher tests.
