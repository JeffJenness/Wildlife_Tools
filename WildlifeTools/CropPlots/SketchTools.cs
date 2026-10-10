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

using System;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Input;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// The map tool the Plot Designer and Create Array along Axes windows
    /// activate to DRAW AN AXIS: the user drags or clicks a line on the map
    /// (to match a fenceline, say) and the window receives its two end
    /// points. Holding Ctrl when the sketch is finished asks the window to
    /// snap the bearing to the nearest 45 degrees (relative to the other
    /// axis for a second axis), as the CropCentric tools did. The tool
    /// hands the result to whichever window asked (<see cref="Completed"/>)
    /// and then returns the map to the Explore tool.
    /// </summary>
    internal class AxisSketchTool : MapTool
    {
        public const string Id = "WildlifeTools_AxisSketchTool";

        /// <summary>(start, end, ctrl held) of the sketched line, map CS.</summary>
        public static Action<MapPoint, MapPoint, bool> Completed { get; set; }

        public AxisSketchTool()
        {
            IsSketchTool = true;
            SketchType = SketchGeometryType.Line;
            SketchOutputMode = SketchOutputMode.Map;
        }

        protected override Task<bool> OnSketchCompleteAsync(Geometry geometry)
        {
            try
            {
                bool ctrl = (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control;
                if (geometry is Polyline line && !line.IsEmpty && line.PointCount >= 2)
                {
                    MapPoint a = line.Points.First();
                    MapPoint b = line.Points.Last();
                    Completed?.Invoke(a, b, ctrl);
                }
            }
            catch
            {
            }
            FrameworkApplication.SetCurrentToolAsync("esri_mapping_exploreTool");
            return Task.FromResult(true);
        }

        /// <summary>Activate the tool for one sketch, delivering it to
        /// <paramref name="onDone"/>.</summary>
        public static void Begin(Action<MapPoint, MapPoint, bool> onDone)
        {
            Completed = onDone;
            FrameworkApplication.SetCurrentToolAsync(Id);
        }
    }

    /// <summary>
    /// The map tool the Plot Designer window activates to CLICK THE ANCHOR
    /// POINT (the back-left corner of the first plot). One click, delivered
    /// to the window in the map's coordinate system, then back to Explore.
    /// </summary>
    internal class AnchorSketchTool : MapTool
    {
        public const string Id = "WildlifeTools_AnchorSketchTool";

        public static Action<MapPoint> Completed { get; set; }

        public AnchorSketchTool()
        {
            IsSketchTool = true;
            SketchType = SketchGeometryType.Point;
            SketchOutputMode = SketchOutputMode.Map;
        }

        protected override Task<bool> OnSketchCompleteAsync(Geometry geometry)
        {
            try
            {
                if (geometry is MapPoint p && !p.IsEmpty)
                {
                    Completed?.Invoke(p);
                }
            }
            catch
            {
            }
            FrameworkApplication.SetCurrentToolAsync("esri_mapping_exploreTool");
            return Task.FromResult(true);
        }

        public static void Begin(Action<MapPoint> onDone)
        {
            Completed = onDone;
            FrameworkApplication.SetCurrentToolAsync(Id);
        }
    }
}
