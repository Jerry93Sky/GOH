[CmdletBinding()]
param(
    [string]$GamePath,
    [string]$SteamRoot
)

$ErrorActionPreference = 'Stop'
$AppNamePattern = 'Call to Arms.*Gates of Hell|Gates of Hell.*Ostfront'
$GameExe = 'call_to_arms.exe'

function Add-UniquePath([System.Collections.Generic.List[string]]$List, [string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return }
    $candidate = [Environment]::ExpandEnvironmentVariables($Path.Trim().Trim('"')).Replace('/', '\')
    try { $candidate = [IO.Path]::GetFullPath($candidate) } catch { return }
    foreach ($existing in $List) {
        if ([string]::Equals($existing, $candidate, [StringComparison]::OrdinalIgnoreCase)) { return }
    }
    $List.Add($candidate)
}

function Read-VdfLibraryPaths([string]$VdfPath) {
    $result = [System.Collections.Generic.List[string]]::new()
    if (-not (Test-Path -LiteralPath $VdfPath -PathType Leaf)) { return $result }
    $content = Get-Content -LiteralPath $VdfPath -Raw
    # Steam libraryfolders.vdf uses quoted key/value pairs. This extracts both modern
    # { "path" "..." } entries and legacy numeric entries without modifying the file.
    foreach ($match in [regex]::Matches($content, '"(?:path|\d+)"\s*"((?:\\.|[^"])*)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
        $raw = $match.Groups[1].Value -replace '\\\\', '\'
        if ($raw -match '^[A-Za-z]:\\|^\\\\') { Add-UniquePath $result $raw }
    }
    return $result
}

$roots = [System.Collections.Generic.List[string]]::new()
if ($GamePath) { Add-UniquePath $roots $GamePath }

$steamRoots = [System.Collections.Generic.List[string]]::new()
if ($SteamRoot) { Add-UniquePath $steamRoots $SteamRoot }
$registryKeys = @(
    'HKCU:\Software\Valve\Steam',
    'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam',
    'HKLM:\SOFTWARE\Valve\Steam'
)
foreach ($key in $registryKeys) {
    try {
        $item = Get-ItemProperty -LiteralPath $key -ErrorAction Stop
        Add-UniquePath $steamRoots ([string]$item.SteamPath)
        Add-UniquePath $steamRoots ([string]$item.InstallPath)
    } catch { }
}
foreach ($envName in @('ProgramFiles(x86)', 'ProgramFiles', 'LOCALAPPDATA')) {
    $value = [Environment]::GetEnvironmentVariable($envName)
    if ($value) {
        if ($envName -eq 'LOCALAPPDATA') { Add-UniquePath $steamRoots (Join-Path $value 'Programs\Steam') }
        else { Add-UniquePath $steamRoots (Join-Path $value 'Steam') }
    }
}

$libraries = [System.Collections.Generic.List[string]]::new()
foreach ($steam in $steamRoots) {
    Add-UniquePath $libraries $steam
    foreach ($library in (Read-VdfLibraryPaths (Join-Path $steam 'steamapps\libraryfolders.vdf'))) {
        Add-UniquePath $libraries $library
    }
}

$manifestCandidates = [System.Collections.Generic.List[string]]::new()
foreach ($library in $libraries) {
    $common = Join-Path $library 'steamapps\common'
    if (Test-Path -LiteralPath $common -PathType Container) {
        $direct = Join-Path $common 'Call to Arms - Gates of Hell'
        if (Test-Path -LiteralPath $direct -PathType Container) { Add-UniquePath $roots $direct }
    }
    $steamApps = Join-Path $library 'steamapps'
    if (Test-Path -LiteralPath $steamApps -PathType Container) {
        Get-ChildItem -LiteralPath $steamApps -Filter 'appmanifest_*.acf' -File -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $manifest = Get-Content -LiteralPath $_.FullName -Raw
                $nameMatch = [regex]::Match($manifest, '"name"\s*"([^"]+)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
                if ($nameMatch.Success -and $nameMatch.Groups[1].Value -match $AppNamePattern) {
                    $installdirMatch = [regex]::Match($manifest, '"installdir"\s*"([^"]+)"', [Text.RegularExpressions.RegexOptions]::IgnoreCase)
                    if ($installdirMatch.Success) {
                        Add-UniquePath $manifestCandidates (Join-Path $common $installdirMatch.Groups[1].Value)
                    }
                }
            } catch { }
        }
    }
}
foreach ($candidate in $manifestCandidates) { Add-UniquePath $roots $candidate }

$found = @()
foreach ($root in $roots) {
    $exe = Join-Path $root "binaries\x64\$GameExe"
    if (Test-Path -LiteralPath $exe -PathType Leaf) {
        $server = Join-Path $root 'binaries\x64\call_to_arms_server.exe'
        $found += [pscustomobject]@{
            GamePath = $root
            GameExe = $exe
            GameExeVersion = (Get-Item -LiteralPath $exe).VersionInfo.FileVersion
            GameExeSHA256 = (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash
            LocalServerExe = (Test-Path -LiteralPath $server -PathType Leaf)
            VersionDllPresent = (Test-Path -LiteralPath (Join-Path $root 'binaries\x64\version.dll') -PathType Leaf)
        }
    }
}

$documents = [System.Collections.Generic.List[string]]::new()
$knownDocs = [Environment]::GetFolderPath([Environment+SpecialFolder]::MyDocuments)
if ($knownDocs) { Add-UniquePath $documents (Join-Path $knownDocs 'My Games\gates of hell') }
foreach ($envName in @('USERPROFILE', 'OneDrive', 'OneDriveCommercial', 'OneDriveConsumer')) {
    $base = [Environment]::GetEnvironmentVariable($envName)
    if ($base) {
        Add-UniquePath $documents (Join-Path $base 'Documents\My Games\gates of hell')
        Add-UniquePath $documents (Join-Path $base 'My Games\gates of hell')
    }
}
$saveLocations = @()
foreach ($doc in $documents) {
    if (Test-Path -LiteralPath $doc -PathType Container) {
        $profiles = Join-Path $doc 'profiles'
        $saveLocations += [pscustomobject]@{
            Candidate = $doc
            ProfilesDirectory = $profiles
            ProfilesFound = (Test-Path -LiteralPath $profiles -PathType Container)
            SaveDirectories = @((Get-ChildItem -LiteralPath $profiles -Directory -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'save' } | ForEach-Object FullName))
        }
    }
}
$tempLogs = @()
$tempPath = [IO.Path]::GetTempPath()
if (Test-Path -LiteralPath $tempPath) {
    $tempLogs = @(Get-ChildItem -LiteralPath $tempPath -File -Filter 'goh*' -ErrorAction SilentlyContinue | Where-Object { $_.Extension -eq '.log' } | ForEach-Object FullName)
}

if ($found.Count -eq 0) {
    Write-Output 'Gates of Hell installation was not found in discovered Steam libraries.'
    Write-Output 'Check the Steam library containing the game, then run:'
    Write-Output '  .\tools\find_goh_installation.ps1 -GamePath "D:\SteamLibrary\steamapps\common\Call to Arms - Gates of Hell"'
    if ($GamePath) { Write-Output "The supplied path was checked: $GamePath" }
    exit 1
}

[pscustomobject]@{
    Installations = $found
    SaveLocations = $saveLocations
    ExistingGoHLogs = $tempLogs
    ReadOnly = $true
} | ConvertTo-Json -Depth 6
