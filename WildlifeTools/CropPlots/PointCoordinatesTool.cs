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
using System.Linq;
using System.Threading.Tasks;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Dialogs;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Internal.Layouts.Ribbon;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// The Point Coordinates tool: click a map, or a map frame on a layout,
    /// and a callout graphic appears describing that point -- longitude and
    /// latitude with the geographic coordinate system's name and, when the
    /// map is projected, the projected X and Y with the projected system's
    /// name -- in the lines, fonts and callout style chosen in Point
    /// Coordinates Settings. On a map the graphic goes to the map's
    /// graphics layer; on a layout it describes the TOPMOST map frame under
    /// the click and the graphic goes into the layout itself, where it is
    /// easy to move and edit. A click on a layout outside every map frame
    /// adds nothing. A port of the CropCentric Add Coordinates tool.
    ///
    /// Built on Esri's own element tool base (the class behind Insert &gt;
    /// Text), so one click works on maps, layouts and layouts without a map
    /// frame alike; see CompassRoseGraphicTool for the recipe.
    /// </summary>
    internal class PointCoordinatesTool : NewElementToolBase
    {
        public const string Id = "WildlifeTools_PointCoordinatesTool";
        public const string GraphicName = "Point Coordinates";

        public PointCoordinatesTool()
            : base(SketchGeometryType.Point, ServiceContracts.LayoutElementType.PointText,
                   CompassRose.InsertCursor, "Point Coordinates")
        {
        }

        protected override async Task OnActivateImpl(bool hasViewChanged)
        {
            if (LayoutView.Active == null && MapView.Active?.Map is Map map)
            {
                try
                {
                    await QueuedTask.Run(() =>
                    {
                        if (map.TargetGraphicsLayer == null)
                        {
                            GraphicsLayer gl =
                                map.GetLayersAsFlattenedList().OfType<GraphicsLayer>().FirstOrDefault()
                                ?? LayerFactory.Instance.CreateLayer<GraphicsLayer>(
                                       new GraphicsLayerCreationParams(), map);
                            map.TargetGraphicsLayer = gl;
                        }
                    });
                }
                catch
                {
                }
            }
            await base.OnActivateImpl(hasViewChanged);
        }

        protected override async Task<bool> OnSketchFinished(
            ArcGIS.Desktop.Internal.Mapping.View.ViewSketchCompleteEventArgs e)
        {
            if (e != null && e.GeometryType != SketchType)
            {
                return false;
            }
            string geomStr = e?.GeometryString;
            try
            {
                await QueuedTask.Run(() =>
                {
                    Geometry sketch = CompassRose.SketchGeometryFromString(geomStr);
                    MapPoint click = sketch as MapPoint ?? sketch?.Extent?.Center;
                    if (click == null)
                    {
                        return;
                    }
                    PointCoordinatesSettings settings = PointCoordinatesSettings.Load();
                    LayoutView lv = LayoutView.Active;
                    if (lv?.Layout is Layout layout)
                    {
                        PlaceOnLayout(layout, click, settings);
                    }
                    else if (MapView.Active?.Map is Map map)
                    {
                        PlaceOnMap(map, MapView.Active, click, settings);
                    }
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show("Unable to add the coordinates graphic:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
            // stay on the tool: the user usually wants several points. (Esri's
            // element tools switch to Select; a coordinates tool is a probe.)
            return true;
        }

        // =====================================================================
        // where the click landed
        // =====================================================================
        private static void PlaceOnMap(Map map, MapView mv, MapPoint click, PointCoordinatesSettings s)
        {
            GraphicsLayer gl = map.TargetGraphicsLayer
                               ?? map.GetLayersAsFlattenedList().OfType<GraphicsLayer>().FirstOrDefault()
                               ?? LayerFactory.Instance.CreateLayer<GraphicsLayer>(new GraphicsLayerCreationParams(), map);
            map.TargetGraphicsLayer = gl;
            SpatialReference sr = click.SpatialReference ?? map.SpatialReference;
            MapPoint p = click.SpatialReference == null ? MapPointBuilderEx.CreateMapPoint(click.X, click.Y, sr) : click;
            string text = Describe(p, sr, s);
            if (string.IsNullOrEmpty(text))
            {
                return;
            }
            // the text sits up and to the right of the point, a little way
            // into the view, with a leader back to the point
            Envelope ve = mv.Extent;
            double off = Math.Max(ve.Width, ve.Height) * 0.04;
            MapPoint textAt = MapPointBuilderEx.CreateMapPoint(p.X + off, p.Y + off * 0.6, sr);
            AddElements(gl, p, textAt, text, s);
        }

        private static void PlaceOnLayout(Layout layout, MapPoint pagePoint, PointCoordinatesSettings s)
        {
            // the topmost map frame under the click (highest Z order wins)
            MapFrame frame = layout.GetElementsAsFlattenedList().OfType<MapFrame>()
                .Where(f => f.Map != null)
                .Where(f =>
                {
                    try
                    {
                        Envelope b = f.GetBounds(true);
                        return b != null && GeometryEngine.Instance.Contains(b, pagePoint);
                    }
                    catch
                    {
                        return false;
                    }
                })
                .OrderByDescending(f => f.ZOrder)
                .FirstOrDefault();
            if (frame == null)
            {
                return;             // outside every map frame: nothing to describe
            }
            MapPoint mapPoint = frame.PageToMap(pagePoint);
            SpatialReference sr = mapPoint?.SpatialReference ?? frame.Map.SpatialReference;
            if (mapPoint == null)
            {
                return;
            }
            string text = Describe(mapPoint, sr, s);
            if (string.IsNullOrEmpty(text))
            {
                return;
            }
            double pageW = 8.5;
            try { pageW = layout.GetPage().Width; } catch { }
            double off = pageW * 0.03;
            MapPoint textAt = MapPointBuilderEx.CreateMapPoint(pagePoint.X + off, pagePoint.Y + off * 0.6,
                pagePoint.SpatialReference);
            AddElements(layout, pagePoint, textAt, text, s);
        }

        // =====================================================================
        // the description
        // =====================================================================
        /// <summary>The formatted callout text for a point in <paramref name="sr"/>.</summary>
        internal static string Describe(MapPoint p, SpatialReference sr, PointCoordinatesSettings s)
        {
            if (sr == null || !s.AnyLine)
            {
                return "";
            }
            SpatialReference gcs = CropPlotActions.Gcs(sr);
            double lon, lat;
            if (sr.IsGeographic)
            {
                lon = p.X;
                lat = p.Y;
            }
            else
            {
                MapPoint g = (MapPoint)GeometryEngine.Instance.Project(p, gcs);
                lon = g.X;
                lat = g.Y;
            }
            double? x = null, y = null;
            string pcs = null, unit = null;
            if (!sr.IsGeographic)
            {
                x = p.X;
                y = p.Y;
                pcs = sr.Name;
                unit = UnitAbbrev(sr);
            }
            var lines = s.Lines(lon, lat, gcs?.Name, x, y, pcs, unit);
            return lines.Count == 0 ? "" : s.FormattedText(lines);
        }

        private static string UnitAbbrev(SpatialReference sr)
        {
            string n = "";
            try { n = sr.Unit?.Name ?? ""; } catch { }
            switch (n.ToLowerInvariant())
            {
                case "meter": return "m";
                case "foot": return "ft";
                case "foot_us": return "ft US";
                case "kilometer": return "km";
                case "mile_us": case "mile": return "mi";
                case "yard": return "yd";
                default: return n.Replace('_', ' ');
            }
        }

        // =====================================================================
        // the graphics
        // =====================================================================
        private static CIMColor Rgb(string hex, byte alpha = 100)
        {
            string h = (hex ?? "#000000").TrimStart('#');
            int r = 0, g = 0, b = 0;
            if (h.Length == 6)
            {
                r = Convert.ToInt32(h.Substring(0, 2), 16);
                g = Convert.ToInt32(h.Substring(2, 2), 16);
                b = Convert.ToInt32(h.Substring(4, 2), 16);
            }
            return ColorFactory.Instance.CreateRGBColor(r, g, b, alpha);
        }

        /// <summary>The text symbol with the chosen callout.</summary>
        internal static CIMTextSymbol TextSymbol(PointCoordinatesSettings s)
        {
            CIMTextSymbol sym = SymbolFactory.Instance.ConstructTextSymbol(Rgb(s.Coordinates.Color),
                s.Coordinates.Size, s.Coordinates.Family ?? "Arial", StyleName(s.Coordinates));
            sym.HorizontalAlignment = HorizontalAlignment.Left;
            sym.VerticalAlignment = VerticalAlignment.Bottom;
            CIMPolygonSymbol bg = SymbolFactory.Instance.ConstructPolygonSymbol(Rgb(s.BackgroundColor),
                SimpleFillStyle.Solid, SymbolFactory.Instance.ConstructStroke(Rgb(s.OutlineColor), 0.6, SimpleLineStyle.Solid));
            CIMLineSymbol leader = SymbolFactory.Instance.ConstructLineSymbol(Rgb(s.LeaderColor), 0.8, SimpleLineStyle.Solid);
            var margin = new CIMTextMargin { Left = 5, Right = 5, Top = 5, Bottom = 5 };
            switch (s.Callout)
            {
                case PointCoordinatesSettings.CalloutBalloon:
                    sym.Callout = new CIMBalloonCallout
                    {
                        BalloonStyle = BalloonCalloutStyle.RoundedRectangle,
                        BackgroundSymbol = bg,
                        Margin = margin,
                        LeaderTolerance = 15,
                    };
                    break;
                case PointCoordinatesSettings.CalloutBackground:
                    sym.Callout = new CIMBackgroundCallout
                    {
                        BackgroundSymbol = bg,
                        Margin = margin,
                        LeaderTolerance = 15,
                        LeaderLineSymbol = leader,
                    };
                    break;
                case PointCoordinatesSettings.CalloutSimpleLine:
                    sym.Callout = new CIMSimpleLineCallout
                    {
                        LineSymbol = leader,
                        AutoSnap = true,
                        LeaderTolerance = 15,
                    };
                    break;
                default:
                    sym.Callout = null;
                    break;
            }
            return sym;
        }

        private static string StyleName(CalloutTextStyle st)
        {
            if (st.Bold && st.Italic) return "Bold Italic";
            if (st.Bold) return "Bold";
            if (st.Italic) return "Italic";
            return "Regular";
        }

        private static void AddElements(IElementContainer container, MapPoint anchor, MapPoint textAt, string text,
            PointCoordinatesSettings s)
        {
            CIMTextSymbol sym = TextSymbol(s);
            var graphic = new CIMTextGraphic
            {
                Text = text,
                Shape = textAt,
                Symbol = sym.MakeSymbolReference(),
            };
            if (s.Callout != PointCoordinatesSettings.CalloutNone)
            {
                graphic.Leaders = new CIMLeader[] { new CIMLeaderPoint { Point = anchor } };
            }
            ElementFactory.Instance.CreateGraphicElement(container, graphic, GraphicName, false, null);
            if (s.Marker)
            {
                CIMPointSymbol marker = SymbolFactory.Instance.ConstructPointSymbol(Rgb(s.MarkerColor), s.MarkerSize,
                    SimpleMarkerStyle.Circle);
                try
                {
                    if (marker.SymbolLayers?.FirstOrDefault() is CIMVectorMarker vm)
                    {
                        foreach (CIMMarkerGraphic mg in vm.MarkerGraphics ?? Array.Empty<CIMMarkerGraphic>())
                        {
                            if (mg.Symbol is CIMPolygonSymbol ps)
                            {
                                var layers = new List<CIMSymbolLayer>(ps.SymbolLayers ?? Array.Empty<CIMSymbolLayer>())
                                {
                                    SymbolFactory.Instance.ConstructStroke(Rgb("#4E4E4E"), 1.2, SimpleLineStyle.Solid),
                                };
                                ps.SymbolLayers = layers.ToArray();
                            }
                        }
                    }
                }
                catch
                {
                }
                ElementFactory.Instance.CreateGraphicElement(container, anchor, marker, GraphicName + " marker", false, null);
            }
        }
    }
}
