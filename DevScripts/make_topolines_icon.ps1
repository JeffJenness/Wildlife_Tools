# Generates the ACR ribbon icons from topolines_icon.svg and topolines_icon_dark.svg:
#   Images\TopoLines_16.png / _32.png       light theme
#   DarkImages\TopoLines_16.png / _32.png   dark theme (same names -- ArcGIS Pro
#                                     picks DarkImages automatically)
# Rerun after editing either SVG, then rebuild the add-in.

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$inkscape = "C:\Program Files\Inkscape\bin\inkscape.com"
$root = Split-Path $PSScriptRoot -Parent
$lightDir = Join-Path $root "WildlifeTools\Images"
$darkDir = Join-Path $root "WildlifeTools\DarkImages"
$tmpDir = Join-Path $env:TEMP "topolines_icon"
New-Item -ItemType Directory -Force $tmpDir | Out-Null

function Export-Icon($svgPath, $outPath, [int]$size) {
    $tmp = Join-Path $tmpDir "raw.png"
    if (Test-Path $tmp) { Remove-Item $tmp -Force }
    & $inkscape $svgPath --export-type=png --export-filename="$tmp" --export-height=$size | Out-Null
    if (-not (Test-Path $tmp)) { throw "Inkscape export failed for $svgPath" }

    # Inkscape stamps an arbitrary DPI into the PNG, and GDI+ scales draws by
    # the DPI ratio -- so always draw via an explicit pixel-unit destination
    # rectangle and save the icon at a normalized 96 DPI.
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

$lightSvg = Join-Path $PSScriptRoot "topolines_icon.svg"
$darkSvg = Join-Path $PSScriptRoot "topolines_icon_dark.svg"
foreach ($size in 16, 32) {
    Export-Icon $lightSvg (Join-Path $lightDir "TopoLines_$size.png") $size
    Export-Icon $darkSvg (Join-Path $darkDir "TopoLines_$size.png") $size
    Write-Host "  TopoLines_$size.png (light + dark)"
}
Write-Host "Done."
