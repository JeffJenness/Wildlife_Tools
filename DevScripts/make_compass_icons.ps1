# Generates the ribbon icons for the eight compass tools from the four compass
# SVGs, using Inkscape (light theme) plus a color remap for the dark theme.
#
#   Images\<Art>_16.png / _32.png       light-theme icons
#   DarkImages\<Art>_16.png / _32.png   dark-theme icons (same file names --
#                                       ArcGIS Pro picks DarkImages automatically)
#
# Each graphic/north-arrow tool pair shares its artwork's icon set. Non-square
# artwork is exported at full icon height and padded to a square transparent
# canvas. Dark variants remap the darkest tones to light ones in the SVG text
# before export (black <-> near-white and dark gray -> silver in the classic
# rose, so the hatch and outline contrast relationships survive).
#
# Rerun after regenerating any compass SVG (fc_to_svg.py / compass2d_to_svg.py),
# then rebuild the add-in.

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$inkscape = "C:\Program Files\Inkscape\bin\inkscape.com"
$root = Split-Path $PSScriptRoot -Parent
$cartDir = Join-Path $root "WildlifeTools\Cartography"
$lightDir = Join-Path $root "WildlifeTools\Images"
$darkDir = Join-Path $root "WildlifeTools\DarkImages"
$tmpDir = Join-Path $env:TEMP "compass_icons"
New-Item -ItemType Directory -Force $tmpDir | Out-Null

# Art name -> dark-theme color remap (applied as literal text replacement,
# in order, on the SVG before export). An empty list means the dark icon is
# identical to the light one (per user preference, the Compass 2 icons read
# fine on the dark ribbon as-is; a light remap made them too dark/washed out).
$arts = @{
    "CompassRose"    = @( @("#000000", "#E6E6E6"), @("#FFFFFF", "#3A3A3A"), @("#414141", "#C0C0C0") )
    "CompassRoseDot" = @( @("#000000", "#E6E6E6"), @("#FFFFFF", "#3A3A3A"), @("#414141", "#C0C0C0") )
    "Compass2"       = @()
    "Compass2Dot"    = @()
}

function Export-Icon($svgPath, $outPath, [int]$size) {
    # Export at full icon height (width follows the aspect ratio), then center
    # on a square transparent canvas.
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

foreach ($art in $arts.Keys | Sort-Object) {
    $svg = Join-Path $cartDir "$art.svg"
    if (-not (Test-Path $svg)) { throw "Missing $svg" }

    # Dark variant of the SVG, with the color remap applied.
    $darkSvg = Join-Path $tmpDir "$art`_dark.svg"
    $text = Get-Content $svg -Raw
    foreach ($pair in $arts[$art]) { $text = $text.Replace($pair[0], $pair[1]) }
    Set-Content -Path $darkSvg -Value $text -Encoding utf8 -NoNewline

    foreach ($size in 16, 32) {
        Export-Icon $svg (Join-Path $lightDir "$art`_$size.png") $size
        Export-Icon $darkSvg (Join-Path $darkDir "$art`_$size.png") $size
        Write-Host "  $art`_$size.png (light + dark)"
    }
}
Write-Host "Done."
