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
    /// "Compass graphic 1 - dot" (Graphics group): identical behavior to
    /// <see cref="CompassRoseGraphicTool"/> but places the CompassRoseDot.svg artwork —
    /// the compass rose with the black "north dot" above the north arrowhead. The stamped
    /// focal point is the compass center (feature-class origin 0,0), NOT the box center.
    /// </summary>
    internal class CompassRoseDotGraphicTool : CompassRoseGraphicTool
    {
        protected override CompassArt Art => CompassArt.Dot;
    }

    /// <summary>
    /// "North arrow 1 - dot" (Cartography group, layout-only): identical behavior to
    /// <see cref="CompassRoseNorthArrowTool"/> but places the CompassRoseDot.svg artwork,
    /// with the focal point on the compass center.
    /// </summary>
    internal class CompassRoseDotNorthArrowTool : CompassRoseNorthArrowTool
    {
        protected override CompassArt Art => CompassArt.Dot;
    }
}
