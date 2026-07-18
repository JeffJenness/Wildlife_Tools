# Builds the "Remove SLC" ribbon icons: the user's base image
# (Tool_Images\No_SLC.png, a service-layer-credits pictograph) with the standard
# red "prohibited" ring and diagonal slash composited on top. Outputs 16 and
# 32 px PNGs at 96 DPI. The DarkImages variant first INVERTS the base
# pictograph — gray portions become white, white portions become black
# (alpha preserved) — before the red overlay is drawn. The base image's opaque
# pixels split cleanly at luminance 224 (whites ~248, grays 96-160).

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing

$base = "D:\ArcGIS_stuff\general_tools\Pro_Wildlife\Tool_Images\No_SLC.png"
$root = Split-Path $PSScriptRoot -Parent
$red = [System.Drawing.Color]::FromArgb(255, 204, 0, 0)

function Load-Base([bool]$darkTheme) {
    $srcBytes = [System.IO.File]::ReadAllBytes($base)
    $ms = New-Object System.IO.MemoryStream(,$srcBytes)
    $bmp = New-Object System.Drawing.Bitmap([System.Drawing.Bitmap]::FromStream($ms))
    $ms.Dispose()
    if ($darkTheme) {
        for ($x = 0; $x -lt $bmp.Width; $x++) {
            for ($y = 0; $y -lt $bmp.Height; $y++) {
                $p = $bmp.GetPixel($x, $y)
                if ($p.A -lt 20) { continue }
                $lum = ([int]$p.R + $p.G + $p.B) / 3
                $v = if ($lum -ge 224) { 0 } else { 255 }   # white -> black, gray -> white
                $bmp.SetPixel($x, $y, [System.Drawing.Color]::FromArgb($p.A, $v, $v, $v))
            }
        }
    }
    return $bmp
}

foreach ($dir in "Images", "DarkImages") {
    $src = Load-Base ($dir -eq "DarkImages")
    foreach ($size in 16, 32) {
        $dst = New-Object System.Drawing.Bitmap($size, $size, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
        $dst.SetResolution(96, 96)
        $g = [System.Drawing.Graphics]::FromImage($dst)
        $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic

        # Base pictograph, scaled to fill the icon.
        $g.DrawImage($src, (New-Object System.Drawing.Rectangle(0, 0, $size, $size)),
            0, 0, $src.Width, $src.Height, [System.Drawing.GraphicsUnit]::Pixel)

        # Prohibition sign: red ring + diagonal slash (upper-left to lower-right).
        $strokeW = [Math]::Max(2.0, $size / 10.0)
        $pen = New-Object System.Drawing.Pen($red, $strokeW)
        $inset = $strokeW / 2 + 0.5
        $g.DrawEllipse($pen, $inset, $inset, $size - 2 * $inset, $size - 2 * $inset)
        $c = $size / 2.0
        $r = ($size - 2 * $inset) / 2.0
        $d = $r / [Math]::Sqrt(2)
        $g.DrawLine($pen, [single]($c - $d), [single]($c - $d), [single]($c + $d), [single]($c + $d))
        $pen.Dispose(); $g.Dispose()

        $out = "$root\WildlifeTools\$dir\RemoveSLC_$size.png"
        $dst.Save($out, [System.Drawing.Imaging.ImageFormat]::Png)
        $dst.Dispose()
        Write-Host "  $dir\RemoveSLC_$size.png"
    }
    $src.Dispose()
}
Write-Host "Done."
