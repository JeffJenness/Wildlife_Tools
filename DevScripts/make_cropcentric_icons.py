# -*- coding: utf-8 -*-
"""Writes the SVG artwork for the six CropCentric-port buttons (light and
dark theme each) into DevScripts, then renders them with Inkscape into
WildlifeTools\Images and WildlifeTools\DarkImages at 16 and 32 px (the same
pipeline as make_color_helper_icon.ps1: export at the target height, then
center on a square transparent canvas at 96 DPI).

Icons: PlotDesigner (a block of plots with a direction arrow), FusePlots
(a field with plots cut in), ArrayAlongAxes (a shape copied along two
axes), AddRandomNumbers (a table column of dice-like numbers),
PointCoordinates (a map pin with a callout), PointCoordinatesSettings (the
same callout with a gear).

Run with ArcGIS Pro's Python (it has PIL):
  "C:\\Program Files\\ArcGIS\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe" make_cropcentric_icons.py
Rerun after editing the SVG text below, then rebuild the add-in.
"""
import os
import subprocess
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
LIGHT_DIR = os.path.join(ROOT, "WildlifeTools", "Images")
DARK_DIR = os.path.join(ROOT, "WildlifeTools", "DarkImages")
INKSCAPE = r"C:\Program Files\Inkscape\bin\inkscape.com"

# palette: light theme draws on white, dark theme on charcoal
P = {
    "light": {"ink": "#2b2b2b", "green": "#2e8b57", "green2": "#6fbf8e", "blue": "#1f6fb2",
              "gold": "#c9a227", "paper": "#ffffff", "soft": "#9aa5b1", "red": "#c62828"},
    "dark": {"ink": "#e6e6e6", "green": "#5ccb8c", "green2": "#8fdcb0", "blue": "#6fb3f0",
             "gold": "#e5c35a", "paper": "#3a3a3a", "soft": "#a8b2bc", "red": "#ef6f6f"},
}


def svg(body, c):
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32">'
            + body.format(**c) + "</svg>")


