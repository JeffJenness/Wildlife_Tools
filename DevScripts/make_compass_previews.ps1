# Renders the four built-in compass artworks (WildlifeTools\Cartography\*.svg)
# to PNG previews, WildlifeTools\Cartography\Previews\<name>.png, 256 pixels
# tall. The add-in embeds them as WPF resources and uses them for the
# thumbnails in the Add Graphics and North Arrows galleries and for the
# preview in the Manage Custom Graphics and North Arrows window.
#
# Each PNG is the WHOLE SVG canvas (the page), so a focal point given as a
# fraction of the canvas lands on the same spot of the preview.
#
# Rerun after regenerating any of the SVGs (fc_to_svg.py,
# compass2d_to_svg.py), then rebuild the add-in.

$ErrorActionPreference = "Stop"
$inkscape = "C:\Program Files\Inkscape\bin\inkscape.com"
$root = Split-Path -Parent $PSScriptRoot
$artDir = Join-Path $root "WildlifeTools\Cartography"
$outDir = Join-Path $artDir "Previews"
New-Item -ItemType Directory -Force $outDir | Out-Null

foreach ($name in @("CompassRose", "CompassRoseDot", "Compass2", "Compass2Dot")) {
    $svg = Join-Path $artDir "$name.svg"
    $png = Join-Path $outDir "$name.png"
    & $inkscape $svg --export-type=png --export-filename="$png" --export-area-page --export-height=256 | Out-Null
    if (-not (Test-Path $png)) { throw "Inkscape export failed for $svg" }
    Write-Host "wrote $png"
}
