# Generates the TPI classification ribbon icons (light + dark) from the SVGs:
#   Images\{SlopePos,Landform,GenClass}_16/_32.png       light theme
#   DarkImages\{...}_16/_32.png                           dark theme
# Rerun after editing any SVG, then rebuild the add-in.

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$inkscape = "C:\Program Files\Inkscape\bin\inkscape.com"
$root = Split-Path $PSScriptRoot -Parent
$lightDir = Join-Path $root "WildlifeTools\Images"
$darkDir = Join-Path $root "WildlifeTools\DarkImages"
$tmpDir = Join-Path $env:TEMP "tpi_icons"
New-Item -ItemType Directory -Force $tmpDir | Out-Null

function Export-Icon($svgPath, $outPath, [int]$size) {
    $tmp = Join-Path $tmpDir "raw.png"
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
    & $inkscape $svgPath --export-type=png --export-filename="$tmp" --export-height=$size | Out-Null
    if (-not (Test-Path $tmp)) { throw "Inkscape export failed for $svgPath" }
    $src = [System.Drawing.Bitmap]::FromFile($tmp)
    try {
        $dst = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $dst.SetResolution(96, 96)
        $g = [System.Drawing.Graphics]::FromImage($dst)
        $destRect = New-Object System.Drawing.Rectangle(
            [int][Math]::Floor(($size - $src.Width) / 2),
            [int][Math]::Floor(($size - $src.Height) / 2),
            $src.Width, $src.Height)
        $g.DrawImage($src, $destRect, 0, 0, $src.Width, $src.Height, [System.Drawing.GraphicsUnit]::Pixel)
        $g.Dispose()
        $dst.Save($outPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $dst.Dispose()
    } finally {
        $src.Dispose()
    }
}

$icons = @(
    @{ svg = "tpi_icon"; png = "TPI" },
    @{ svg = "slopepos_icon"; png = "SlopePos" },
    @{ svg = "landform_icon"; png = "Landform" },
    @{ svg = "genclass_icon"; png = "GenClass" },
    @{ svg = "classbuilder_icon"; png = "ClassBuilder" }
)
foreach ($ic in $icons) {
    $lightSvg = Join-Path $PSScriptRoot "$($ic.svg).svg"
    $darkSvg = Join-Path $PSScriptRoot "$($ic.svg)_dark.svg"
    foreach ($size in 16, 32) {
        Export-Icon $lightSvg (Join-Path $lightDir "$($ic.png)_$size.png") $size
        Export-Icon $darkSvg (Join-Path $darkDir "$($ic.png)_$size.png") $size
    }
    Write-Host "  $($ic.png)_16/_32.png (light + dark)"
}
Write-Host "Done."