ICONS = {
    # a 2x2 block of plots, replicated once to the right, with a direction arrow
    "PlotDesigner": (
        '<g stroke="{ink}" stroke-width="1.4" fill="{green2}">'
        '<rect x="3" y="12" width="6" height="9"/><rect x="9" y="12" width="6" height="9" fill="{gold}"/>'
        '<rect x="3" y="21" width="6" height="8" fill="{gold}"/><rect x="9" y="21" width="6" height="8"/>'
        '<rect x="18" y="12" width="6" height="9" fill="{gold}"/><rect x="24" y="12" width="6" height="9"/>'
        '<rect x="18" y="21" width="6" height="8"/><rect x="24" y="21" width="6" height="8" fill="{gold}"/>'
        '</g>'
        '<path d="M5 8 L5 2 M2 5 L5 2 L8 5" fill="none" stroke="{red}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    # a field polygon with two plots cut into it
    "FusePlots": (
        '<path d="M3 6 L26 3 L30 24 L8 29 Z" fill="{green2}" stroke="{ink}" stroke-width="1.5" stroke-linejoin="round"/>'
        '<g stroke="{ink}" stroke-width="1.3" fill="{gold}">'
        '<rect x="9" y="9" width="8" height="6"/><rect x="14" y="17" width="9" height="6"/>'
        '</g>'
        '<path d="M9 9 L17 15 M17 9 L9 15" stroke="{paper}" stroke-width="0" />'
    ),
    # one shape copied along two axes
    "ArrayAlongAxes": (
        '<g stroke="{ink}" stroke-width="1.2" fill="{blue}" fill-opacity="0.55">'
        '<rect x="4" y="19" width="7" height="7"/><rect x="13" y="19" width="7" height="7"/><rect x="22" y="19" width="7" height="7"/>'
        '<rect x="4" y="10" width="7" height="7"/><rect x="13" y="10" width="7" height="7"/><rect x="22" y="10" width="7" height="7"/>'
        '</g>'
        '<rect x="4" y="19" width="7" height="7" fill="{green}" stroke="{ink}" stroke-width="1.4"/>'
        '<path d="M4 7 L4 3 M2.5 4.5 L4 3 L5.5 4.5 M27 29 L31 29 M29.5 27.5 L31 29 L29.5 30.5" fill="none" stroke="{red}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>'
    ),
    # a column of a table filled with numbers, a die for randomness
    "AddRandomNumbers": (
        '<rect x="3" y="4" width="16" height="24" rx="1.5" fill="{paper}" stroke="{ink}" stroke-width="1.5"/>'
        '<path d="M3 10 L19 10 M3 16 L19 16 M3 22 L19 22" stroke="{soft}" stroke-width="1.2"/>'
        '<text x="11" y="8.6" font-family="Arial" font-size="5" font-weight="bold" text-anchor="middle" fill="{ink}">7</text>'
        '<text x="11" y="14.6" font-family="Arial" font-size="5" font-weight="bold" text-anchor="middle" fill="{ink}">3</text>'
        '<text x="11" y="20.6" font-family="Arial" font-size="5" font-weight="bold" text-anchor="middle" fill="{ink}">12</text>'
        '<text x="11" y="26.6" font-family="Arial" font-size="5" font-weight="bold" text-anchor="middle" fill="{ink}">5</text>'
        '<rect x="18" y="16" width="12" height="12" rx="2.5" fill="{green2}" stroke="{ink}" stroke-width="1.4"/>'
        '<g fill="{ink}"><circle cx="21" cy="19" r="1.2"/><circle cx="27" cy="19" r="1.2"/><circle cx="24" cy="22" r="1.2"/>'
        '<circle cx="21" cy="25" r="1.2"/><circle cx="27" cy="25" r="1.2"/></g>'
    ),
    # a pin on the map with a callout of coordinates
    "PointCoordinates": (
        '<path d="M12 29 C12 29 5 20 5 14 A7 7 0 0 1 19 14 C19 20 12 29 12 29 Z" fill="{green}" stroke="{ink}" stroke-width="1.4"/>'
        '<circle cx="12" cy="14" r="2.6" fill="{paper}" stroke="{ink}" stroke-width="1.2"/>'
        '<path d="M16 3 L30 3 L30 11 L21 11 L18 14 L18 11 L16 11 Z" fill="{gold}" stroke="{ink}" stroke-width="1.2" stroke-linejoin="round"/>'
        '<path d="M18.5 6 L27.5 6 M18.5 8.5 L25.5 8.5" stroke="{ink}" stroke-width="1.1" stroke-linecap="round"/>'
    ),
    # the same callout with a gear
    "PointCoordinatesSettings": (
        '<path d="M3 4 L19 4 L19 13 L9 13 L6 16 L6 13 L3 13 Z" fill="{gold}" stroke="{ink}" stroke-width="1.2" stroke-linejoin="round"/>'
        '<path d="M5.5 7 L16.5 7 M5.5 10 L13.5 10" stroke="{ink}" stroke-width="1.1" stroke-linecap="round"/>'
        '<g transform="translate(22 22)" fill="{soft}" stroke="{ink}" stroke-width="1.2">'
        '<path d="M-2 -8 L2 -8 L2.6 -5.6 L4.9 -4.9 L7 -6.2 L8.5 -3.5 L6.4 -2 L6.4 2 L8.5 3.5 L7 6.2 L4.9 4.9 L2.6 5.6 L2 8 L-2 8 L-2.6 5.6 L-4.9 4.9 L-7 6.2 L-8.5 3.5 L-6.4 2 L-6.4 -2 L-8.5 -3.5 L-7 -6.2 L-4.9 -4.9 L-2.6 -5.6 Z"/>'
        '<circle cx="0" cy="0" r="2.6" fill="{paper}"/>'
        '</g>'
    ),
}


def export(svg_path, out_path, size):
    tmp = os.path.join(HERE, "_raw_icon.png")
    if os.path.exists(tmp):
        os.remove(tmp)
    subprocess.run([INKSCAPE, svg_path, "--export-type=png", "--export-filename=" + tmp,
                    "--export-height=%d" % size], check=True, capture_output=True)
    src = Image.open(tmp).convert("RGBA")
    dst = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    dst.paste(src, ((size - src.width) // 2, (size - src.height) // 2), src)
    dst.info["dpi"] = (96, 96)
    dst.save(out_path, dpi=(96, 96))
    src.close()
    os.remove(tmp)


def main():
    only = sys.argv[1:] or list(ICONS)
    for name in only:
        body = ICONS[name]
        for theme, out_dir in (("light", LIGHT_DIR), ("dark", DARK_DIR)):
            svg_path = os.path.join(HERE, "%s_icon%s.svg" % (name.lower(), "" if theme == "light" else "_dark"))
            with open(svg_path, "w", encoding="utf-8") as f:
                f.write(svg(body, P[theme]))
            for size in (16, 32):
                export(svg_path, os.path.join(out_dir, "%s_%d.png" % (name, size)), size)
        print("  %s_16/32.png (light + dark)" % name)
    print("Done.")


if __name__ == "__main__":
    main()
