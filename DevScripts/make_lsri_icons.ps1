# Generates the LSRI ribbon icons from the lsri_*.svg sources:
#   Images\LSRI_DEM_16.png / _32.png, LSRI_Contours_16.png / _32.png     light
#   DarkImages\... (same names -- ArcGIS Pro picks DarkImages automatically)
# Rerun after editing any of the 4 SVGs, then rebuild the add-in.

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$inkscape = "C:\Program Files\Inkscape\bin\inkscape.com"
$root = Split-Path $PSScriptRoot -Parent
$lightDir = Join-Path $root "WildlifeTools\Images"
$darkDir = Join-Path $root "WildlifeTools\DarkImages"
$tmpDir = Join-Path $env:TEMP "lsri_icon"
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

$icons = @(
    @{ Name = "LSRI_DEM"; Light = "lsri_on_dem_icon.svg"; Dark = "lsri_on_dem_icon_dark.svg" },
    @{ Name = "LSRI_Contours"; Light = "lsri_on_contours_icon.svg"; Dark = "lsri_on_contours_icon_dark.svg" }
)

foreach ($icon in $icons) {
    $lightSvg = Join-Path $PSScriptRoot $icon.Light
    $darkSvg = Join-Path $PSScriptRoot $icon.Dark
    foreach ($size in 16, 32) {
        Export-Icon $lightSvg (Join-Path $lightDir "$($icon.Name)_$size.png") $size
        Export-Icon $darkSvg (Join-Path $darkDir "$($icon.Name)_$size.png") $size
        Write-Host "  $($icon.Name)_$size.png (light + dark)"
    }
}
Write-Host "Done."
