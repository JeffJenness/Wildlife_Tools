using System;
using System.Globalization;
using System.Text;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Renders a <see cref="PolarPlotModel"/> to an SVG string. Model space is
    /// inches, center origin, +y up; SVG is px (96/in), top-left origin, +y down.
    /// Pure C#, no ArcGIS/WPF dependency.
    /// </summary>
    internal static class PolarPlotSvg
    {
        private const double PxPerIn = 96.0;
        private const double PtToPx = 96.0 / 72.0;
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        public static string Render(PolarPlotModel m)
        {
            double wPx = 2 * m.HalfWidthIn * PxPerIn;
            double hPx = 2 * m.HalfHeightIn * PxPerIn;
            double X(double xin) => (xin + m.HalfWidthIn) * PxPerIn;
            double Y(double yin) => (m.HalfHeightIn - yin) * PxPerIn;   // flip y

            var sb = new StringBuilder();
            sb.Append("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"no\"?>\n");
            sb.AppendFormat(CI,
                "<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"{0}\" height=\"{1}\" " +
                "viewBox=\"0 0 {0} {1}\">\n", F(wPx), F(hPx));

            foreach (var e in m.Elements)
            {
                switch (e.Kind)
                {
                    case PolarElementKind.Circle:
                        sb.AppendFormat(CI,
                            "<circle cx=\"{0}\" cy=\"{1}\" r=\"{2}\" {3} {4}/>\n",
                            F(X(e.Center.X)), F(Y(e.Center.Y)), F(e.Radius * PxPerIn),
                            e.Fill.IsTransparent ? "fill=\"none\"" : FillAttrs(e),
                            StrokeAttrs(e));
                        break;
                    case PolarElementKind.Line:
                    case PolarElementKind.Polyline:
                    case PolarElementKind.Polygon:
                        {
                            if (e.Kind == PolarElementKind.Polygon && e.Rings != null)
                            {
                                // Multi-ring (donut) polygon: one path with a
                                // subpath per ring, even-odd filled.
                                var d = new StringBuilder();
                                foreach (var ring in e.Rings)
                                {
                                    if (ring == null || ring.Count == 0) continue;
                                    for (int i = 0; i < ring.Count; i++)
                                        d.AppendFormat(CI, "{0}{1},{2} ",
                                            i == 0 ? "M" : "L",
                                            F(X(ring[i].X)), F(Y(ring[i].Y)));
                                    d.Append("Z ");
                                }
                                sb.AppendFormat(CI,
                                    "<path d=\"{0}\" fill-rule=\"evenodd\" {1} {2}/>\n",
                                    d.ToString().Trim(), FillAttrs(e), StrokeAttrs(e));
                                break;
                            }
                            var pts = new StringBuilder();
                            foreach (var p in e.Points)
                                pts.AppendFormat(CI, "{0},{1} ", F(X(p.X)), F(Y(p.Y)));
                            string tag = e.Kind == PolarElementKind.Polygon ? "polygon" : "polyline";
                            sb.AppendFormat(CI, "<{0} points=\"{1}\" {2} {3}/>\n",
                                tag, pts.ToString().Trim(), FillAttrs(e), StrokeAttrs(e));
                            break;
                        }
                    case PolarElementKind.Text:
                        {
                            string anchor = e.Anchor == TextAnchor.Start ? "start"
                                : e.Anchor == TextAnchor.End ? "end" : "middle";
                            double sz = e.FontSizePt * PtToPx;
                            // dominant-baseline central so y is the vertical center
                            string halo = (!e.Halo.IsTransparent && e.HaloWidthPt > 0)
                                ? string.Format(CI,
                                    " paint-order=\"stroke\" stroke=\"{0}\" " +
                                    "stroke-opacity=\"{1}\" stroke-width=\"{2}\" " +
                                    "stroke-linejoin=\"round\"",
                                    e.Halo.ToSvg(), F(e.Halo.SvgOpacity),
                                    F(e.HaloWidthPt * PtToPx * 2.0))
                                : "";
                            string styleAttrs =
                                (e.Bold ? " font-weight=\"bold\"" : "") +
                                (e.Italic ? " font-style=\"italic\"" : "");
                            sb.AppendFormat(CI,
                                "<text x=\"{0}\" y=\"{1}\" font-family=\"{2}\" " +
                                "font-size=\"{3}\"{8} fill=\"{4}\" fill-opacity=\"{5}\" " +
                                "text-anchor=\"{6}\" dominant-baseline=\"central\"{7}",
                                F(X(e.Center.X)), F(Y(e.Center.Y)),
                                Escape(e.FontFamily ?? "sans-serif"), F(sz),
                                e.Fill.ToSvg(), F(e.Fill.SvgOpacity), anchor, halo,
                                styleAttrs);
                            if (Math.Abs(e.RotationDeg) > 1e-6)
                                sb.AppendFormat(CI, " transform=\"rotate({0} {1} {2})\"",
                                    F(e.RotationDeg), F(X(e.Center.X)), F(Y(e.Center.Y)));
                            sb.AppendFormat(CI, ">{0}</text>\n", Escape(e.Text ?? ""));
                            break;
                        }
                }
            }
            sb.Append("</svg>\n");
            return sb.ToString();
        }

        private static string FillAttrs(PolarElement e) =>
            e.Fill.IsTransparent
                ? "fill=\"none\""
                : string.Format(CI, "fill=\"{0}\" fill-opacity=\"{1}\"",
                                e.Fill.ToSvg(), F(e.Fill.SvgOpacity));

        private static string StrokeAttrs(PolarElement e)
        {
            if (e.Stroke.IsTransparent || e.StrokeWidthPt <= 0)
                return "stroke=\"none\"";
            string dash = DashArray(e.Dash, e.StrokeWidthPt);
            return string.Format(CI,
                "stroke=\"{0}\" stroke-opacity=\"{1}\" stroke-width=\"{2}\"{3}",
                e.Stroke.ToSvg(), F(e.Stroke.SvgOpacity), F(e.StrokeWidthPt * PtToPx),
                dash);
        }

        private static string DashArray(PolarDash d, double widthPt)
        {
            double w = Math.Max(widthPt * PtToPx, 0.5);
            switch (d)
            {
                case PolarDash.Dot: return string.Format(CI,
                    " stroke-dasharray=\"{0},{1}\"", F(w), F(w * 2));
                case PolarDash.Dash: return string.Format(CI,
                    " stroke-dasharray=\"{0},{1}\"", F(w * 4), F(w * 3));
                case PolarDash.DashDot: return string.Format(CI,
                    " stroke-dasharray=\"{0},{1},{2},{1}\"", F(w * 4), F(w * 2), F(w));
                default: return "";
            }
        }

        private static string F(double v) => v.ToString("0.###", CI);

        private static string Escape(string s) => (s ?? "")
            .Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;")
            .Replace("\"", "&quot;");
    }
}
