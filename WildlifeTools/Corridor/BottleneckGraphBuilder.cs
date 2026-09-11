using System;
using System.Collections.Generic;
using System.Globalization;
using WildlifeTools.PolarPlots;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Builds the Bottleneck Analysis width-profile chart as a
    /// PolarPlotModel (the suite's shared resolution-independent primitive
    /// list) so the same geometry drives the live interactive canvas
    /// (indirectly -- BottleneckWindow.DrawGraph draws the WPF version
    /// itself, for hit-testing the draggable threshold) and the static
    /// layout graphic: axes with nice-step ticks, the width-vs-station
    /// profile line (colored red below the threshold, matching the live
    /// view), and the dashed threshold reference line with its label. Pure
    /// geometry -- no ArcGIS or WPF types.
    /// </summary>
    internal static class BottleneckGraphBuilder
    {
        public static PolarPlotModel Build(double[] stations, double[] widths,
            double threshold, double widthIn = 6.5, double heightIn = 4.0)
        {
            double W = Math.Max(widthIn, 2.5), H = Math.Max(heightIn, 2.0);
            var m = new PolarPlotModel { HalfWidthIn = W / 2, HalfHeightIn = H / 2 };
            PtIn P(double x, double y) => new PtIn(x - W / 2, H / 2 - y);

            if (stations == null || widths == null || widths.Length == 0)
                return m;

            double maxX = stations[stations.Length - 1];
            double maxY = 0;
            foreach (var w in widths) maxY = Math.Max(maxY, w);
            maxY *= 1.06;
            bool km = maxX >= 10000;

            var axis = new RgbaColor(60, 60, 60, 255);
            var grid = new RgbaColor(0, 0, 0, 36);
            var aboveColor = new RgbaColor(31, 119, 180, 255);
            var belowColor = new RgbaColor(198, 40, 40, 255);
            var thrColor = new RgbaColor(85, 85, 85, 255);
            var tickFont = new FontSpec { SizePt = 8.5 };
            var labelFont = new FontSpec { SizePt = 10 };

            double left = 0.62, right = 0.15, top = 0.16, bottom = 0.62;
            double plotX = left, plotY = top;
            double plotW = Math.Max(W - left - right, 0.5);
            double plotH = Math.Max(H - top - bottom, 0.5);

            double XPos(double x) => plotX + (maxX > 0 ? x / maxX : 0) * plotW;
            double YPos(double y) => plotY + plotH - (maxY > 0 ? y / maxY : 0) * plotH;

            // ---- y gridlines + labels ------------------------------------
            double yStep = NiceStep(maxY, 6);
            for (double wv = 0; wv <= maxY + 1e-9; wv += yStep)
            {
                double y = YPos(wv);
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Line,
                    Points = new List<PtIn> { P(plotX, y), P(plotX + plotW, y) },
                    Stroke = wv == 0 ? axis : grid,
                    StrokeWidthPt = 0.7,
                });
                m.Elements.Add(Text(P(plotX - 0.08, y),
                    wv.ToString("#,##0", CultureInfo.InvariantCulture),
                    tickFont, axis, TextAnchor.End));
            }

            // ---- x gridlines + labels -------------------------------------
            double xStep = NiceStep(maxX, 8);
            for (double xv = 0; xv <= maxX + 1e-9; xv += xStep)
            {
                double x = XPos(xv);
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Line,
                    Points = new List<PtIn> { P(x, plotY), P(x, plotY + plotH) },
                    Stroke = xv == 0 ? axis : grid,
                    StrokeWidthPt = 0.7,
                });
                m.Elements.Add(Text(P(x, plotY + plotH + 0.16),
                    (km ? xv / 1000.0 : xv).ToString("#,##0.##",
                        CultureInfo.InvariantCulture),
                    tickFont, axis, TextAnchor.Middle));
            }

            // ---- axis titles ------------------------------------------------
            m.Elements.Add(Text(P(plotX + plotW / 2, plotY + plotH + 0.42),
                km ? "Distance along route (km)" : "Distance along route (m)",
                labelFont, axis, TextAnchor.Middle));
            var yTitle = Text(P(0.16, plotY + plotH / 2), "Corridor width (m)",
                labelFont, axis, TextAnchor.Middle);
            yTitle.RotationDeg = -90;
            m.Elements.Add(yTitle);

            // ---- the profile, split at threshold crossings ------------------
            bool hasThr = !double.IsNaN(threshold);
            var run = new List<PtIn>();
            bool runBelow = hasThr && widths[0] < threshold;
            void Flush()
            {
                if (run.Count < 2) return;
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Polyline,
                    Points = new List<PtIn>(run),
                    Stroke = runBelow ? belowColor : aboveColor,
                    StrokeWidthPt = runBelow ? 1.9 : 1.4,
                });
            }
            for (int i = 0; i < widths.Length; i++)
            {
                bool below = hasThr && widths[i] < threshold;
                var pt = P(XPos(stations[i]), YPos(widths[i]));
                if (below != runBelow && run.Count > 0 && hasThr)
                {
                    double f = (threshold - widths[i - 1])
                               / (widths[i] - widths[i - 1]);
                    f = Math.Max(0, Math.Min(1, f));
                    double xc = stations[i - 1]
                                + f * (stations[i] - stations[i - 1]);
                    var cross = P(XPos(xc), YPos(threshold));
                    run.Add(cross);
                    Flush();
                    run = new List<PtIn> { cross };
                    runBelow = below;
                }
                run.Add(pt);
            }
            Flush();

            // ---- threshold line (dashed) + label -----------------------------
            if (hasThr && threshold <= maxY)
            {
                double ty = YPos(threshold);
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Line,
                    Points = new List<PtIn> { P(plotX, ty), P(plotX + plotW, ty) },
                    Stroke = thrColor,
                    StrokeWidthPt = 1.1,
                    Dash = PolarDash.Dash,
                });
                m.Elements.Add(Text(P(plotX + plotW - 0.06, ty - 0.10),
                    string.Format(CultureInfo.InvariantCulture,
                        "threshold {0:#,##0.#} m", threshold),
                    tickFont, thrColor, TextAnchor.End));
            }

            // ---- plot border --------------------------------------------------
            m.Elements.Add(new PolarElement
            {
                Kind = PolarElementKind.Polyline,
                Points = new List<PtIn>
                {
                    P(plotX, plotY), P(plotX, plotY + plotH),
                    P(plotX + plotW, plotY + plotH), P(plotX + plotW, plotY),
                    P(plotX, plotY),
                },
                Stroke = axis,
                StrokeWidthPt = 1.0,
            });

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

        private static double NiceStep(double range, int target)
        {
            if (range <= 0) return 1;
            double raw = range / Math.Max(1, target);
            double mag = Math.Pow(10, Math.Floor(Math.Log10(raw)));
            foreach (double mm in new[] { 1.0, 2.0, 5.0, 10.0 })
                if (raw <= mm * mag) return mm * mag;
            return 10 * mag;
        }
    }
}
