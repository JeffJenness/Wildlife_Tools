using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using WildlifeTools.Corridor;
using WildlifeTools.PolarPlots;

namespace WildlifeTools
{
    /// <summary>
    /// Builds the LoCoH Parameter Explorer chart as a PolarPlotModel (the
    /// suite's shared resolution-independent primitive list): the 50 / 75 /
    /// 95 percent isopleth-area curves against the swept parameter, a dashed
    /// reference line at the published starting heuristic, axes with nice
    /// 1-2-5 ticks, title and legend. Pure geometry -- no ArcGIS or WPF
    /// types -- so the live preview, PNG/SVG export and layout graphic all
    /// render the identical model.
    /// </summary>
    internal static class LocohSweepChart
    {
        // Okabe-Ito colorblind-safe: blue, orange, green
        private static readonly RgbaColor C50 = new RgbaColor(0, 114, 178, 255);
        private static readonly RgbaColor C75 = new RgbaColor(230, 159, 0, 255);
        private static readonly RgbaColor C95 = new RgbaColor(0, 158, 115, 255);
        private static readonly RgbaColor Axis = new RgbaColor(60, 60, 60, 255);
        private static readonly RgbaColor Grid = new RgbaColor(225, 225, 225, 255);
        private static readonly RgbaColor Heur = new RgbaColor(120, 120, 120, 255);

        public static PolarPlotModel Build(LocohSweepResult r,
            double widthIn, double heightIn,
            Func<string, double, string, double> measureIn)
        {
            double W = Math.Max(widthIn, 3.0), H = Math.Max(heightIn, 2.4);
            var m = new PolarPlotModel { HalfWidthIn = W / 2, HalfHeightIn = H / 2 };
            PtIn P(double x, double y) => new PtIn(x - W / 2, H / 2 - y);

            var titleFont = new FontSpec { SizePt = 13, Bold = true };
            var labelFont = new FontSpec { SizePt = 10 };
            var tickFont = new FontSpec { SizePt = 8.5 };
            var legendFont = new FontSpec { SizePt = 9 };

            double top = 0.58;                       // title + n line
            double left = 0.26 + 0.60;               // y label + y ticks
            double right = 0.15;
            double bottom = 0.24 + 0.30 + 0.34;      // x ticks + x label + legend
            double plotX = left, plotY = top;
            double plotW = Math.Max(W - left - right, 0.5);
            double plotH = Math.Max(H - top - bottom, 0.5);

            // ---- ranges -------------------------------------------------
            var steps = r.Steps.Where(s => !double.IsNaN(s.AreaKm95)
                || !double.IsNaN(s.AreaKm75)
                || !double.IsNaN(s.AreaKm50)).ToList();
            double xMax = Math.Max(r.Steps.Max(s => s.Param),
                                   r.HeuristicParam) * 1.05;
            if (xMax <= 0) xMax = 1;
            double yMax = 0;
            foreach (var s in steps)
                foreach (var v in new[] { s.AreaKm50, s.AreaKm75, s.AreaKm95 })
                    if (!double.IsNaN(v)) yMax = Math.Max(yMax, v);
            if (yMax <= 0) yMax = 1;
            int yTicks = Math.Max(2, (int)(plotH / 0.42));
            double yStep = HistogramMath.NiceStep(yMax * 1.05, yTicks);
            double yTop = Math.Ceiling(yMax * 1.05 / yStep) * yStep;
            double xStep = HistogramMath.NiceStep(xMax, Math.Max(3,
                (int)(plotW / 0.7)));

            double XPos(double v) => plotX + v / xMax * plotW;
            double YPos(double v) => plotY + plotH - v / yTop * plotH;

            // ---- grid + y ticks ----------------------------------------
            for (double yv = 0; yv <= yTop + yStep * 0.001; yv += yStep)
            {
                double y = YPos(yv);
                if (yv > 0)
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Line,
                        Points = new List<PtIn>
                        { P(plotX, y), P(plotX + plotW, y) },
                        Stroke = Grid,
                        StrokeWidthPt = 0.7,
                    });
                m.Elements.Add(Text(P(plotX - 0.07, y),
                    HistogramMath.AxisNumber(yv, yStep), tickFont, Axis,
                    TextAnchor.End));
            }

