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
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// Everything the crop plot windows need from ArcGIS Pro: the active
    /// map's polygon layers, the selected graphic, the map's coordinate
    /// system, running and opening the Plot Designer and Create Array along
    /// Axes geoprocessing tools, the live overlay preview, and the help
    /// pages. The panels themselves are plain WPF so they can be drawn and
    /// checked outside Pro.
    /// </summary>
    internal static class CropPlotActions
    {
        /// <summary>A polygon layer of the active map.</summary>
        internal sealed class LayerChoice
        {
            public string Name { get; set; }
            public FeatureLayer Layer { get; set; }
            public long SelectedCount { get; set; }
            public bool IsSelectedInContents { get; set; }
            /// <summary>"point", "line" or "polygon" when the list mixes
            /// shapes (shown after the name); null in polygon-only lists.</summary>
            public string Shape { get; set; }
            public override string ToString() => string.IsNullOrEmpty(Shape) ? Name : Name + " (" + Shape + ")";
        }

        /// <summary>The source of an array: one selected graphic (its
        /// geometry as Esri JSON) or a layer's selected features.</summary>
        internal sealed class SourceInfo
        {
            public Geometry Geometry { get; set; }        // the graphic, map CS
            public string Json { get; set; }
            public string Describe { get; set; }
            public bool Ok => Geometry != null;
        }

        public static readonly string[] Units =
        {
            "Meters", "Feet", "US survey feet", "Yards", "Kilometers", "Miles",
        };

        public static double MetersPer(string unit)
        {
            switch ((unit ?? "Meters").Trim().ToLowerInvariant())
            {
                case "feet": return 0.3048;
                case "us survey feet": return 1200.0 / 3937.0;
                case "yards": return 0.9144;
                case "kilometers": return 1000.0;
                case "miles": return 1609.344;
                default: return 1.0;
            }
        }

        // =====================================================================
        // the map
        // =====================================================================
        public static Task<List<LayerChoice>> ListPolygonLayersAsync() => ListLayersAsync(true);

        /// <summary>The active map's feature layers: polygons only, or
        /// (<paramref name="polygonsOnly"/> false) points, lines and
        /// polygons alike, each tagged with its shape kind.</summary>
        public static Task<List<LayerChoice>> ListLayersAsync(bool polygonsOnly)
        {
            return QueuedTask.Run(() =>
            {
                var list = new List<LayerChoice>();
                MapView mv = MapView.Active;
                Map map = mv?.Map;
                if (map == null)
                {
                    return list;
                }
                IReadOnlyList<Layer> selected = null;
                try { selected = mv.GetSelectedLayers(); } catch { }
                foreach (FeatureLayer fl in map.GetLayersAsFlattenedList().OfType<FeatureLayer>())
                {
                    try
                    {
                        string shape;
                        switch (fl.ShapeType)
                        {
                            case esriGeometryType.esriGeometryPolygon: shape = "polygon"; break;
                            case esriGeometryType.esriGeometryPolyline: shape = "line"; break;
                            case esriGeometryType.esriGeometryPoint:
                            case esriGeometryType.esriGeometryMultipoint: shape = "point"; break;
                            default: continue;                       // annotation, dimension, ...
                        }
                        if (polygonsOnly && shape != "polygon")
                        {
                            continue;
                        }
                        long sel = 0;
                        try { sel = fl.GetSelection()?.GetCount() ?? 0; } catch { }
                        list.Add(new LayerChoice
                        {
                            Name = fl.Name,
                            Layer = fl,
                            SelectedCount = sel,
                            IsSelectedInContents = selected != null && selected.Contains(fl),
                            Shape = polygonsOnly ? null : shape,
                        });
                    }
                    catch
                    {
                    }
                }
                return list;
            });
        }

        /// <summary>The active map's spatial reference (null without a map).</summary>
        public static Task<SpatialReference> MapSpatialReferenceAsync()
        {
            return QueuedTask.Run(() => MapView.Active?.Map?.SpatialReference);
        }

        /// <summary>The ONE selected graphic element on the active map (any
        /// graphics layer): its geometry in the map's coordinate system.</summary>
        public static Task<SourceInfo> SelectedGraphicAsync()
        {
            return QueuedTask.Run(() =>
            {
                var info = new SourceInfo();
                Map map = MapView.Active?.Map;
                if (map == null)
                {
                    info.Describe = "No active map.";
                    return info;
                }
                var sel = new List<GraphicElement>();
                foreach (GraphicsLayer gl in map.GetLayersAsFlattenedList().OfType<GraphicsLayer>())
                {
                    foreach (Element el in gl.GetSelectedElements() ?? Enumerable.Empty<Element>())
                    {
                        Flatten(el, sel);
                    }
                }
                sel = sel.Where(g => g != null && !(g.GetGraphic() is CIMTextGraphic)
                                     && !(g.GetGraphic() is CIMPictureGraphic)).ToList();
                if (sel.Count == 0)
                {
                    info.Describe = "No graphic is selected on the map. Select one polygon, line or point graphic.";
                    return info;
                }
                if (sel.Count > 1)
                {
                    info.Describe = sel.Count.ToString("N0", CultureInfo.CurrentCulture)
                                    + " graphics are selected. Select exactly one.";
                    return info;
                }
                Geometry g = sel[0].GetGeometry();
                if (g == null || g.IsEmpty)
                {
                    info.Describe = "The selected graphic has no geometry.";
                    return info;
                }
                if (g.SpatialReference == null && map.SpatialReference != null)
                {
                    g = GeometryEngine.Instance.Project(g, map.SpatialReference) ?? g;
                }
                info.Geometry = g;
                info.Json = GeometryEngine.Instance.ExportToJson(JsonExportFlags.JsonExportDefaults, g);
                string kind = g.GeometryType == GeometryType.Polygon ? "polygon"
                    : g.GeometryType == GeometryType.Polyline ? "line"
                    : g.GeometryType == GeometryType.Point ? "point" : g.GeometryType.ToString().ToLowerInvariant();
                info.Describe = "One " + kind + " graphic is selected.";
                return info;
            });
        }

        private static void Flatten(Element el, List<GraphicElement> into)
        {
            if (el is GroupElement ge)
            {
                foreach (Element child in ge.Elements)
                {
                    Flatten(child, into);
                }
            }
            else if (el is GraphicElement g)
            {
                into.Add(g);
            }
        }

        /// <summary>The selected features of a layer as one geometry list
        /// (map CS), for the preview.</summary>
        public static Task<List<Geometry>> SelectedFeatureGeometriesAsync(FeatureLayer layer, int max = 500)
        {
            return QueuedTask.Run(() =>
            {
                var list = new List<Geometry>();
                if (layer == null)
                {
                    return list;
                }
                SpatialReference mapSr = MapView.Active?.Map?.SpatialReference;
                try
                {
                    Selection sel = layer.GetSelection();
                    if (sel == null || sel.GetCount() == 0)
                    {
                        return list;
                    }
                    using (RowCursor cur = sel.Search(null, false))
                    {
                        while (cur.MoveNext() && list.Count < max)
                        {
                            using (var f = cur.Current as Feature)
                            {
                                Geometry g = f?.GetShape();
                                if (g == null || g.IsEmpty)
                                {
                                    continue;
                                }
                                if (mapSr != null && g.SpatialReference != null
                                    && !g.SpatialReference.IsEqual(mapSr))
                                {
                                    g = GeometryEngine.Instance.Project(g, mapSr);
                                }
                                list.Add(g);
                            }
                        }
                    }
                }
                catch
                {
                }
                return list;
            });
        }

        // =====================================================================
        // geodesic helpers (match crop_plots.py: a local azimuthal
        // equidistant frame on the output's own datum)
        // =====================================================================
        public static SpatialReference Gcs(SpatialReference sr)
        {
            if (sr == null)
            {
                return null;
            }
            return sr.IsGeographic ? sr : sr.Gcs ?? sr;
        }

        /// <summary>(lon, lat) of a point in any coordinate system.</summary>
        public static (double lon, double lat) LonLat(MapPoint p)
        {
            if (p.SpatialReference == null || p.SpatialReference.IsGeographic)
            {
                return (p.X, p.Y);
            }
            MapPoint g = (MapPoint)GeometryEngine.Instance.Project(p, Gcs(p.SpatialReference));
            return (g.X, g.Y);
        }

        /// <summary>An azimuthal equidistant projection centered at lon/lat
        /// on the datum of <paramref name="sr"/>.</summary>
        public static SpatialReference LocalAeqd(SpatialReference sr, double lon, double lat)
        {
            SpatialReference gcs = Gcs(sr);
            string gcsWkt = gcs.Wkt;
            string wkt = "PROJCS[\"CropCentric_AEQD\"," + gcsWkt
                         + ",PROJECTION[\"Azimuthal_Equidistant\"],PARAMETER[\"False_Easting\",0.0],"
                         + "PARAMETER[\"False_Northing\",0.0],PARAMETER[\"Central_Meridian\","
                         + lon.ToString("R", CultureInfo.InvariantCulture) + "],PARAMETER[\"Latitude_Of_Origin\","
                         + lat.ToString("R", CultureInfo.InvariantCulture) + "],UNIT[\"Meter\",1.0]]";
            return SpatialReferenceBuilder.CreateSpatialReference(wkt);
        }

        /// <summary>Geodesic bearing (degrees clockwise from north) from
        /// <paramref name="a"/> to <paramref name="b"/>: the direction of b
        /// seen in an equidistant frame centered on a.</summary>
        public static double Bearing(MapPoint a, MapPoint b)
        {
            var (lon, lat) = LonLat(a);
            SpatialReference local = LocalAeqd(a.SpatialReference, lon, lat);
            MapPoint q = (MapPoint)GeometryEngine.Instance.Project(b, local);
            double deg = Math.Atan2(q.X, q.Y) * 180.0 / Math.PI;
            return (deg % 360.0 + 360.0) % 360.0;
        }

        /// <summary>The nearest multiple of 45 degrees to <paramref name="bearing"/>
        /// measured from <paramref name="reference"/> (0 = the cardinal
        /// directions), as the legacy Ctrl key did.</summary>
        public static double SnapBearing(double bearing, double reference)
        {
            double rel = bearing - reference;
            double snapped = Math.Round(rel / 45.0) * 45.0 + reference;
            return (snapped % 360.0 + 360.0) % 360.0;
        }

        /// <summary>Plot Designer geometry for the PREVIEW, the same
        /// construction as crop_plots.design_plots: polygons in
        /// <paramref name="outSr"/>, one per plot; rates are not needed here.</summary>
        public static List<Polygon> PlotPolygons(MapPoint anchor, SpatialReference outSr, double longM,
            double shortM, double bearing, int nLong, int nShort, int nReps, bool sideBySide, double gapM)
        {
            var (lon, lat) = LonLat(anchor);
            SpatialReference local = LocalAeqd(outSr, lon, lat);
            double b = bearing * Math.PI / 180.0;
            double ux = Math.Sin(b), uy = Math.Cos(b);
            double vx = Math.Cos(b), vy = -Math.Sin(b);
            double repDx, repDy;
            if (sideBySide)
            {
                repDx = (nShort * shortM + gapM) * vx;
                repDy = (nShort * shortM + gapM) * vy;
            }
            else
            {
                repDx = (nLong * longM + gapM) * ux;
                repDy = (nLong * longM + gapM) * uy;
            }
            var list = new List<Polygon>();
            for (int r = 0; r < nReps; r++)
            {
                double ox = r * repDx, oy = r * repDy;
                for (int i = 0; i < nLong; i++)
                {
                    for (int j = 0; j < nShort; j++)
                    {
                        double x0 = ox + i * longM * ux + j * shortM * vx;
                        double y0 = oy + i * longM * uy + j * shortM * vy;
                        var pts = new List<Coordinate2D>
                        {
                            new Coordinate2D(x0, y0),
                            new Coordinate2D(x0 + longM * ux, y0 + longM * uy),
                            new Coordinate2D(x0 + longM * ux + shortM * vx, y0 + longM * uy + shortM * vy),
                            new Coordinate2D(x0 + shortM * vx, y0 + shortM * vy),
                            new Coordinate2D(x0, y0),
                        };
                        Polygon p = PolygonBuilderEx.CreatePolygon(pts, local);
                        list.Add((Polygon)GeometryEngine.Instance.Project(p, outSr));
                    }
                }
            }
            return list;
        }

        /// <summary>Array geometry for the PREVIEW, the same construction as
        /// crop_plots.array_geometries: every copy of every source in
        /// <paramref name="outSr"/> (the originals included).</summary>
        public static List<Geometry> ArrayCopies(IList<Geometry> sources, SpatialReference outSr, double bearing1,
            double gap1M, int nForward, int nBack, double? bearing2, double gap2M, int nRight, int nLeft)
        {
            var result = new List<Geometry>();
            if (sources == null || sources.Count == 0)
            {
                return result;
            }
            Envelope ext = null;
            foreach (Geometry g in sources)
            {
                ext = ext == null ? g.Extent : ext.Union(g.Extent);
            }
            MapPoint c = MapPointBuilderEx.CreateMapPoint(ext.Center.X, ext.Center.Y, ext.SpatialReference);
            var (lon, lat) = LonLat(c);
            SpatialReference local = LocalAeqd(outSr, lon, lat);
            var locals = new List<Geometry>();
            var pts = new List<Coordinate2D>();
            foreach (Geometry g in sources)
            {
                Geometry gl = g;
                if (g is Multipart mp && mp.HasCurves)
                {
                    try { gl = GeometryEngine.Instance.DensifyByLength(g, Math.Max(ext.Width, ext.Height) / 400.0); }
                    catch { }
                }
                gl = GeometryEngine.Instance.Project(gl, local);
                locals.Add(gl);
                switch (gl)
                {
                    case Multipart m:
                        pts.AddRange(m.Points.Select(p => new Coordinate2D(p.X, p.Y)));
                        break;
                    case Multipoint mpt:
                        pts.AddRange(mpt.Points.Select(p => new Coordinate2D(p.X, p.Y)));
                        break;
                    case MapPoint p:
                        pts.Add(new Coordinate2D(p.X, p.Y));
                        break;
                }
            }
            if (pts.Count == 0)
            {
                return result;
            }
            double b1 = bearing1 * Math.PI / 180.0;
            double u1x = Math.Sin(b1), u1y = Math.Cos(b1);
            double b2 = (bearing2 ?? bearing1 + 90.0) * Math.PI / 180.0;
            double u2x = Math.Sin(b2), u2y = Math.Cos(b2);
            double min1 = pts.Min(p => p.X * u1x + p.Y * u1y), max1 = pts.Max(p => p.X * u1x + p.Y * u1y);
            double min2 = pts.Min(p => p.X * u2x + p.Y * u2y), max2 = pts.Max(p => p.X * u2x + p.Y * u2y);
            double step1 = (max1 - min1) + gap1M, step2 = (max2 - min2) + gap2M;
            for (int k1 = -nBack; k1 <= nForward; k1++)
            {
                for (int k2 = -nLeft; k2 <= nRight; k2++)
                {
                    double dx = k1 * step1 * u1x + k2 * step2 * u2x;
                    double dy = k1 * step1 * u1y + k2 * step2 * u2y;
                    foreach (Geometry gl in locals)
                    {
                        Geometry moved = (k1 == 0 && k2 == 0) ? gl : GeometryEngine.Instance.Move(gl, dx, dy);
                        result.Add(GeometryEngine.Instance.Project(moved, outSr));
                    }
                }
            }
            return result;
        }

        /// <summary>Step sizes (meters) along the two axes for the window's
        /// status line, same rule as the engine.</summary>
        public static (double step1, double step2) ArraySteps(IList<Geometry> sources, SpatialReference outSr,
            double bearing1, double gap1M, double? bearing2, double gap2M)
        {
            if (sources == null || sources.Count == 0)
            {
                return (0, 0);
            }
            Envelope ext = null;
            foreach (Geometry g in sources)
            {
                ext = ext == null ? g.Extent : ext.Union(g.Extent);
            }
            MapPoint c = MapPointBuilderEx.CreateMapPoint(ext.Center.X, ext.Center.Y, ext.SpatialReference);
            var (lon, lat) = LonLat(c);
            SpatialReference local = LocalAeqd(outSr, lon, lat);
            var pts = new List<Coordinate2D>();
            foreach (Geometry g in sources)
            {
                Geometry gl = GeometryEngine.Instance.Project(g, local);
                switch (gl)
                {
                    case Multipart m: pts.AddRange(m.Points.Select(p => new Coordinate2D(p.X, p.Y))); break;
                    case Multipoint mpt: pts.AddRange(mpt.Points.Select(p => new Coordinate2D(p.X, p.Y))); break;
                    case MapPoint p: pts.Add(new Coordinate2D(p.X, p.Y)); break;
                }
            }
            if (pts.Count == 0)
            {
                return (0, 0);
            }
            double b1 = bearing1 * Math.PI / 180.0, b2 = (bearing2 ?? bearing1 + 90.0) * Math.PI / 180.0;
            double u1x = Math.Sin(b1), u1y = Math.Cos(b1), u2x = Math.Sin(b2), u2y = Math.Cos(b2);
            double e1 = pts.Max(p => p.X * u1x + p.Y * u1y) - pts.Min(p => p.X * u1x + p.Y * u1y);
            double e2 = pts.Max(p => p.X * u2x + p.Y * u2y) - pts.Min(p => p.X * u2x + p.Y * u2y);
            return (e1 + gap1M, e2 + gap2M);
        }

        // =====================================================================
        // overlays (the live preview; nothing is ever written to the map)
        // =====================================================================
        /// <summary>Owns a set of map overlays; replace them with
        /// <see cref="ShowAsync"/>, remove them with <see cref="Clear"/>.
        /// Disposal happens inline on the calling thread (overlay handles
        /// are thread-safe to dispose; queueing close-time work to the
        /// worker is a shutdown race -- see the Bottleneck window).</summary>
        internal sealed class Overlay
        {
            private readonly List<IDisposable> _handles = new List<IDisposable>();
            private bool _busy, _dirty;
            private Func<List<(Geometry geometry, CIMSymbolReference symbol)>> _pending;

            public void Clear()
            {
                var doomed = _handles.ToList();
                _handles.Clear();
                foreach (IDisposable h in doomed)
                {
                    try { h.Dispose(); } catch { }
                }
            }

            /// <summary>Replace the overlays with the shapes
            /// <paramref name="build"/> returns (it runs on the worker).
            /// Coalesced: a call during a refresh queues one more round.</summary>
            public async Task ShowAsync(Func<List<(Geometry geometry, CIMSymbolReference symbol)>> build)
            {
                _pending = build;
                if (_busy)
                {
                    _dirty = true;
                    return;
                }
                _busy = true;
                try
                {
                    do
                    {
                        _dirty = false;
                        Func<List<(Geometry, CIMSymbolReference)>> b = _pending;
                        var old = _handles.ToList();
                        _handles.Clear();
                        List<IDisposable> made = await QueuedTask.Run(() =>
                        {
                            foreach (IDisposable h in old)
                            {
                                try { h.Dispose(); } catch { }
                            }
                            var list = new List<IDisposable>();
                            MapView mv = MapView.Active;
                            if (mv == null || b == null)
                            {
                                return list;
                            }
                            try
                            {
                                foreach (var (geometry, symbol) in b() ?? new List<(Geometry, CIMSymbolReference)>())
                                {
                                    if (geometry == null || geometry.IsEmpty)
                                    {
                                        continue;
                                    }
                                    list.Add(mv.AddOverlay(geometry, symbol));
                                }
                            }
                            catch
                            {
                            }
                            return list;
                        });
                        _handles.AddRange(made);
                    } while (_dirty);
                }
                finally
                {
                    _busy = false;
                }
            }
        }

        public static CIMSymbolReference FillSymbol(byte r, byte g, byte b, byte alpha = 70, double outline = 1.5)
        {
            CIMColor fill = ColorFactory.Instance.CreateRGBColor(r, g, b, alpha);
            CIMColor line = ColorFactory.Instance.CreateRGBColor(r, g, b, 100);
            return SymbolFactory.Instance.ConstructPolygonSymbol(fill, SimpleFillStyle.Solid,
                SymbolFactory.Instance.ConstructStroke(line, outline, SimpleLineStyle.Solid)).MakeSymbolReference();
        }

        public static CIMSymbolReference LineSymbol(byte r, byte g, byte b, double width = 2.5)
        {
            return SymbolFactory.Instance.ConstructLineSymbol(ColorFactory.Instance.CreateRGBColor(r, g, b), width,
                SimpleLineStyle.Solid).MakeSymbolReference();
        }

        public static CIMSymbolReference PointSymbol(byte r, byte g, byte b, double size = 9)
        {
            return SymbolFactory.Instance.ConstructPointSymbol(ColorFactory.Instance.CreateRGBColor(r, g, b), size,
                SimpleMarkerStyle.Circle).MakeSymbolReference();
        }

        /// <summary>A symbol for a preview geometry of any type.</summary>
        public static CIMSymbolReference PreviewSymbol(Geometry g, byte r, byte gg, byte b)
        {
            switch (g?.GeometryType)
            {
                case GeometryType.Polygon:
                case GeometryType.Envelope:
                    return FillSymbol(r, gg, b);
                case GeometryType.Polyline:
                    return LineSymbol(r, gg, b);
                default:
                    return PointSymbol(r, gg, b);
            }
        }

        // =====================================================================
        // the geoprocessing tools
        // =====================================================================
        private static string ToolPathOrThrow(string name)
        {
            return ToolboxPaths.ToolPath(name)
                   ?? throw new InvalidOperationException("Could not find JennessEnterprisesTools.pyt next to the add-in.");
        }

        private static async Task<IGPResult> ExecuteAsync(string toolPath, IReadOnlyList<string> args,
            bool addOutputsToMap = true)
        {
            GPExecuteToolFlags flags = GPExecuteToolFlags.AddToHistory | GPExecuteToolFlags.RefreshProjectItems
                                       | (addOutputsToMap ? GPExecuteToolFlags.AddOutputsToMap : GPExecuteToolFlags.None);
            try
            {
                return await Geoprocessing.ExecuteToolAsync(toolPath, args, null,
                    (System.Threading.CancellationToken?)null, null, flags);
            }
            catch (NullReferenceException)
            {
                // the flags overloads have misfired before (see the Land Facet window)
                return await Geoprocessing.ExecuteToolAsync(toolPath, args);
            }
        }

        private static string Fail(IGPResult result, string what)
        {
            string why = string.Join(" ", result.ErrorMessages.Select(m => m.Text).Take(3));
            return string.IsNullOrWhiteSpace(why) ? what + " failed." : why;
        }

        private static string Num(double v) => v.ToString("R", CultureInfo.InvariantCulture);

        /// <summary>The Plot Designer tool's argument list (window order ==
        /// the tool's parameter order).</summary>
        public static IReadOnlyList<string> PlotDesignerArgs(string outFc, double longEdge, double shortEdge,
            string units, double bearing, int nLong, int nShort, string rates, int nReps, FeatureLayer fieldLayer,
            double? anchorX, double? anchorY, string anchorWkt, bool sideBySide, double gap, string outPoints,
            string outLines, int? seed)
        {
            return Geoprocessing.MakeValueArray(
                outFc ?? "", Num(longEdge), Num(shortEdge), units ?? "Meters", Num(bearing), nLong, nShort,
                rates ?? "", nReps, (object)fieldLayer ?? "",
                anchorX.HasValue ? Num(anchorX.Value) : "", anchorY.HasValue ? Num(anchorY.Value) : "",
                anchorWkt ?? "",
                sideBySide ? "Side by side (across the plot direction)" : "End to end (along the plot direction)",
                Num(gap), outPoints ?? "", outLines ?? "", seed.HasValue ? seed.Value.ToString() : "");
        }

        public static async Task<string> RunPlotDesignerAsync(IReadOnlyList<string> args)
        {
            IGPResult result = await ExecuteAsync(ToolPathOrThrow("PlotDesigner"), args);
            if (result.IsFailed)
            {
                throw new InvalidOperationException(Fail(result, "Plot Designer"));
            }
            string done = result.Messages.Select(m => m.Text).FirstOrDefault(s => s.Contains(" plots: "))
                          ?? "Plots created.";
            return done;
        }

        public static void OpenPlotDesignerTool(IReadOnlyList<string> args)
        {
            Geoprocessing.OpenToolDialog(ToolPathOrThrow("PlotDesigner"), args);
        }

        /// <summary>The Create Array along Axes tool's argument list.</summary>
        public static IReadOnlyList<string> CreateArrayArgs(FeatureLayer inFeatures, double bearing1, double gap1,
            string units, int nForward, int nBack, string outFc, double? bearing2, double gap2, int nRight,
            int nLeft, FeatureLayer clip, bool toGraphics, string geometryJson)
        {
            return Geoprocessing.MakeValueArray(
                (object)inFeatures ?? "", Num(bearing1), Num(gap1), units ?? "Meters", nForward, nBack,
                outFc ?? "", bearing2.HasValue ? Num(bearing2.Value) : "", Num(gap2), nRight, nLeft,
                (object)clip ?? "", toGraphics ? "true" : "false", geometryJson ?? "");
        }

        public static async Task<string> RunCreateArrayAsync(IReadOnlyList<string> args, bool addOutputsToMap = true)
        {
            IGPResult result = await ExecuteAsync(ToolPathOrThrow("CreateArrayAlongAxes"), args, addOutputsToMap);
            if (result.IsFailed)
            {
                throw new InvalidOperationException(Fail(result, "Create Array along Axes"));
            }
            string done = result.Messages.Select(m => m.Text).FirstOrDefault(s => s.Contains("copies written"))
                          ?? "Array created.";
            string warn = string.Join(" ", result.Messages.Where(m => m.Type == GPMessageType.Warning).Select(m => m.Text));
            return done + (warn.Length > 0 ? " " + warn : "");
        }

        public static void OpenCreateArrayTool(IReadOnlyList<string> args)
        {
            Geoprocessing.OpenToolDialog(ToolPathOrThrow("CreateArrayAlongAxes"), args);
        }

        /// <summary>Adds every feature of <paramref name="fcPath"/> to the
        /// active map's graphics layer as a graphic element (the window's
        /// "add as graphics" option; the engine's own graphics option needs
        /// a map layer, which a window run does not add).</summary>
        public static Task<int> AddGraphicsFromFeatureClassAsync(string fcPath, string name)
        {
            return QueuedTask.Run(() =>
            {
                Map map = MapView.Active?.Map;
                if (map == null || string.IsNullOrEmpty(fcPath))
                {
                    return 0;
                }
                GraphicsLayer gl = map.TargetGraphicsLayer
                                   ?? map.GetLayersAsFlattenedList().OfType<GraphicsLayer>().FirstOrDefault()
                                   ?? LayerFactory.Instance.CreateLayer<GraphicsLayer>(new GraphicsLayerCreationParams(), map);
                map.TargetGraphicsLayer = gl;
                int n = 0;
                string dir = System.IO.Path.GetDirectoryName(fcPath);
                string fcName = System.IO.Path.GetFileName(fcPath);
                bool shp = fcName.EndsWith(".shp", StringComparison.OrdinalIgnoreCase);
                if (shp)
                {
                    fcName = fcName.Substring(0, fcName.Length - 4);
                }
                Datastore ds = null;
                FeatureClass fc = null;
                try
                {
                    if (shp)
                    {
                        var fs = new FileSystemDatastore(new FileSystemConnectionPath(new Uri(dir),
                            FileSystemDatastoreType.Shapefile));
                        ds = fs;
                        fc = fs.OpenDataset<FeatureClass>(fcName);
                    }
                    else
                    {
                        var gdb = new Geodatabase(new FileGeodatabaseConnectionPath(new Uri(dir)));
                        ds = gdb;
                        fc = gdb.OpenDataset<FeatureClass>(fcName);
                    }
                    using (RowCursor cur = fc.Search(null, false))
                    {
                        while (cur.MoveNext())
                        {
                            using (var f = cur.Current as Feature)
                            {
                                Geometry g = f?.GetShape();
                                if (g == null || g.IsEmpty)
                                {
                                    continue;
                                }
                                if (map.SpatialReference != null && g.SpatialReference != null
                                    && !g.SpatialReference.IsEqual(map.SpatialReference))
                                {
                                    g = GeometryEngine.Instance.Project(g, map.SpatialReference);
                                }
                                CIMSymbol sym = GraphicSymbol(g);
                                ElementFactory.Instance.CreateGraphicElement(gl, g, sym, name, false);
                                n++;
                            }
                        }
                    }
                }
                finally
                {
                    fc?.Dispose();
                    ds?.Dispose();
                }
                return n;
            });
        }

        /// <summary>The graphics the window adds: a cross-hatched fill with
        /// a dark outline (polygons), a green two-tone line, or a marker --
        /// after the CropCentric symbols.</summary>
        private static CIMSymbol GraphicSymbol(Geometry g)
        {
            switch (g.GeometryType)
            {
                case GeometryType.Polygon:
                case GeometryType.Envelope:
                    return SymbolFactory.Instance.ConstructPolygonSymbol(
                        ColorFactory.Instance.CreateRGBColor(0, 0, 255, 25), SimpleFillStyle.DiagonalCross,
                        SymbolFactory.Instance.ConstructStroke(ColorFactory.Instance.CreateRGBColor(40, 40, 40), 1.5,
                            SimpleLineStyle.Solid));
                case GeometryType.Polyline:
                    return SymbolFactory.Instance.ConstructLineSymbol(ColorFactory.Instance.CreateRGBColor(0, 140, 0), 2.0,
                        SimpleLineStyle.Solid);
                default:
                    return SymbolFactory.Instance.ConstructPointSymbol(ColorFactory.Instance.CreateRGBColor(0, 140, 0), 8,
                        SimpleMarkerStyle.Circle);
            }
        }

        public static void OpenPlotDesignerHelp() => WebHelp.Open("geometric-tools/plot-designer.html");
        public static void OpenCreateArrayHelp() => WebHelp.Open("geometric-tools/create-array-along-axes.html");
    }
}
