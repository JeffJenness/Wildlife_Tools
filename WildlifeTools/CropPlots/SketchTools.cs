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
using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// The map tool the Plot Designer and Create Array along Axes windows
    /// activate to DRAW AN AXIS: the user drags or clicks a line on the map
    /// (to match a fenceline, say) and the window receives its two end
    /// points. Holding Ctrl snaps the line to the nearest 45 degrees
    /// (relative to <see cref="SnapReference"/>: north for a first axis,
    /// the first axis for a second), as the CropCentric tools did -- and,
    /// since 2026-10-10, the snap shows WHILE DRAWING: Pro's own rubber
    /// band cannot be constrained through the SDK, so while Ctrl is held
    /// the tool draws its own red line from the first vertex to the
    /// snapped end, and the finished line is delivered snapped. The tool
    /// hands the result to whichever window asked (<see cref="Completed"/>)
    /// and then returns the map to the Explore tool.
    /// </summary>
    internal class AxisSketchTool : MapTool
    {
        public const string Id = "WildlifeTools_AxisSketchTool";

        /// <summary>(start, end, ctrl held) of the sketched line, map CS.</summary>
        public static Action<MapPoint, MapPoint, bool> Completed { get; set; }

        /// <summary>The bearing (degrees clockwise from north) the Ctrl
        /// snap is measured from: 0 for a first axis, the first axis's
        /// bearing for a second axis.</summary>
        public static double SnapReference { get; set; }

        private MapPoint _start;                 // the first vertex, map CS
        private IDisposable _overlay;            // our snapped rubber band
        private CIMSymbolReference _lineSym;
        private bool _busy;

        public AxisSketchTool()
        {
            IsSketchTool = true;
            SketchType = SketchGeometryType.Line;
            SketchOutputMode = SketchOutputMode.Map;
        }

        protected override Task OnToolActivateAsync(bool active)
        {
            _start = null;
            ClearOverlay();
            return QueuedTask.Run(() =>
            {
                _lineSym = SymbolFactory.Instance.ConstructLineSymbol(
                    CIMColor.CreateRGBColor(205, 35, 35), 2.2, SimpleLineStyle.Solid).MakeSymbolReference();
            });
        }

        protected override Task OnToolDeactivateAsync(bool hasMapViewChanged)
        {
            ClearOverlay();
            _start = null;
            return Task.CompletedTask;
        }

        /// <summary>The first vertex was placed (or the sketch changed):
        /// remember where the line starts, for the snapped preview.</summary>
        protected override async Task<bool> OnSketchModifiedAsync()
        {
            try
            {
                Geometry g = await GetCurrentSketchAsync();
                MapPoint first = (g as Polyline)?.Points?.FirstOrDefault() ?? g as MapPoint;
                if (first != null && !first.IsEmpty)
                {
                    _start = first;
                }
            }
            catch
            {
            }
            return true;
        }

        /// <summary>While Ctrl is held and a first vertex exists, draw the
        /// snapped line from that vertex toward the cursor; otherwise show
        /// nothing of our own (Pro's rubber band suffices).</summary>
        protected override void OnToolMouseMove(MapViewMouseEventArgs e)
        {
            base.OnToolMouseMove(e);
            bool ctrl = (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control;
            if (_start == null || !ctrl)
            {
                ClearOverlay();
                return;
            }
            if (_busy)
            {
                return;                              // a previous move is still being drawn
            }
            MapView mv = ActiveMapView;
            if (mv == null)
            {
                return;
            }
            System.Windows.Point client = e.ClientPoint;
            MapPoint start = _start;
            double reference = SnapReference;
            _busy = true;
            QueuedTask.Run(() =>
            {
                try
                {
                    MapPoint cursor = mv.ClientToMap(client);
                    if (cursor == null || cursor.IsEmpty)
                    {
                        return;
                    }
                    MapPoint end = SnapEnd(start, cursor, reference);
                    Polyline line = PolylineBuilderEx.CreatePolyline(new[] { start, end }, start.SpatialReference);
                    if (_overlay == null)
                    {
                        _overlay = mv.AddOverlay(line, _lineSym);
                    }
                    else
                    {
                        mv.UpdateOverlay(_overlay, line, _lineSym);
                    }
                }
                catch
                {
                }
                finally
                {
                    _busy = false;
                }
            });
        }

        protected override Task<bool> OnSketchCompleteAsync(Geometry geometry)
        {
            ClearOverlay();
            try
            {
                bool ctrl = (Keyboard.Modifiers & ModifierKeys.Control) == ModifierKeys.Control;
                if (geometry is Polyline line && !line.IsEmpty && line.PointCount >= 2)
                {
                    MapPoint a = line.Points.First();
                    MapPoint b = line.Points.Last();
                    if (ctrl)
                    {
                        b = SnapEnd(a, b, SnapReference);   // deliver the line as previewed
                    }
                    Completed?.Invoke(a, b, ctrl);
                }
            }
            catch
            {
            }
            _start = null;
            FrameworkApplication.SetCurrentToolAsync("esri_mapping_exploreTool");
            return Task.FromResult(true);
        }

        private void ClearOverlay()
        {
            IDisposable o = _overlay;
            _overlay = null;
            try
            {
                o?.Dispose();
            }
            catch
            {
            }
        }

        /// <summary>The end point moved about <paramref name="a"/> so the
        /// segment runs at the nearest 45-degree step from
        /// <paramref name="reference"/>, keeping its length. Planar in the
        /// map's coordinate system: the preview is visual, and the window
        /// snaps the geodesic bearing it computes to the exact step.</summary>
        internal static MapPoint SnapEnd(MapPoint a, MapPoint b, double reference)
        {
            double dx = b.X - a.X;
            double dy = b.Y - a.Y;
            double len = Math.Sqrt(dx * dx + dy * dy);
            if (!(len > 0))
            {
                return b;
            }
            double bearing = (Math.Atan2(dx, dy) * 180.0 / Math.PI + 360.0) % 360.0;   // clockwise from +Y
            double snapped = CropPlotActions.SnapBearing(bearing, reference) * Math.PI / 180.0;
            return MapPointBuilderEx.CreateMapPoint(a.X + len * Math.Sin(snapped), a.Y + len * Math.Cos(snapped),
                a.SpatialReference);
        }

        /// <summary>Activate the tool for one sketch, delivering it to
        /// <paramref name="onDone"/>; <paramref name="snapReference"/> is
        /// the bearing the Ctrl snap is measured from (0 = north).</summary>
        public static void Begin(Action<MapPoint, MapPoint, bool> onDone, double snapReference = 0.0)
        {
            Completed = onDone;
            SnapReference = snapReference;
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