            // ---- x ticks ------------------------------------------------
            for (double xv = 0; xv <= xMax + xStep * 0.001; xv += xStep)
            {
                double x = XPos(xv);
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Line,
                    Points = new List<PtIn>
                    { P(x, plotY + plotH), P(x, plotY + plotH + 0.05) },
                    Stroke = Axis,
                    StrokeWidthPt = 0.9,
                });
                m.Elements.Add(Text(P(x, plotY + plotH + 0.16),
                    HistogramMath.AxisNumber(xv, xStep), tickFont, Axis,
                    TextAnchor.Middle));
            }

            // ---- heuristic reference line ------------------------------
            if (r.HeuristicParam > 0 && r.HeuristicParam <= xMax)
            {
                double hx = XPos(r.HeuristicParam);
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Line,
                    Points = new List<PtIn>
                    { P(hx, plotY), P(hx, plotY + plotH) },
                    Stroke = Heur,
                    StrokeWidthPt = 1.1,
                    Dash = PolarDash.Dash,
                });
                // label flips to the LEFT of the line when the line sits in
                // the right part of the plot, so it never clips off the edge
                bool leftSide = hx > plotX + plotW * 0.62;
                m.Elements.Add(Text(
                    P(hx + (leftSide ? -0.05 : 0.05), plotY + 0.12),
                    r.HeuristicLabel, tickFont, Heur,
                    leftSide ? TextAnchor.End : TextAnchor.Start));
            }

            // ---- the three curves --------------------------------------
            void Curve(Func<LocohSweepStep, double> pick, RgbaColor color)
            {
                var pts = new List<PtIn>();
                foreach (var s in r.Steps.OrderBy(s2 => s2.Param))
                {
                    double v = pick(s);
                    if (double.IsNaN(v)) continue;
                    pts.Add(P(XPos(s.Param), YPos(Math.Min(v, yTop))));
                }
                if (pts.Count >= 2)
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Polyline,
                        Points = pts,
                        Stroke = color,
                        StrokeWidthPt = 1.8,
                    });
                foreach (var p in pts)
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Circle,
                        Center = p,
                        Radius = 0.030,
                        Fill = color,
                    });
            }
            Curve(s => s.AreaKm50, C50);
            Curve(s => s.AreaKm75, C75);
            Curve(s => s.AreaKm95, C95);

            // ---- axes ---------------------------------------------------
            m.Elements.Add(new PolarElement
            {
                Kind = PolarElementKind.Polyline,
                Points = new List<PtIn>
                {
                    P(plotX, plotY), P(plotX, plotY + plotH),
                    P(plotX + plotW, plotY + plotH),
                },
                Stroke = Axis,
                StrokeWidthPt = 1.1,
            });

            // ---- title, n, axis labels ---------------------------------
            m.Elements.Add(Text(P(plotX + plotW / 2, 0.20),
                "LoCoH leveling-off — " + r.MethodTitle, titleFont, Axis,
                TextAnchor.Middle));
            m.Elements.Add(Text(P(plotX + plotW / 2, 0.44),
                r.LayerName + "   (n = " +
                r.N.ToString("N0", CultureInfo.InvariantCulture) +
                " points)", legendFont, Axis, TextAnchor.Middle));
            m.Elements.Add(Text(P(plotX + plotW / 2, plotY + plotH + 0.42),
                r.ParamAxisLabel, labelFont, Axis, TextAnchor.Middle));
            var yl = Text(P(0.16, plotY + plotH / 2),
                "Isopleth area (km²)", labelFont, Axis, TextAnchor.Middle);
            yl.RotationDeg = -90;
            m.Elements.Add(yl);

            // ---- legend (one centered row) ------------------------------
            double MeasureText(string t, FontSpec f) =>
                measureIn != null ? measureIn(t ?? "", f.SizePt, f.Family)
                : 0.075 * (t?.Length ?? 0) * f.SizePt / 9.0;
            var entries = new (string label, RgbaColor color)[]
            {
                ("50% of points", C50),
                ("75% of points", C75),
                ("95% of points", C95),
            };
            double totalW = entries.Sum(e =>
                0.30 + MeasureText(e.label, legendFont) + 0.22);
            double lx = plotX + (plotW - totalW) / 2;
            double ly = H - 0.26;
            foreach (var (label, color) in entries)
            {
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Line,
                    Points = new List<PtIn>
                    { P(lx, ly + 0.08), P(lx + 0.24, ly + 0.08) },
                    Stroke = color,
                    StrokeWidthPt = 2.0,
                });
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Circle,
                    Center = P(lx + 0.12, ly + 0.08),
                    Radius = 0.030,
                    Fill = color,
                });
                m.Elements.Add(Text(P(lx + 0.30, ly + 0.075), label,
                    legendFont, Axis, TextAnchor.Start));
                lx += 0.30 + MeasureText(label, legendFont) + 0.22;
            }
            return m;
        }

        private static PolarElement Text(PtIn at, string text, FontSpec f,
            RgbaColor color, TextAnchor anchor) => new PolarElement
        {
            Kind = PolarElementKind.Text,
            Center = at,
            Text = text,
            FontFamily = string.IsNullOrEmpty(f.Family) ? "Segoe UI" : f.Family,
            FontSizePt = f.SizePt,
            Bold = f.Bold,
            Italic = f.Italic,
            Fill = color,
            Anchor = anchor,
        };
    }
}
