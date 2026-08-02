using System;
using System.Collections.Generic;
using System.Linq;
using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Renders a <see cref="PolarPlotModel"/> as CIM graphic elements in an
    /// element container (a layout, or a map graphics layer) and groups them --
    /// the third renderer alongside WPF (preview) and SVG (file), built from
    /// the same geometry model so all three outputs match. Model space is
    /// inches, center origin, +y up; the container placement maps it to
    /// (cx, cy) with <paramref name="unitsPerInch"/> container units per model
    /// inch. All methods must run on the MCT (inside QueuedTask.Run).
    /// </summary>
    internal static class PolarPlotCim
    {
        public static GroupElement Place(PolarPlotModel m, IElementContainer container,
            double cx, double cy, double unitsPerInch, string name)
        {
            var made = new List<Element>();
            double X(double xin) => cx + xin * unitsPerInch;
            double Y(double yin) => cy + yin * unitsPerInch;

            foreach (var e in m.Elements)
            {
                try
                {
                    Element el = null;
                    switch (e.Kind)
                    {
                        case PolarElementKind.Circle:
                            el = PlaceRing(e, container, X, Y, unitsPerInch);
                            break;
                        case PolarElementKind.Line:
                        case PolarElementKind.Polyline:
                            el = PlaceLine(e, container, X, Y);
                            break;
                        case PolarElementKind.Polygon:
                            el = PlacePolygon(e, container, X, Y);
                            break;
                        case PolarElementKind.Text:
                            el = PlaceText(e, container, X, Y);
                            break;
                    }
                    if (el != null) made.Add(el);
                }
                catch
                {
                    // Skip an element that fails rather than losing the plot.
                }
            }
            if (made.Count == 0) return null;
            return ElementFactory.Instance.CreateGroupElement(
                container, made, string.IsNullOrWhiteSpace(name) ? "Polar Plot" : name,
                false, null);
        }

        // ---- element builders ----------------------------------------------
        private static Element PlaceRing(PolarElement e, IElementContainer c,
            Func<double, double> X, Func<double, double> Y, double upi)
        {
            bool filled = !e.Fill.IsTransparent;
            bool stroked = !e.Stroke.IsTransparent && e.StrokeWidthPt > 0;
            if (!filled && !stroked) return null;
            var pts = new List<Coordinate2D>(73);
            for (int i = 0; i <= 72; i++)
            {
                double a = i * 5.0 * Math.PI / 180.0;
                pts.Add(new Coordinate2D(
                    X(e.Center.X + e.Radius * Math.Sin(a)),
                    Y(e.Center.Y + e.Radius * Math.Cos(a))));
            }
            if (filled)     // e.g. dot-plot dots
            {
                var poly = new PolygonBuilderEx(pts).ToGeometry();
                return ElementFactory.Instance.CreateGraphicElement(
                    c, poly, PolySymbol(e), null, false, null);
            }
            var line = PolylineBuilderEx.CreatePolyline(pts);
            return ElementFactory.Instance.CreateGraphicElement(
                c, line, LineSymbol(e), null, false, null);
        }

        private static Element PlaceLine(PolarElement e, IElementContainer c,
            Func<double, double> X, Func<double, double> Y)
        {
            if (e.Stroke.IsTransparent || e.StrokeWidthPt <= 0) return null;
            var pts = e.Points.Select(p => new Coordinate2D(X(p.X), Y(p.Y))).ToList();
            if (pts.Count < 2) return null;
            var line = PolylineBuilderEx.CreatePolyline(pts);
            return ElementFactory.Instance.CreateGraphicElement(
                c, line, LineSymbol(e), null, false, null);
        }

        private static Element PlacePolygon(PolarElement e, IElementContainer c,
            Func<double, double> X, Func<double, double> Y)
        {
            var rings = e.Rings ?? new List<List<PtIn>> { e.Points };
            var first = rings.FirstOrDefault(r => r != null && r.Count >= 3);
            if (first == null) return null;
            var pb = new PolygonBuilderEx(
                first.Select(p => new Coordinate2D(X(p.X), Y(p.Y))).ToList());
            foreach (var ring in rings.Skip(1))
            {
                if (ring == null || ring.Count < 3) continue;
                // Holes must wind opposite the exterior; reverse them.
                var rev = ring.Select(p => new Coordinate2D(X(p.X), Y(p.Y))).ToList();
                rev.Reverse();
                pb.AddPart(rev);
            }
            var poly = pb.ToGeometry();
            return ElementFactory.Instance.CreateGraphicElement(
                c, poly, PolySymbol(e), null, false, null);
        }

        private static Element PlaceText(PolarElement e, IElementContainer c,
            Func<double, double> X, Func<double, double> Y)
        {
            if (string.IsNullOrEmpty(e.Text) || e.Fill.IsTransparent) return null;
            var pt = MapPointBuilderEx.CreateMapPoint(
                new Coordinate2D(X(e.Center.X), Y(e.Center.Y)));
            var ts = TextSymbol(e);
            // Name the element after its text so the TOC / contents pane shows
            // which label is which ("Text: 330°", "Text: Sample Title", ...).
            string label = e.Text.Replace("\r", " ").Replace("\n", " ").Trim();
            if (label.Length > 20) label = label.Substring(0, 20) + "...";
            var el = ElementFactory.Instance.CreateTextGraphicElement(
                c, TextType.PointText, pt, ts, e.Text, "Text: " + label, false, null);
            if (el != null && Math.Abs(e.RotationDeg) > 1e-6)
            {
                try { el.SetRotation(-e.RotationDeg); }   // Pro rotation is CCW
                catch { }
            }
            return el;
        }

        // ---- symbols --------------------------------------------------------
        private static CIMColor Color(RgbaColor c) =>
            ColorFactory.Instance.CreateRGBColor(c.R, c.G, c.B, c.A / 255.0 * 100.0);

        private static SimpleLineStyle DashStyle(PolarDash d)
        {
            switch (d)
            {
                case PolarDash.Dot: return SimpleLineStyle.Dot;
                case PolarDash.Dash: return SimpleLineStyle.Dash;
                case PolarDash.DashDot: return SimpleLineStyle.DashDot;
                default: return SimpleLineStyle.Solid;
            }
        }

        private static CIMLineSymbol LineSymbol(PolarElement e) =>
            SymbolFactory.Instance.ConstructLineSymbol(
                Color(e.Stroke), e.StrokeWidthPt, DashStyle(e.Dash));

        private static CIMPolygonSymbol PolySymbol(PolarElement e)
        {
            CIMStroke outline = SymbolFactory.Instance.ConstructStroke(
                Color(e.Stroke.IsTransparent || e.StrokeWidthPt <= 0
                    ? new RgbaColor(0, 0, 0, 0) : e.Stroke),
                Math.Max(e.StrokeWidthPt, 0.0), SimpleLineStyle.Solid);
            return SymbolFactory.Instance.ConstructPolygonSymbol(
                Color(e.Fill), SimpleFillStyle.Solid, outline);
        }

        private static CIMTextSymbol TextSymbol(PolarElement e)
        {
            string style = e.Bold && e.Italic ? "Bold Italic"
                : e.Bold ? "Bold" : e.Italic ? "Italic" : "Regular";
            var ts = SymbolFactory.Instance.ConstructTextSymbol(
                Color(e.Fill), e.FontSizePt, e.FontFamily ?? "Segoe UI", style);
            ts.HorizontalAlignment = e.Anchor == TextAnchor.Start
                ? HorizontalAlignment.Left
                : e.Anchor == TextAnchor.End
                    ? HorizontalAlignment.Right : HorizontalAlignment.Center;
            ts.VerticalAlignment = VerticalAlignment.Center;
            if (!e.Halo.IsTransparent && e.HaloWidthPt > 0)
            {
                ts.HaloSize = e.HaloWidthPt;
                // Fill-only halo symbol. ConstructPolygonSymbol would add its
                // default BLACK outline, which draws as a second dark ring
                // around the white halo.
                ts.HaloSymbol = new CIMPolygonSymbol
                {
                    SymbolLayers = new CIMSymbolLayer[]
                    {
                        new CIMSolidFill { Color = Color(e.Halo), Enable = true },
                    },
                };
            }
            return ts;
        }

        // ---- placement targets ----------------------------------------------
        /// <summary>
        /// Places the plot on the active LAYOUT (page units; centered on the
        /// visible page) or, if a map view is active instead, into a "Polar
        /// Plots" graphics layer (sized to ~30% of the shorter visible extent
        /// dimension). Returns a description of where it went, or throws with a
        /// user-frien­dly message. MCT.
        /// </summary>
        public static string PlaceOnActiveView(PolarPlotModel m, string name,
                                               string graphicsLayerName = "Polar Plots")
        {
            var lv = LayoutView.Active;
            if (lv?.Layout != null)
            {
                var layout = lv.Layout;
                double cx = 0, cy = 0, upi = 1.0;
                try
                {
                    var page = layout.GetPage();
                    cx = page.Width / 2.0;
                    cy = page.Height / 2.0;
                }
                catch { cx = m.HalfWidthIn; cy = m.HalfHeightIn; }
                var grp = Place(m, layout, cx, cy, upi, name);
                if (grp == null) throw new InvalidOperationException(
                    "No plot elements could be created on the layout.");
                return "Added to layout '" + layout.Name + "' (grouped as '" +
                       grp.Name + "').";
            }

            var mv = MapView.Active;
            if (mv?.Map != null)
            {
                var map = mv.Map;
                GraphicsLayer gl = map.GetLayersAsFlattenedList()
                    .OfType<GraphicsLayer>()
                    .FirstOrDefault(g => g.Name == graphicsLayerName)
                    ?? LayerFactory.Instance.CreateLayer<GraphicsLayer>(
                        new GraphicsLayerCreationParams { Name = graphicsLayerName }, map);
                var ext = mv.Extent;
                double cx = (ext.XMin + ext.XMax) / 2.0;
                double cy = (ext.YMin + ext.YMax) / 2.0;
                double shorter = Math.Min(ext.Width, ext.Height);
                double upi = shorter * 0.30 / Math.Max(2.0 * m.HalfWidthIn, 0.1);
                var grp = Place(m, gl, cx, cy, upi, name);
                if (grp == null) throw new InvalidOperationException(
                    "No plot elements could be created in the graphics layer.");
                return "Added to the '" + graphicsLayerName +
                       "' graphics layer in map '" + map.Name + "'.";
            }

            throw new InvalidOperationException(
                "Open a layout view (or a map view) first, then add the plot.");
        }

        /// <summary>
        /// One-click statistics: places a text element on the active layout
        /// whose text carries ArcGIS formatting tags (rendered by Pro). MCT.
        /// </summary>
        public static string PlaceStatsTextOnLayout(string taggedText)
        {
            var lv = LayoutView.Active;
            if (lv?.Layout == null)
                throw new InvalidOperationException(
                    "Open a layout view first, then add the statistics text.");
            var layout = lv.Layout;
            double cx = 1.0, cy = 1.0;
            try
            {
                var page = layout.GetPage();
                cx = page.Width * 0.5;
                cy = page.Height * 0.5;
            }
            catch { }
            var pt = MapPointBuilderEx.CreateMapPoint(new Coordinate2D(cx, cy));
            var ts = SymbolFactory.Instance.ConstructTextSymbol(
                ColorFactory.Instance.CreateRGBColor(0, 0, 0), 10, "Segoe UI", "Regular");
            ts.HorizontalAlignment = HorizontalAlignment.Left;
            var el = ElementFactory.Instance.CreateTextGraphicElement(
                layout, TextType.PointText, pt, ts, taggedText,
                "Circular Statistics", false, null);
            if (el == null)
                throw new InvalidOperationException("The text element could not be created.");
            return "Statistics text element added to layout '" + layout.Name + "'.";
        }
    }
}
