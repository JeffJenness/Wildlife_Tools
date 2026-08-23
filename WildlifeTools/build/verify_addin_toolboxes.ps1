# Fails the build if either Toolboxes tree inside the packaged .esriAddinX
# (root Toolboxes/ -> Pro's auto-deploy listing; Install/Toolboxes -> the
# AssemblyCache copy the ribbon buttons and tool help read) differs from the
# source Toolboxes folder. Guards against the 2026-08-23 incident where a
# stale incremental staging folder shipped an old Install/Toolboxes that
# silently reverted the installed help on every reinstall.
param(
    [Parameter(Mandatory = $true)][string]$ArchivePath,
    [Parameter(Mandatory = $true)][string]$SourceToolboxes
)

$ErrorActionPreference = "Stop"
# must match the csproj Toolboxes Content/None globs
$extensions = @(".pyt", ".py", ".lyrx", ".xml", ".html", ".css", ".png", ".json")

if (-not (Test-Path $ArchivePath)) {
    Write-Error "verify_addin_toolboxes: archive not found: $ArchivePath"
    exit 1
}

$sha = [System.Security.Cryptography.SHA256]::Create()

function Get-StreamHash($stream) {
    [BitConverter]::ToString($sha.ComputeHash($stream)) -replace "-", ""
}

# source manifest: relative path (forward slashes) -> hash
$expected = @{}
Get-ChildItem $SourceToolboxes -Recurse -File | ForEach-Object {
    if ($extensions -contains $_.Extension.ToLower()) {
        $rel = $_.FullName.Substring($SourceToolboxes.TrimEnd("\").Length + 1) -replace "\\", "/"
        $fs = $_.OpenRead()
        $expected[$rel] = Get-StreamHash $fs
        $fs.Close()
    }
}
if ($expected.Count -eq 0) {
    Write-Error "verify_addin_toolboxes: no source files matched under $SourceToolboxes"
    exit 1
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($ArchivePath)
$failures = New-Object System.Collections.Generic.List[string]
try {
    foreach ($prefix in @("Toolboxes/", "Install/Toolboxes/")) {
        $actual = @{}
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName.StartsWith($prefix) -and
                ($prefix -ne "Toolboxes/" -or -not $entry.FullName.StartsWith("Install/")) -and
                $entry.Name) {
                $rel = $entry.FullName.Substring($prefix.Length)
                $es = $entry.Open()
                $ms = New-Object System.IO.MemoryStream
                $es.CopyTo($ms); $es.Close(); $ms.Position = 0
                $actual[$rel] = Get-StreamHash $ms
                $ms.Close()
            }
        }
        foreach ($rel in $expected.Keys) {
            if (-not $actual.ContainsKey($rel)) {
                $failures.Add("$prefix$rel : MISSING from archive")
            }
            elseif ($actual[$rel] -ne $expected[$rel]) {
                $failures.Add("$prefix$rel : STALE (content differs from source)")
            }
        }
        foreach ($rel in $actual.Keys) {
            if (-not $expected.ContainsKey($rel)) {
                $failures.Add("$prefix$rel : ORPHAN (not in source; stale staging?)")
            }
        }
    }
}
finally {
    $zip.Dispose()
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Output "TOOLBOX PACKAGING DRIFT: $_" }
    Write-Error ("verify_addin_toolboxes: {0} drift(s). Delete obj\<cfg>\net10.0-windows\temp_archive and the .esriAddinX, then rebuild." -f $failures.Count)
    exit 1
}
Write-Output ("verify_addin_toolboxes: both archive Toolboxes trees match source ({0} files each). OK." -f $expected.Count)
exit 0
