# Signs the current Release build of the Wildlife and Forestry Tools add-in
# with the Jenness Enterprises code-signing certificate on the YubiKey.
#
#   Input : WildlifeTools\bin\Release\net10.0-windows\WildlifeTools.esriAddinX
#   Output: D:\ArcGIS_stuff\general_tools\Pro_Wildlife\Signed_Releases\<version>\WildlifeTools.esriAddinX
#
# The build itself is never modified. Run it from the desktop shortcut
# "Sign Wildlife and Forestry Tools"; plug in the YubiKey first and enter
# the PIV PIN (the smart-card PIN, at most 8 characters) when Windows asks.
#
# The certificate is found automatically: the newest valid code-signing
# certificate for "CN=Jenness Enterprises" in the personal store that has
# its private key (on the YubiKey). That keeps working after the 2027
# reissue without editing this script.
#
#   -CheckOnly   do every check but do not sign (no PIN prompt)

param([switch]$CheckOnly)

$ErrorActionPreference = "Stop"
$here    = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo    = Split-Path -Parent $here
$src     = Join-Path $repo "WildlifeTools\bin\Release\net10.0-windows\WildlifeTools.esriAddinX"
$daml    = Join-Path $repo "WildlifeTools\Config.daml"
$outRoot = Join-Path (Split-Path -Parent $repo) "Signed_Releases"
$signExe = "C:\Program Files\ArcGIS\Pro\bin\ArcGISSignAddIn.exe"

function Finish([string]$msg, [string]$color = "Gray") {
    Write-Host ""
    if ($msg) { Write-Host $msg -ForegroundColor $color }
    Write-Host ""
    if (-not $CheckOnly) { Read-Host "Press Enter to close" | Out-Null }
    exit
}

Write-Host "Sign Wildlife and Forestry Tools" -ForegroundColor Cyan
Write-Host "================================"

# --- the build to sign
if (-not (Test-Path $src)) { Finish "No Release build found at:`n  $src`nBuild the add-in in Release first." Red }
$build = Get-Item $src
# The version comes from the Config.daml INSIDE the built add-in, not the
# project's Config.daml, which is bumped at each commit and can be ahead of
# the last build.
$version = "unknown"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($src)
try {
    $entry = $zip.Entries | Where-Object { $_.FullName -ieq "Config.daml" } | Select-Object -First 1
    if ($entry) {
        $reader = New-Object System.IO.StreamReader($entry.Open())
        try { $builtDaml = $reader.ReadToEnd() } finally { $reader.Dispose() }
        $m = [regex]::Match($builtDaml, 'AddInInfo[^>]*\sversion="([^"]+)"')
        if ($m.Success) { $version = $m.Groups[1].Value }
    }
} finally { $zip.Dispose() }
$m = Select-String -Path $daml -Pattern 'AddInInfo[^>]*\sversion="([^"]+)"' | Select-Object -First 1
$projVersion = if ($m) { $m.Matches[0].Groups[1].Value } else { "unknown" }
Write-Host ("Add-in version : {0}" -f $version)
if ($projVersion -ne $version) {
    Write-Host ("NOTE: the project is now at version {0}, newer than this build. Rebuild in Release first if you meant to sign {0}." -f $projVersion) -ForegroundColor Yellow
}
Write-Host ("Release build  : {0}  ({1:N0} bytes)" -f $build.LastWriteTime, $build.Length)

# --- the certificate
$now = Get-Date
$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object {
        $_.Subject -like "CN=Jenness Enterprises*" -and $_.HasPrivateKey -and
        $_.NotBefore -le $now -and $_.NotAfter -gt $now -and
        ($_.EnhancedKeyUsageList | Where-Object { $_.ObjectId -eq "1.3.6.1.5.5.7.3.3" })
    } |
    Sort-Object NotAfter -Descending | Select-Object -First 1
if (-not $cert) {
    Finish ("No valid Jenness Enterprises code-signing certificate was found.`n" +
            "Plug in the YubiKey (unplug and replug it if it is already in), then try again.") Red
}
$daysLeft = [int]($cert.NotAfter - $now).TotalDays
Write-Host ("Certificate    : {0}" -f $cert.Subject)
Write-Host ("Issued by      : {0}" -f $cert.Issuer)
Write-Host ("Valid until    : {0:yyyy-MM-dd}  ({1} days left)" -f $cert.NotAfter, $daysLeft)
Write-Host ("Thumbprint     : {0}" -f $cert.Thumbprint)
if ($daysLeft -lt 90) {
    Write-Host ("WARNING: the certificate expires in {0} days. Arrange the reissue with SSL.com." -f $daysLeft) -ForegroundColor Yellow
}
if (-not (Test-Path $signExe)) { Finish "Esri's signing tool was not found at:`n  $signExe" Red }

$outDir = Join-Path $outRoot $version
$out = Join-Path $outDir "WildlifeTools.esriAddinX"
Write-Host ("Signed copy    : {0}" -f $out)

if ($CheckOnly) { Finish "Check only: everything is in place. Nothing was signed." Green }

# --- sign into a temporary file first, so a failure never leaves a bad file behind
New-Item -ItemType Directory -Force $outDir | Out-Null
$tmp = Join-Path $env:TEMP ("WildlifeTools_signing_{0}.esriAddinX" -f $PID)
if (Test-Path $tmp) { Remove-Item $tmp -Force }
Write-Host ""
Write-Host "Signing... Windows will ask for the YubiKey PIV PIN." -ForegroundColor Cyan
& $signExe $src "/r:$tmp" "/cert-thumbprint:$($cert.Thumbprint)" | Out-Null
if (-not (Test-Path $tmp)) {
    Finish ("Signing FAILED: Esri's tool did not write a signed file.`n" +
            "Its own message box (if any) gives the reason. Check the PIN and that the YubiKey is plugged in.") Red
}

# --- verify the signature before keeping the file
Add-Type -AssemblyName WindowsBase
$ok = $false; $detail = ""
$pkg = [System.IO.Packaging.Package]::Open($tmp, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read)
try {
    $mgr = New-Object System.IO.Packaging.PackageDigitalSignatureManager($pkg)
    $result = $mgr.VerifySignatures($false)
    $content = @($pkg.GetParts() | Where-Object {
        -not [System.IO.Packaging.PackUriHelper]::IsRelationshipPartUri($_.Uri) -and
        $_.Uri.OriginalString -notmatch "digital-signature" })
    if ($mgr.Signatures.Count -eq 1) {
        $sig = $mgr.Signatures[0]
        $signer = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($sig.Signer)
        $detail = "{0} of {1} files signed; signer {2}" -f $sig.SignedParts.Count, $content.Count, $signer.Subject
        $ok = ($result -eq "Success") -and ($signer.Thumbprint -eq $cert.Thumbprint) -and ($sig.SignedParts.Count -eq $content.Count)
    } else {
        $detail = "{0} signatures found (expected 1)" -f $mgr.Signatures.Count
    }
} finally { $pkg.Close() }

if (-not $ok) {
    Remove-Item $tmp -Force
    Finish ("Signing produced a file, but it did NOT verify, so it was discarded.`n  " + $detail) Red
}

Move-Item $tmp $out -Force
Write-Host ""
Write-Host "Signature verified: $detail" -ForegroundColor Green
Write-Host ""
Write-Host "Signed add-in ready to distribute:" -ForegroundColor Green
Write-Host "  $out"
Start-Process explorer.exe "/select,`"$out`""
Finish
