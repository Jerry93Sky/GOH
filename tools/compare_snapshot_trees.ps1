[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Before,
    [Parameter(Mandatory = $true)][string]$After,
    [Parameter(Mandatory = $true)][string]$OutFile
)

$ErrorActionPreference = 'Stop'

function Get-TreeManifest([string]$Root) {
    $resolved = (Resolve-Path -LiteralPath $Root).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "Not a directory: $Root"
    }

    $manifest = @{}
    Get-ChildItem -LiteralPath $resolved -File -Recurse -Force | ForEach-Object {
        $relative = $_.FullName.Substring($resolved.Length).TrimStart([char[]]@('\', '/')).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        $manifest[$relative] = [pscustomobject]@{
            Length = [long]$_.Length
            SHA256 = $hash
        }
    }
    return ,$manifest
}

$beforeMap = Get-TreeManifest $Before
$afterMap = Get-TreeManifest $After
$paths = @($beforeMap.Keys) + @($afterMap.Keys) | Sort-Object -Unique
$rows = foreach ($path in $paths) {
    $hasBefore = $beforeMap.ContainsKey($path)
    $hasAfter = $afterMap.ContainsKey($path)
    if (-not $hasBefore) {
        $status = 'Added'
    } elseif (-not $hasAfter) {
        $status = 'Removed'
    } elseif ($beforeMap[$path].SHA256 -ne $afterMap[$path].SHA256) {
        $status = 'Changed'
    } else {
        $status = 'Identical'
    }
    [pscustomobject]@{
        Path = $path
        Status = $status
        BeforeLength = if ($hasBefore) { $beforeMap[$path].Length } else { $null }
        AfterLength = if ($hasAfter) { $afterMap[$path].Length } else { $null }
        BeforeSHA256 = if ($hasBefore) { $beforeMap[$path].SHA256 } else { $null }
        AfterSHA256 = if ($hasAfter) { $afterMap[$path].SHA256 } else { $null }
    }
}

$outParent = Split-Path -Parent $OutFile
if ($outParent -and -not (Test-Path -LiteralPath $outParent -PathType Container)) {
    throw "Output directory does not exist: $outParent"
}
$rows | Export-Csv -LiteralPath $OutFile -NoTypeInformation -Encoding UTF8
$rows | Group-Object Status | Sort-Object Name | ForEach-Object {
    '{0}: {1}' -f $_.Name, $_.Count
}
