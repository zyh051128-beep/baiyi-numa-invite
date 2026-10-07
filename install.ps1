#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$InviteUrl,
    [string]$InviteCode,
    [string]$InviteCodeFile,
    [switch]$VerifyOnly,
    [string]$InstallRoot,
    [string]$CodexHome,
    [string]$CodexPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
$script:MarketName = 'baiyi-numa-invite'
$script:PluginSpec = 'nuphus@baiyi-numa-invite'
$script:ChildCodexHome = $null
$script:Cli = $null
$script:CreatedRoot = $null
$script:Receipt = $null
$script:CurrentStage = 'platform'
$script:MaxBlob = 536870912L
$script:MaxExpanded = 2147483648L

function Get-Property($Object, [string]$Name) {
    if ($null -eq $Object) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -ne $property) { return ,$property.Value }
    return $null
}

function Assert-WindowsX64 {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw 'This release contains a Windows x64 MCP runtime. Run this installer on Windows x64.'
    }
    $architecture = [Environment]::GetEnvironmentVariable('PROCESSOR_ARCHITECTURE', 'Machine')
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITEW6432 }
    if (-not $architecture) { $architecture = $env:PROCESSOR_ARCHITECTURE }
    if ($architecture -ne 'AMD64' -or -not [Environment]::Is64BitOperatingSystem -or -not [Environment]::Is64BitProcess) {
        throw 'Windows x64 and a 64-bit PowerShell process are required. On x64 Windows use Windows PowerShell from System32 (Sysnative from a 32-bit process). ARM64 is not a supported release target.'
    }
}

function Get-LocalPath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path) -or -not [IO.Path]::IsPathRooted($Path)) { throw 'An absolute local filesystem path is required.' }
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full -notmatch '^[A-Za-z]:\\' -or $full.Length -le 3) { throw 'Use an absolute directory on a local Windows drive, not a drive root or UNC path.' }
    return $full
}

function Assert-NoReparsePoints([string]$Path) {
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
        try {
            $attributes = [IO.File]::GetAttributes($current)
            if ($attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'A symbolic link or junction was found in an input or installation path.' }
        } catch [IO.FileNotFoundException] {
            # A new leaf may not exist yet; its existing ancestors still matter.
        } catch [IO.DirectoryNotFoundException] {
            # Walk every ancestor; permission and other I/O failures propagate.
        }
        $parent = [IO.Path]::GetDirectoryName($current.TrimEnd('\'))
        if ($parent -eq $current) { break }
        $current = $parent
    }
}

function Show-InstallStage([string]$Message) {
    if (-not $VerifyOnly) { Write-Host $Message }
}

function Show-InstallCount([string]$Activity, [int]$Current, [int]$Total) {
    if (-not $VerifyOnly -and ($Current % 250 -eq 0 -or $Current -eq $Total)) {
        Write-Progress -Id 1 -Activity $Activity -Status ($Current.ToString() + ' / ' + $Total + ' files') -PercentComplete ([int](100.0 * $Current / [Math]::Max(1,$Total)))
        if ($Current -eq $Total) { Write-Progress -Id 1 -Activity $Activity -Completed }
    }
}

function Read-JsonText([string]$Text, [string]$Label) {
    try { $value = ConvertFrom-Json -InputObject $Text -ErrorAction Stop }
    catch { throw ('Invalid JSON in ' + $Label + '.') }
    if ($null -eq $value -or $value -is [Array] -or $value -is [string] -or $value -is [ValueType]) { throw ($Label + ' must be a JSON object.') }
    return $value
}

function Read-JsonFile([string]$Path, [string]$Label, [long]$Limit = 16777216) {
    Assert-NoReparsePoints $Path
    if (-not [IO.File]::Exists($Path) -or (Get-Item -LiteralPath $Path).Length -gt $Limit) { throw ($Label + ' is missing or exceeds its size limit.') }
    return Read-JsonText ([IO.File]::ReadAllText($Path, [Text.UTF8Encoding]::new($false, $true))) $Label
}

function Get-Sha256([byte[]]$Bytes) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ,$sha.ComputeHash($Bytes) } finally { $sha.Dispose() }
}

function Get-Hex([byte[]]$Bytes) { return ([BitConverter]::ToString($Bytes)).Replace('-', '').ToLowerInvariant() }

function Get-FileDigest([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return Get-Hex ($sha.ComputeHash($stream)) }
    finally { $sha.Dispose(); $stream.Dispose() }
}

function Test-FixedBytes([byte[]]$Left, [byte[]]$Right) {
    if ($Left.Length -ne $Right.Length) { return $false }
    $difference = 0
    for ($i = 0; $i -lt $Left.Length; $i++) { $difference = $difference -bor ($Left[$i] -bxor $Right[$i]) }
    return $difference -eq 0
}

function Resolve-Invitation {
    $values = [Collections.Generic.List[string]]::new()
    if ($InviteUrl) {
        $uri = $null
        if (-not [Uri]::TryCreate($InviteUrl, [UriKind]::Absolute, [ref]$uri) -or $uri.Scheme -ne 'https' -or $uri.UserInfo) {
            throw 'InviteUrl must be an HTTPS invitation URL without embedded user information.'
        }
        # Parse locally only. The invitation URL is never requested or printed.
        foreach ($component in @($uri.Fragment.TrimStart('#'), $uri.Query.TrimStart('?'))) {
            foreach ($part in ($component -split '&')) {
                $pair = $part -split '=', 2
                if ($pair.Count -eq 2 -and $pair[0] -ceq 'invite') {
                    $value = [Uri]::UnescapeDataString($pair[1]).Trim()
                    if ($value) { $values.Add($value) }
                }
            }
        }
    }
    if ($InviteCode) { $values.Add($InviteCode.Trim()) }
    if ($InviteCodeFile) {
        $path = Get-LocalPath $InviteCodeFile
        Assert-NoReparsePoints $path
        if (-not [IO.File]::Exists($path) -or (Get-Item -LiteralPath $path).Length -gt 4096) { throw 'InviteCodeFile must be a regular UTF-8 text file of at most 4096 bytes.' }
        $values.Add([IO.File]::ReadAllText($path, [Text.UTF8Encoding]::new($false, $true)).Trim())
    }
    if ($values.Count -eq 0) { throw 'Supply the complete -InviteUrl, -InviteCode, or preferably -InviteCodeFile pointing to a privately created UTF-8 file.' }
    $code = $values[0]
    foreach ($value in $values) { if ($value -cne $code) { throw 'The supplied invitation sources contain conflicting values.' } }
    if ($code.Length -lt 32 -or $code.Length -gt 1024 -or $code -match '\s' -or @($code.ToCharArray() | Select-Object -Unique).Count -lt 16) {
        throw 'The invitation code has an invalid format.'
    }
    return $code
}

