// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Scholarship Fund notice, marking of modified versions, and
// use of names).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

namespace WildlifeTools
{
    /// <summary>
    /// "Compass graphic 2" (Graphics group): identical behavior to
    /// <see cref="CompassRoseGraphicTool"/> but places the Compass2.svg artwork —
    /// the Compass_2d design (black cardinal star, gray intercardinal star, twin
    /// rings). Focal point is the compass center.
    /// </summary>
    internal class Compass2GraphicTool : CompassRoseGraphicTool
    {
        protected override CompassArt Art => CompassArt.Compass2;
    }

    /// <summary>
    /// "North arrow 2" (Cartography group, layout-only): identical behavior to
    /// <see cref="CompassRoseNorthArrowTool"/> but places the Compass2.svg artwork.
    /// </summary>
    internal class Compass2NorthArrowTool : CompassRoseNorthArrowTool
    {
        protected override CompassArt Art => CompassArt.Compass2;
    }

    /// <summary>
    /// "Compass graphic 2 - dot" (Graphics group): the Compass_2d design plus the
    /// Sample_North gradient spearhead above the north point (Compass2Dot.svg).
    /// Focal point is the compass center, below the canvas midpoint.
    /// </summary>
    internal class Compass2DotGraphicTool : CompassRoseGraphicTool
    {
        protected override CompassArt Art => CompassArt.Compass2Dot;
    }

    /// <summary>
    /// "North arrow 2 - dot" (Cartography group, layout-only): north arrow version of
    /// <see cref="Compass2DotGraphicTool"/>.
    /// </summary>
    internal class Compass2DotNorthArrowTool : CompassRoseNorthArrowTool
    {
        protected override CompassArt Art => CompassArt.Compass2Dot;
    }
}