function Assert-Integer($Value, [long]$Minimum, [long]$Maximum, [string]$Label) {
    if ($null -eq $Value -or $Value -is [bool] -or $Value -is [string] -or $Value -isnot [ValueType]) { throw ($Label + ' must be an integer.') }
    try { $number = [decimal]$Value } catch { throw ($Label + ' must be an integer.') }
    if ($number -ne [Math]::Truncate($number) -or $number -lt $Minimum -or $number -gt $Maximum) { throw ($Label + ' is outside its allowed range.') }
    return [long]$number
}

function Read-Payload {
    $payloadRoot = Join-Path $PSScriptRoot 'payload'
    $index = Read-JsonFile (Join-Path $payloadRoot 'parts.json') 'parts.json' 1048576
    if ((Get-Property $index 'schema_version') -ne 1 -or (Get-Property $index 'format') -cne 'MMCMAX1') { throw 'Unsupported payload index version or format.' }
    $parts = Get-Property $index 'parts'
    if ($parts -isnot [Array] -or $parts.Count -lt 1 -or $parts.Count -gt 256) { throw 'parts.json must contain an ordered nonempty parts array.' }
    $totalSize = Assert-Integer (Get-Property $index 'size') 71 $script:MaxBlob 'Payload size'
    $totalHash = Get-Property $index 'sha256'
    if ($totalHash -isnot [string] -or $totalHash -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid payload SHA-256.' }
    $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $buffer = [IO.MemoryStream]::new()
    try {
        [long]$sum = 0
        foreach ($part in $parts) {
            $name = Get-Property $part 'name'
            $hash = Get-Property $part 'sha256'
            $size = Assert-Integer (Get-Property $part 'size') 1 $script:MaxBlob 'Part size'
            if ($name -isnot [string] -or $name -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$' -or $name -match '[. ]$' -or $name -match '^(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)' -or -not $seen.Add($name)) { throw 'Payload part names must be unique safe basenames.' }
            if ($hash -isnot [string] -or $hash -notmatch '^[0-9a-fA-F]{64}$') { throw 'Invalid part SHA-256.' }
            $path = Join-Path $payloadRoot $name
            Assert-NoReparsePoints $path
            if (-not [IO.File]::Exists($path) -or (Get-Item -LiteralPath $path).Length -ne $size) { throw ('Missing or wrong-sized payload part: ' + $name) }
            $sum += $size
            if ($sum -gt $totalSize) { throw 'Payload parts exceed the declared aggregate size.' }
            $data = [IO.File]::ReadAllBytes($path)
            if ((Get-Hex (Get-Sha256 $data)) -ine $hash) { throw ('Payload part checksum failed: ' + $name) }
            $buffer.Write($data, 0, $data.Length)
        }
        if ($sum -ne $totalSize) { throw 'Payload parts do not match the aggregate size.' }
        $blob = $buffer.ToArray()
        if ((Get-Hex (Get-Sha256 $blob)) -ine $totalHash) { throw 'Combined payload checksum failed.' }
        return [pscustomobject]@{ Bytes=$blob; Sha256=$totalHash.ToLowerInvariant(); Size=$sum; PartCount=$parts.Count }
    } finally { $buffer.Dispose() }
}

function Unlock-Payload([byte[]]$Blob, [string]$Code) {
    $magic = [Text.Encoding]::ASCII.GetBytes('MMCMAX1')
    if ($Blob.Length -lt 71 -or [Text.Encoding]::ASCII.GetString($Blob, 0, 7) -cne 'MMCMAX1' -or ($Blob.Length - 55) % 16 -ne 0) { throw 'Invalid authenticated payload format.' }
    $key = Get-Sha256 ([Text.Encoding]::UTF8.GetBytes($Code))
    $authKey = Get-Sha256 ([Text.Encoding]::UTF8.GetBytes('auth:' + $Code))
    $hmac = New-Object Security.Cryptography.HMACSHA256(,$authKey)
    try { $actual = $hmac.ComputeHash($Blob, 0, $Blob.Length - 32) } finally { $hmac.Dispose() }
    $expected = New-Object byte[] 32
    [Array]::Copy($Blob, $Blob.Length - 32, $expected, 0, 32)
    if (-not (Test-FixedBytes $actual $expected)) { throw 'Invitation authentication failed: invalid code or damaged payload.' }
    $iv = New-Object byte[] 16
    [Array]::Copy($Blob, 7, $iv, 0, 16)
    $aes = [Security.Cryptography.Aes]::Create()
    try {
        $aes.Key = $key; $aes.IV = $iv
        $aes.Mode = [Security.Cryptography.CipherMode]::CBC
        $aes.Padding = [Security.Cryptography.PaddingMode]::PKCS7
        $decryptor = $aes.CreateDecryptor()
        try { return ,$decryptor.TransformFinalBlock($Blob, 23, $Blob.Length - 55) }
        finally { $decryptor.Dispose() }
    } finally {
        $aes.Dispose()
        [Array]::Clear($key, 0, $key.Length)
        [Array]::Clear($authKey, 0, $authKey.Length)
    }
}

function Assert-PortableMember([string]$Name) {
    if ([string]::IsNullOrEmpty($Name) -or $Name.Contains('\') -or $Name.StartsWith('/') -or $Name.Contains(':')) { throw 'Unsafe ZIP or manifest member path.' }
    $logical = $Name.TrimEnd('/')
    if (-not $logical -or $Name.EndsWith('//')) { throw 'Unsafe empty ZIP or manifest member path.' }
    foreach ($part in ($logical -split '/')) {
        if ($part -in @('', '.', '..') -or $part -match '[. ]$' -or $part -match '[\x00-\x1f<>"|?*]' -or $part -match '^(?i:con|prn|aux|nul|com[1-9\u00b9\u00b2\u00b3]|lpt[1-9\u00b9\u00b2\u00b3])(?:\.|$)') { throw 'Unsafe ZIP or manifest path component.' }
    }
}

function Get-RawZipInventory([byte[]]$Bytes) {
    $end = -1
    for ($offset = $Bytes.Length - 22; $offset -ge [Math]::Max(0, $Bytes.Length - 65557); $offset--) {
        if ([BitConverter]::ToUInt32($Bytes, $offset) -eq 0x06054b50 -and $offset + 22 + [BitConverter]::ToUInt16($Bytes, $offset + 20) -eq $Bytes.Length) { $end = $offset; break }
    }
    if ($end -lt 0) { throw 'Invalid ZIP end directory.' }
    $count = [int][BitConverter]::ToUInt16($Bytes, $end + 10)
    [long]$directorySize = [BitConverter]::ToUInt32($Bytes, $end + 12)
    [long]$position = [BitConverter]::ToUInt32($Bytes, $end + 16)
    $directoryStart = $position
    if ([BitConverter]::ToUInt16($Bytes, $end + 4) -ne 0 -or [BitConverter]::ToUInt16($Bytes, $end + 6) -ne 0 -or [BitConverter]::ToUInt16($Bytes, $end + 8) -ne $count -or $count -le 0 -or $count -gt 20000 -or $position + $directorySize -ne $end) { throw 'Invalid ZIP directory count/size, multi-disk archive, or unsupported ZIP64 archive.' }
    $members = [Collections.Generic.List[object]]::new()
    $paths = [Collections.Generic.Dictionary[string,bool]]::new([StringComparer]::OrdinalIgnoreCase)
    [long]$total = 0
    for ($index = 0; $index -lt $count; $index++) {
        if ($position + 46 -gt $end -or [BitConverter]::ToUInt32($Bytes, [int]$position) -ne 0x02014b50) { throw 'Invalid ZIP central directory entry.' }
        $flags = [BitConverter]::ToUInt16($Bytes, [int]$position + 8)
        $method = [BitConverter]::ToUInt16($Bytes, [int]$position + 10)
        [long]$compressed = [BitConverter]::ToUInt32($Bytes, [int]$position + 20)
        [long]$length = [BitConverter]::ToUInt32($Bytes, [int]$position + 24)
        $nameSize = [BitConverter]::ToUInt16($Bytes, [int]$position + 28)
        $extraSize = [BitConverter]::ToUInt16($Bytes, [int]$position + 30)
        $commentSize = [BitConverter]::ToUInt16($Bytes, [int]$position + 32)
        [long]$attributes = [BitConverter]::ToUInt32($Bytes, [int]$position + 38)
        [long]$local = [BitConverter]::ToUInt32($Bytes, [int]$position + 42)
        if ($position + 46 + $nameSize + $extraSize + $commentSize -gt $end -or $nameSize -eq 0 -or ($flags -band 8257) -ne 0 -or $method -notin @(0,8) -or [BitConverter]::ToUInt16($Bytes, [int]$position + 34) -ne 0) { throw 'Unsupported ZIP member metadata.' }
        $encoding = if ($flags -band 2048) { [Text.UTF8Encoding]::new($false, $true) } else { [Text.Encoding]::GetEncoding(437) }
        $name = $encoding.GetString($Bytes, [int]$position + 46, $nameSize)
        Assert-PortableMember $name
        $isDirectory = $name.EndsWith('/')
        $type = ($attributes -shr 16) -band 61440
        if ($type -notin @(0,32768,16384) -or ($attributes -band 1024) -ne 0 -or ($type -eq 16384 -and -not $isDirectory) -or ($type -eq 32768 -and $isDirectory)) { throw 'ZIP links, special files, or inconsistent member types are forbidden.' }
        if ($length -gt $script:MaxBlob -or $compressed -gt $script:MaxBlob -or ($isDirectory -and $length -ne 0)) { throw 'ZIP member exceeds its size limit.' }
        $total += $length
        if ($total -gt $script:MaxExpanded) { throw 'ZIP exceeds its expanded size limit.' }
        $key = $name.TrimEnd('/').Normalize([Text.NormalizationForm]::FormC)
        if ($paths.ContainsKey($key)) { throw 'Duplicate, case-colliding, or normalization-colliding ZIP paths are forbidden.' }
        $paths.Add($key, $isDirectory)
        if ($local + 30 -gt $directoryStart -or [BitConverter]::ToUInt32($Bytes, [int]$local) -ne 0x04034b50) { throw 'Invalid ZIP local header.' }
        $localNameSize = [BitConverter]::ToUInt16($Bytes, [int]$local + 26)
        $localExtraSize = [BitConverter]::ToUInt16($Bytes, [int]$local + 28)
        if ($localNameSize -ne $nameSize -or $local + 30 + $localNameSize + $localExtraSize + $compressed -gt $directoryStart -or [BitConverter]::ToUInt16($Bytes, [int]$local + 6) -ne $flags -or [BitConverter]::ToUInt16($Bytes, [int]$local + 8) -ne $method) { throw 'ZIP local and central metadata disagree.' }
        for ($character = 0; $character -lt $nameSize; $character++) {
            if ($Bytes[[int]$local + 30 + $character] -ne $Bytes[[int]$position + 46 + $character]) { throw 'ZIP local and central names disagree.' }
        }
        $members.Add([pscustomobject]@{Name=$name; IsDirectory=$isDirectory; Length=$length; CompressedLength=$compressed})
        $position += 46 + $nameSize + $extraSize + $commentSize
    }
    if ($position -ne $end) { throw 'ZIP directory length does not match its entries.' }
    foreach ($key in $paths.Keys) {
        $parts = $key -split '/'
        for ($level = 1; $level -lt $parts.Count; $level++) {
            $ancestor = $parts[0..($level - 1)] -join '/'
            if ($paths.ContainsKey($ancestor) -and -not $paths[$ancestor]) { throw 'ZIP file and directory paths conflict.' }
        }
    }
    return $members.ToArray()
}

function Get-EntryBytes($Entry, [long]$Limit = 16777216) {
    if ($Entry.Length -gt $Limit) { throw 'JSON document in ZIP exceeds its size limit.' }
    $source = $Entry.Open(); $memory = [IO.MemoryStream]::new()
    try { $source.CopyTo($memory); return ,$memory.ToArray() } finally { $source.Dispose(); $memory.Dispose() }
}

function Read-EntryJson($Entry, [string]$Label) {
    return Read-JsonText ([Text.UTF8Encoding]::new($false, $true).GetString((Get-EntryBytes $Entry))) $Label
}

function Verify-Archive($Archive, [object[]]$Inventory) {
    if ($Archive.Entries.Count -ne $Inventory.Count) { throw 'ZIP parsers disagree on entry count.' }
    $entries = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
    for ($i = 0; $i -lt $Inventory.Count; $i++) {
        $entry = $Archive.Entries[$i]; $item = $Inventory[$i]
        if ($entry.FullName -cne $item.Name -or $entry.Length -ne $item.Length -or $entry.CompressedLength -ne $item.CompressedLength) { throw 'ZIP parsers disagree on member metadata.' }
        if (-not $item.IsDirectory) { $entries.Add($item.Name, $entry) }
    }
    if (-not $entries.ContainsKey('release-manifest.json')) { throw 'The authenticated package has no release-manifest.json.' }
    $release = Read-EntryJson $entries['release-manifest.json'] 'release-manifest.json'
    if ((Get-Property $release 'schema_version') -ne 1 -or (Get-Property $release 'plugin') -cne 'nuphus' -or (Get-Property $release 'marketplace') -cne $script:MarketName) { throw 'The release manifest has an unexpected version or identity.' }
    $files = Get-Property $release 'files'
    if ($files -isnot [Array] -or $files.Count -ne $entries.Count - 1) { throw 'The release manifest must list every archive file except itself, exactly once.' }
    $declared = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    [long]$totalBytes = 0; $skillCount = 0; $pluginFiles = 0; $verifiedCount = 0
    foreach ($file in $files) {
        $path = Get-Property $file 'path'; $hash = Get-Property $file 'sha256'
        $size = Assert-Integer (Get-Property $file 'size') 0 $script:MaxBlob 'Manifest file size'
        if ($path -isnot [string]) { throw 'Manifest path must be a string.' }
        Assert-PortableMember $path
        if ($path.EndsWith('/') -or -not ($path -ceq '.agents/plugins/marketplace.json' -or $path.StartsWith('plugins/nuphus/', [StringComparison]::Ordinal)) -or -not $declared.Add($path.Normalize([Text.NormalizationForm]::FormC))) { throw 'Manifest contains a duplicate or unexpected path.' }
        if ($hash -isnot [string] -or $hash -notmatch '^[0-9a-fA-F]{64}$' -or -not $entries.ContainsKey($path)) { throw 'Manifest file or SHA-256 is invalid.' }
        $entry = $entries[$path]
        if ($entry.Length -ne $size) { throw ('Manifest size mismatch: ' + $path) }
        $source = $entry.Open(); $sha = [Security.Cryptography.SHA256]::Create()
        try { $actual = Get-Hex ($sha.ComputeHash($source)) } finally { $sha.Dispose(); $source.Dispose() }
        if ($actual -ine $hash) { throw ('Manifest file checksum failed: ' + $path) }
        $totalBytes += $size
        if ($path.StartsWith('plugins/nuphus/', [StringComparison]::Ordinal)) { $pluginFiles++ }
        if ($path -cmatch '^plugins/nuphus/skills/[^/]+/SKILL\.md$') { $skillCount++ }
        $verifiedCount++
        Show-InstallCount 'Verifying authenticated archive files' $verifiedCount $files.Count
    }
    if ($skillCount -ne 129) { throw ('This release must retain all 129 skill entries; found ' + $skillCount + '.') }
    $essential = @('.agents/plugins/marketplace.json','plugins/nuphus/.codex-plugin/plugin.json','plugins/nuphus/.mcp.json','plugins/nuphus/THIRD_PARTY_NOTICES.md','plugins/nuphus/scripts/start-mcp.cmd','plugins/nuphus/bin/win32-x64/nuphus-mcp.exe','plugins/nuphus/bin/win32-x64/onnxruntime.dll','plugins/nuphus/bin/win32-x64/onnxruntime_providers_shared.dll','plugins/nuphus/models/ch_PP-OCR_keys_v1.txt','plugins/nuphus/models/ch_PP-OCRv4_det.onnx','plugins/nuphus/models/ch_PP-OCRv4_rec.onnx','plugins/nuphus/models/icon_detect.onnx','plugins/nuphus/skills/baiyi-numa/SKILL.md','plugins/nuphus/skills/image-blaster/assets/workspace-manifest.json','plugins/nuphus/skills/image-blaster/scripts/prepare_workspace.py')
    foreach ($path in $essential) { if (-not $entries.ContainsKey($path)) { throw ('Required complete-release component is missing: ' + $path) } }
    $market = Read-EntryJson $entries['.agents/plugins/marketplace.json'] 'marketplace.json'
    $plugins = Get-Property $market 'plugins'
    if ((Get-Property $market 'name') -cne $script:MarketName -or $plugins -isnot [Array] -or $plugins.Count -ne 1) { throw 'The marketplace identity or plugin count is invalid.' }
    $source = Get-Property $plugins[0] 'source'
    if ((Get-Property $plugins[0] 'name') -cne 'nuphus' -or (Get-Property $source 'source') -cne 'local' -or (Get-Property $source 'path') -cne './plugins/nuphus') { throw 'The marketplace must reference only ./plugins/nuphus.' }
    $plugin = Read-EntryJson $entries['plugins/nuphus/.codex-plugin/plugin.json'] 'plugin.json'
    $version = Get-Property $plugin 'version'
    if ((Get-Property $plugin 'name') -cne 'nuphus' -or $version -isnot [string] -or $version -notmatch '^[0-9A-Za-z][0-9A-Za-z.+-]{0,127}$' -or (Get-Property $plugin 'mcpServers') -cne './.mcp.json') { throw 'Invalid plugin identity, version, or MCP manifest reference.' }
    return [pscustomobject]@{Version=$version; FileCount=$files.Count; PluginFileCount=$pluginFiles; SkillCount=$skillCount; ExpandedBytes=$totalBytes; ManifestSha256=(Get-Hex (Get-Sha256 (Get-EntryBytes $entries['release-manifest.json']))); Files=$files}
}

function Get-RuntimeStatus {
    $checks = @()
    foreach ($name in @('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','msvcp140_1.dll','ucrtbase.dll')) {
        $path = Join-Path ([Environment]::SystemDirectory) $name
        $checks += [ordered]@{name=$name; present=[IO.File]::Exists($path); check='SYSTEM32_FILE_PRESENCE_ONLY'}
    }
    $missing = @($checks | Where-Object { -not $_.present } | ForEach-Object {$_.name})
    return [ordered]@{platform='windows-x64'; system_libraries=$checks; missing_libraries=$missing; mcp_runtime='BUNDLED_FILES_VERIFIED_NOT_EXECUTED'; ocr_models='BUNDLED_FILES_VERIFIED_NOT_EXECUTED'; dependency_installation='NOT_RUN'; licensed_software_and_paid_apis='NOT_RUN'; action= $(if ($missing.Count) {'Install the official Microsoft Visual C++ 2015-2022 x64 Redistributable and supported Windows updates, then recheck. https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist'} else {'Runtime file presence is confirmed; MCP startup, OCR and external applications still require task-specific execution checks.'})}
}

function Expand-VerifiedArchive($Archive, [object[]]$Inventory, [string]$Destination) {
    Assert-NoReparsePoints $Destination
    if (Test-Path -LiteralPath $Destination) { throw 'InstallRoot already exists. Choose a new dedicated directory; existing files will not be overwritten.' }
    [void][IO.Directory]::CreateDirectory($Destination)
    $script:CreatedRoot = $Destination
    $prefix = $Destination.TrimEnd('\') + '\'
    for ($i = 0; $i -lt $Inventory.Count; $i++) {
        $item = $Inventory[$i]; $entry = $Archive.Entries[$i]
        $target = [IO.Path]::GetFullPath((Join-Path $Destination $item.Name.Replace('/','\')))
        if (-not $target.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'ZIP extraction escaped the installation root.' }
        Assert-NoReparsePoints $target
        if ($item.IsDirectory) { [void][IO.Directory]::CreateDirectory($target); continue }
        [void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($target))
        $source = $entry.Open()
        try {
            $output = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try {
                $buffer = New-Object byte[] 65536; [long]$written = 0
                while (($read = $source.Read($buffer, 0, $buffer.Length)) -gt 0) {
                    $written += $read
                    if ($written -gt $item.Length) { throw 'Extracted file exceeds its declared size.' }
                    $output.Write($buffer, 0, $read)
                }
                if ($written -ne $item.Length) { throw 'Extracted file has an unexpected length.' }
            } finally { $output.Dispose() }
        } finally { $source.Dispose() }
        Show-InstallCount 'Extracting the complete plugin release' ($i + 1) $Inventory.Count
    }
}

function Test-InstalledParity($Release, [string]$Root) {
    $actual = @(Get-ChildItem -LiteralPath $Root -Recurse -Force -File)
    if ($actual.Count -ne $Release.FileCount + 1) { throw 'Extracted file inventory differs from the verified release.' }
    $verifiedCount = 0
    foreach ($file in $Release.Files) {
        $path = Join-Path $Root $file.path.Replace('/','\')
        Assert-NoReparsePoints $path
        if (-not [IO.File]::Exists($path) -or [IO.FileInfo]::new($path).Length -ne $file.size -or (Get-FileDigest $path) -ine $file.sha256) { throw ('Extracted file failed complete-release verification: ' + $file.path) }
        $verifiedCount++
        Show-InstallCount 'Verifying extracted file hashes' $verifiedCount $Release.FileCount
    }
    if ((Get-FileDigest (Join-Path $Root 'release-manifest.json')) -ine $Release.ManifestSha256) { throw 'Extracted release manifest checksum failed.' }
}

function Test-CodexCacheParity($Release, [string]$CacheRoot) {
    try {
        $root = Get-LocalPath $CacheRoot
        Assert-NoReparsePoints $root
        if (-not [IO.Directory]::Exists($root)) { throw 'The installedPath reported by Codex is not an existing directory.' }
        $prefix = $root + '\'
        $actual = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::Ordinal)
        $pending = [Collections.Generic.Stack[string]]::new()
        $pending.Push($root)
        while ($pending.Count -gt 0) {
            $directory = $pending.Pop()
            # Enumerate one level at a time so a junction is rejected before traversal.
            Assert-NoReparsePoints $directory
            foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($directory)) {
                $attributes = [IO.File]::GetAttributes($entry)
                if ($attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'A reparse point was found in the final Codex cache.' }
                if ($attributes -band [IO.FileAttributes]::Directory) { $pending.Push($entry); continue }
                $full = [IO.Path]::GetFullPath($entry)
                if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'A final cache entry escaped its reported root.' }
                $relative = $full.Substring($prefix.Length).Replace('\','/')
                if ($actual.ContainsKey($relative)) { throw 'The final Codex cache contains duplicate file paths.' }
                $actual.Add($relative,$full)
            }
        }
        $expected = @($Release.Files | Where-Object { $_.path.StartsWith('plugins/nuphus/',[StringComparison]::Ordinal) })
        if ($expected.Count -ne $Release.PluginFileCount) { throw 'The verified release has an inconsistent plugin file count.' }
        $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        $verifiedCount = 0
        foreach ($file in $expected) {
            $relative = $file.path.Substring('plugins/nuphus/'.Length)
            if (-not $seen.Add($relative)) { throw 'The final-cache file list contains a duplicate path.' }
            if (-not $actual.ContainsKey($relative)) {
                $targetLength = $prefix.Length + $relative.Length
                throw ('The final Codex cache is missing ' + $relative + ' (full path length ' + $targetLength + ').')
            }
            $path = $actual[$relative]
            Assert-NoReparsePoints $path
            if ([IO.FileInfo]::new($path).Length -ne $file.size -or (Get-FileDigest $path) -ine $file.sha256) { throw ('The final Codex cache file differs from the authenticated release: ' + $relative) }
            $verifiedCount++
            Show-InstallCount 'Verifying the final Codex plugin cache' $verifiedCount $expected.Count
        }
        if ($actual.Count -ne $seen.Count) {
            $extra = @($actual.Keys | Where-Object { -not $seen.Contains($_) } | Select-Object -First 1)
            throw ('The final Codex cache contains an unlisted extra file: ' + $extra[0])
        }
        return [ordered]@{status='FULL_FILE_PARITY_VERIFIED'; path=$root; files=$verifiedCount}
    } catch {
        throw ('Final Codex cache verification failed. ' + $_.Exception.Message + ' Windows long-path copying can silently omit files; retry with a shorter -CodexHome directory. The installer does not change the system long-path policy.')
    }
}

function Find-Codex {
    $candidates = [Collections.Generic.List[string]]::new()
    if ($CodexPath) { $candidates.Add((Get-LocalPath $CodexPath)) }
    else {
        $command = Get-Command codex.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { $candidates.Add($command.Source) }
        $appBin = Join-Path $env:LOCALAPPDATA 'OpenAI\Codex\bin'
        if (Test-Path -LiteralPath $appBin -PathType Container) {
            foreach ($exe in (Get-ChildItem -LiteralPath $appBin -Filter codex.exe -Recurse -File | Sort-Object LastWriteTimeUtc -Descending)) { $candidates.Add($exe.FullName) }
        }
        $wrapper = Get-Command codex.cmd -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($wrapper) {
            $npmPackage = Join-Path ([IO.Path]::GetDirectoryName($wrapper.Source)) 'node_modules\@openai'
            if (Test-Path -LiteralPath $npmPackage -PathType Container) {
                foreach ($exe in (Get-ChildItem -LiteralPath $npmPackage -Filter codex.exe -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match '(win32-x64|x86_64-pc-windows)' })) { $candidates.Add($exe.FullName) }
            }
        }
    }
    foreach ($candidate in $candidates) {
        if ([IO.File]::Exists($candidate) -and [IO.Path]::GetFileName($candidate) -ieq 'codex.exe') {
            Assert-NoReparsePoints $candidate
            return $candidate
        }
    }
    throw 'A real Codex CLI executable was not found. Install/open the official Codex desktop app, or install the official @openai/codex CLI, then pass -CodexPath with the absolute path to codex.exe. The CLI must support plugin marketplace and plugin add. No configuration has been changed.'
}

function Quote-WindowsArgument([string]$Value) {
    # CommandLineToArgvW-compatible quoting, without a shell or cmd.exe.
    return '"' + [regex]::Replace([regex]::Replace($Value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}

function Invoke-Codex([string[]]$Arguments, [int]$TimeoutSeconds = 180) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $script:Cli
    $start.Arguments = (($Arguments | ForEach-Object { Quote-WindowsArgument $_ }) -join ' ')
    $start.UseShellExecute = $false; $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true; $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8; $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    if ($script:ChildCodexHome) { $start.EnvironmentVariables['CODEX_HOME'] = $script:ChildCodexHome }
    $process = [Diagnostics.Process]::new(); $process.StartInfo = $start
    try {
        if (-not $process.Start()) { throw 'Codex CLI failed to start.' }
        $stdout = $process.StandardOutput.ReadToEndAsync(); $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill(); $process.WaitForExit() } catch { }
            throw 'Codex CLI exceeded the operation timeout.'
        }
        return [pscustomobject]@{ExitCode=$process.ExitCode; Stdout=$stdout.Result; Stderr=$stderr.Result}
    } finally { $process.Dispose() }
}

function Assert-CodexSuccess($Result, [string]$Operation) {
    if ($Result.ExitCode -ne 0) { throw ('Codex CLI failed during ' + $Operation + ' (exit ' + $Result.ExitCode + '). Check that this Codex CLI version supports plugin commands and that its user directory is writable. Raw CLI output is not copied into installation reports.') }
}

function Register-Plugin([string]$Root, $Release) {
    $Version = $Release.Version
    Assert-CodexSuccess (Invoke-Codex @('--version') 30) 'version check'
    $listing = Invoke-Codex @('plugin','marketplace','list','--json')
    Assert-CodexSuccess $listing 'marketplace listing'
    $data = Read-JsonText $listing.Stdout 'Codex marketplace listing'
    $markets = Get-Property $data 'marketplaces'
    if ($markets -isnot [Array]) { throw 'Codex did not return a valid marketplace array; safe replacement stopped.' }
    $matching = @($markets | Where-Object { (Get-Property $_ 'name') -ceq $script:MarketName })
    if ($matching.Count -gt 1) { throw 'Multiple invited marketplace entries prevent safe replacement.' }
    $previous = $null; $previousInstalled = $false; $previousVersion = $null
    if ($matching.Count -eq 1) {
        $previous = Get-Property $matching[0] 'root'
        if ($previous -isnot [string] -or -not (Test-Path -LiteralPath $previous -PathType Container)) { throw 'The previous invited marketplace has no recoverable directory; replacement stopped.' }
        $previous = Get-LocalPath $previous
        Assert-NoReparsePoints $previous
        $previousMarket = Read-JsonFile (Join-Path $previous '.agents\plugins\marketplace.json') 'previous marketplace.json'
        $previousEntries = Get-Property $previousMarket 'plugins'
        if ((Get-Property $previousMarket 'name') -cne $script:MarketName -or $previousEntries -isnot [Array] -or $previousEntries.Count -ne 1 -or (Get-Property $previousEntries[0] 'name') -cne 'nuphus') { throw 'The previous marketplace is not a recoverable single-plugin invitation release; safe replacement stopped.' }
        $previousListing = Invoke-Codex @('plugin','list','--marketplace',$script:MarketName,'--json')
        Assert-CodexSuccess $previousListing 'previous plugin state inspection'
        $previousData = Read-JsonText $previousListing.Stdout 'previous plugin listing'
        $previousItems = Get-Property $previousData 'installed'
        if ($previousItems -isnot [Array]) { throw 'The previous installed-plugin state is not readable; safe replacement stopped.' }
        $oldPlugin = @($previousItems | Where-Object { (Get-Property $_ 'pluginId') -ceq $script:PluginSpec -and (Get-Property $_ 'installed') -eq $true })
        if ($previousItems.Count -gt 1 -or $oldPlugin.Count -gt 1 -or ($previousItems.Count -eq 1 -and $oldPlugin.Count -ne 1)) { throw 'The previous marketplace contains unexpected installed state; safe replacement stopped.' }
        if ($oldPlugin.Count -eq 1) {
            $previousInstalled = $true
            $previousVersion = Get-Property $oldPlugin[0] 'version'
            if ((Get-Property $oldPlugin[0] 'enabled') -ne $true) { throw 'The previous invited plugin is disabled. This CLI cannot preserve disabled state during reinstall. Enable it in Codex first if you want this update; its current installation and settings have not been changed.' }
        }
    }
    $removed = $false; $added = $false
    try {
        if ($previous) {
            Assert-CodexSuccess (Invoke-Codex @('plugin','marketplace','remove',$script:MarketName)) 'previous invited marketplace removal'
            $removed = $true
        }
        Assert-CodexSuccess (Invoke-Codex @('plugin','marketplace','add',$Root)) 'invited marketplace registration'
        $added = $true
        $installResult = Invoke-Codex @('plugin','add',$script:PluginSpec,'--json') 300
        Assert-CodexSuccess $installResult 'plugin installation'
        $installData = Read-JsonText $installResult.Stdout 'Codex plugin installation result'
        $cachePath = Get-Property $installData 'installedPath'
        if ($cachePath -isnot [string] -or [string]::IsNullOrWhiteSpace($cachePath)) { throw 'Codex did not report installedPath in its JSON installation result; final-cache completeness cannot be confirmed.' }
        $script:Receipt['installed_cache_path'] = $cachePath
        $script:CurrentStage = 'codex-cache-verification'
        Show-InstallStage ('Verifying every one of the ' + $Release.PluginFileCount + ' files in the actual Codex plugin cache...')
        $cacheCheck = Test-CodexCacheParity $Release $cachePath
        $script:CurrentStage = 'codex-install-confirmation'
        $confirmation = Invoke-Codex @('plugin','list','--marketplace',$script:MarketName,'--json')
        Assert-CodexSuccess $confirmation 'installed plugin confirmation'
        $confirmed = Read-JsonText $confirmation.Stdout 'Codex installed plugin listing'
        $installedItems = Get-Property $confirmed 'installed'
        $installed = @($installedItems | Where-Object { (Get-Property $_ 'pluginId') -ceq $script:PluginSpec -and (Get-Property $_ 'installed') -eq $true -and (Get-Property $_ 'enabled') -eq $true -and (Get-Property $_ 'version') -ceq $Version })
        if ($installed.Count -ne 1) { throw 'Codex did not confirm the expected installed and enabled plugin version.' }
        return [ordered]@{status='INSTALLED_CACHE_VERIFIED_AND_LISTED'; cache_verification=$cacheCheck; previous_marketplace_root=$previous; previous_marketplace_replaced=$removed; previous_plugin_installed=$previousInstalled; previous_plugin_version=$previousVersion; rollback_attempted=$false}
    } catch {
        $original = $_.Exception.Message
        $restored = $false
        if ($added) { try { [void](Invoke-Codex @('plugin','remove',$script:PluginSpec)); [void](Invoke-Codex @('plugin','marketplace','remove',$script:MarketName)) } catch { } }
        if ($previous -and $removed) {
            try {
                $restoreMarket = Invoke-Codex @('plugin','marketplace','add',$previous)
                if ($restoreMarket.ExitCode -eq 0) {
                    if ($previousInstalled) {
                        $restorePlugin = Invoke-Codex @('plugin','add',$script:PluginSpec,'--json') 300
                        if ($restorePlugin.ExitCode -eq 0) {
                            $restoredInstallData = Read-JsonText $restorePlugin.Stdout 'restored Codex plugin installation result'
                            $restoredCachePath = Get-Property $restoredInstallData 'installedPath'
                            $previousReleaseManifest = Read-JsonFile (Join-Path $previous 'release-manifest.json') 'previous release-manifest.json'
                            if ((Get-Property $previousReleaseManifest 'plugin') -cne 'nuphus' -or (Get-Property $previousReleaseManifest 'marketplace') -cne $script:MarketName) { throw 'The previous release manifest identity cannot be verified during rollback.' }
                            $previousFiles = Get-Property $previousReleaseManifest 'files'
                            $previousPluginFiles = @($previousFiles | Where-Object { $_.path.StartsWith('plugins/nuphus/',[StringComparison]::Ordinal) })
                            if ($previousPluginFiles.Count -lt 1 -or $restoredCachePath -isnot [string]) { throw 'The previous release cannot be checked against the restored Codex cache.' }
                            [void](Test-CodexCacheParity ([pscustomobject]@{Files=$previousFiles; PluginFileCount=$previousPluginFiles.Count}) $restoredCachePath)
                            $restoreCheck = Invoke-Codex @('plugin','list','--marketplace',$script:MarketName,'--json')
                            if ($restoreCheck.ExitCode -eq 0) {
                                $restoreData = Read-JsonText $restoreCheck.Stdout 'restored plugin listing'
                                $restoredItems = Get-Property $restoreData 'installed'
                                $restored = @($restoredItems | Where-Object { (Get-Property $_ 'pluginId') -ceq $script:PluginSpec -and (Get-Property $_ 'installed') -eq $true -and (Get-Property $_ 'enabled') -eq $true -and (Get-Property $_ 'version') -ceq $previousVersion }).Count -eq 1
                            }
                        }
                    } else { $restored = $true }
                }
            } catch { $restored = $false }
        }
        $script:Receipt.registration = [ordered]@{status='FAILED'; previous_marketplace_root=$previous; previous_plugin_installed=$previousInstalled; previous_plugin_version=$previousVersion; rollback_attempted=($removed -or $added); previous_marketplace_restored=$restored}
        throw ($original + ' Previous invited marketplace restored: ' + $restored + '.')
    }
}

function Save-Receipt {
    if ($script:CreatedRoot -and $script:Receipt) {
        $path = Join-Path $script:CreatedRoot 'installation-state.json'
        [IO.File]::WriteAllText($path, (($script:Receipt | ConvertTo-Json -Depth 12) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    }
}

$archive = $null; $memory = $null; $code = $null; $plain = $null
try {
    Assert-WindowsX64
    $script:CurrentStage = 'invitation'
    $code = Resolve-Invitation
    $script:CurrentStage = 'payload-checksums'
    Show-InstallStage 'Checking all encrypted payload parts and the combined checksum...'
    $payload = Read-Payload
    $script:CurrentStage = 'payload-authentication'
    Show-InstallStage 'Authenticating and decrypting the complete plugin package...'
    $plain = Unlock-Payload $payload.Bytes $code
    $code = $null; $InviteCode = $null; $InviteUrl = $null
    $script:CurrentStage = 'archive-validation'
    Show-InstallStage 'Checking archive paths and every authenticated release file...'
    $inventory = @(Get-RawZipInventory $plain)
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $memory = [IO.MemoryStream]::new($plain, $false)
    $archive = [IO.Compression.ZipArchive]::new($memory, [IO.Compression.ZipArchiveMode]::Read, $false)
    $release = Verify-Archive $archive $inventory
    $runtime = Get-RuntimeStatus
    $script:Receipt = [ordered]@{schema_version=1; status='VERIFIED'; plugin='nuphus'; marketplace=$script:MarketName; version=$release.Version; timestamp_utc=[DateTime]::UtcNow.ToString('o'); payload_sha256=$payload.Sha256; payload_bytes=$payload.Size; payload_parts=$payload.PartCount; release_manifest_sha256=$release.ManifestSha256; verified_files=$release.FileCount; plugin_files=$release.PluginFileCount; skill_entries=$release.SkillCount; expanded_bytes=$release.ExpandedBytes; plugin_installed=$false; extraction_verified=$false; cache_verified=$false; runtime=$runtime; registration='NOT_RUN'; installer_source='OFFLINE_LOCAL_DISTRIBUTION'; paid_services='NOT_RUN'; commercial_software='NOT_RUN'}
    if ($VerifyOnly) {
        $script:Receipt | ConvertTo-Json -Depth 12
        exit 0
    }
    $script:CurrentStage = 'codex-discovery'
    if ($CodexHome) {
        $script:ChildCodexHome = Get-LocalPath $CodexHome
        Assert-NoReparsePoints $script:ChildCodexHome
        if ([IO.File]::Exists($script:ChildCodexHome)) { throw 'CodexHome must be a directory or a new directory path.' }
    }
    $script:Cli = Find-Codex
    if ($InstallRoot) { $destination = Get-LocalPath $InstallRoot }
    else { $destination = Join-Path $env:USERPROFILE ('baiyi-invited\' + [guid]::NewGuid().ToString('N').Substring(0,12)) }
    $destination = Get-LocalPath $destination
    Assert-NoReparsePoints $destination
    if (Test-Path -LiteralPath $destination) { throw 'InstallRoot already exists. Choose a new directory; this installer never overwrites it.' }
    if ($script:ChildCodexHome -and ($destination -ieq $script:ChildCodexHome -or $script:ChildCodexHome.StartsWith($destination + '\',[StringComparison]::OrdinalIgnoreCase) -or $destination.StartsWith($script:ChildCodexHome + '\',[StringComparison]::OrdinalIgnoreCase))) { throw 'CodexHome and InstallRoot must be separate, non-nested directories so Codex state cannot alter the verified marketplace tree.' }
    if ($script:ChildCodexHome) {
        $script:CurrentStage = 'codex-home-setup'
        Show-InstallStage 'Preparing the isolated Codex home for child processes...'
        [void][IO.Directory]::CreateDirectory($script:ChildCodexHome)
        Assert-NoReparsePoints $script:ChildCodexHome
    }
    $script:CurrentStage = 'extraction'
    Show-InstallStage ('Extracting ' + $inventory.Count + ' archive entries into the new installation directory...')
    Expand-VerifiedArchive $archive $inventory $destination
    $script:CurrentStage = 'extracted-file-verification'
    Show-InstallStage ('Verifying the size and SHA-256 of all ' + $release.FileCount + ' installed release files...')
    Test-InstalledParity $release $destination
    $script:Receipt.extraction_verified = $true
    $script:Receipt['marketplace_root'] = $destination
    $script:Receipt['codex_home_override'] = $script:ChildCodexHome
    $script:Receipt['codex_executable'] = $script:Cli
    Save-Receipt
    $script:CurrentStage = 'codex-registration'
    Show-InstallStage 'Registering the local invited marketplace and confirming the installed plugin with Codex...'
    $script:Receipt.registration = Register-Plugin $destination $release
    $script:Receipt.cache_verified = $true
    $script:Receipt.plugin_installed = $true
    $script:Receipt.status = if ($runtime.missing_libraries.Count) { 'INSTALLED_RUNTIME_INCOMPLETE' } else { 'INSTALLED' }
    Save-Receipt
    $script:Receipt | ConvertTo-Json -Depth 12
    Write-Host ('Installation receipt: ' + (Join-Path $destination 'installation-state.json'))
    Write-Host 'Restart Codex and create a new ordinary chat to load the plugin. Software licenses and external service accounts remain yours to configure.'
    if ($runtime.missing_libraries.Count) { exit 2 }
    exit 0
} catch {
    $failureMessage = $_.Exception.Message
    if ($script:Receipt) { $script:Receipt.status='FAILED'; $script:Receipt['failed_stage']=$script:CurrentStage }
    try { Save-Receipt } catch { }
    # Never echo input credentials, invitation URLs, environment dumps, or raw CLI logs.
    [Console]::Error.WriteLine('Installation failed at ' + $script:CurrentStage + ': ' + $failureMessage)
    if ($script:CreatedRoot) { [Console]::Error.WriteLine('This attempt owns a new directory with diagnostic state: ' + $script:CreatedRoot + '. It was not automatically deleted.') }
    exit 1
} finally {
    if (-not $VerifyOnly) { Write-Progress -Id 1 -Activity 'Installing the invited plugin' -Completed }
    if ($archive) { $archive.Dispose() }
    if ($memory) { $memory.Dispose() }
    if ($plain) { [Array]::Clear($plain, 0, $plain.Length) }
    $code=$null; $InviteCode=$null; $InviteUrl=$null
}
