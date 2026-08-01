using System;
using System.Collections.Generic;
using System.Globalization;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Builds the resolution-independent <see cref="PolarPlotModel"/> from the
    /// settings and the binned azimuth data, for the three core plot types
    /// (Peaks and Valleys, Circular Bins / Rose, Shade by Density). Reference
    /// circles/rays, azimuth labels, inner ring labels, and titles are added
    /// here so every renderer draws the identical plot.
    /// </summary>
    internal static class PolarPlotBuilder
    {
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        public static PolarPlotModel Build(PolarPlotSettings s, double[] bins,
                                           IReadOnlyList<string> secondaryTitles)
        {
            var m = new PolarPlotModel();
            int n = bins?.Length ?? 0;
            double plotR = Math.Max(s.PlotDiameterIn, 0.5) / 2.0;
            double maxVal = 0.0;
            for (int i = 0; i < n; i++) maxVal = Math.Max(maxVal, bins[i]);
            if (maxVal <= 0.0) maxVal = 1.0;

            // Layout margins (inches): room for rim labels + titles. The plot
            // circle stays centered on the origin; the canvas simply grows by
            // the larger of the top (primary title) and bottom (secondary
            // titles) bands, keeping every band clear of the rim labels.
            double labelMargin = s.ShowAzimuthLabels ? 0.45 : 0.12;
            double rimR = plotR + labelMargin;
            double titleTop = s.ShowPrimaryTitle ? s.TitleFontSizePt / 72.0 * 1.8 + 0.1 : 0.0;
            int secLines = (secondaryTitles?.Count ?? 0);
            double titleBottom = (s.ShowSecondaryTitles && secLines > 0)
                ? secLines * s.SecondaryTitleFontSizePt / 72.0 * 1.5 + 0.2 : 0.0;
            m.HalfWidthIn = rimR + 0.1;
            m.HalfHeightIn = rimR + 0.1 + Math.Max(titleTop, titleBottom);
            const double yShift = 0.0;

            // ---- secondary reference rings (drawn first, under the data) -----
            if (s.ShowSecondaryRefCircles && s.SecondaryRefCircles >= 1)
            {
                for (int i = 1; i <= s.SecondaryRefCircles; i++)
                {
                    double rr = plotR * i / (s.SecondaryRefCircles + 1.0);
                    m.Elements.Add(Ring(rr, yShift, s.SecondaryRefColor,
                                        s.SecondaryRefWidthPt, s.SecondaryRefDash));
                }
            }
            // ---- secondary reference rays -----------------------------------
            if (s.ShowSecondaryRefRadius && s.SecondaryRefBearings >= 3)
            {
                double step = 360.0 / s.SecondaryRefBearings;
                for (int i = 0; i < s.SecondaryRefBearings; i++)
                {
                    double az = i * step;
                    if (IsCardinal(az)) continue;          // cardinals are primary
                    m.Elements.Add(Ray(plotR, az, yShift, s.SecondaryRefColor,
                                       s.SecondaryRefWidthPt, s.SecondaryRefDash));
                }
            }

            // ---- the data ---------------------------------------------------
            if (n > 0)
            {
                switch (s.PlotType)
                {
                    case PolarPlotType.PeaksAndValleys:
                        BuildPeaks(m, s, bins, maxVal, plotR, yShift); break;
                    case PolarPlotType.ShadeByDensity:
                        BuildShade(m, s, bins, maxVal, plotR, yShift); break;
                    default:
                        BuildRose(m, s, bins, maxVal, plotR, yShift); break;
                }
            }

            // ---- primary reference: N/E/S/W rays + outer circle at max ------
            if (s.ShowPrimaryRefLines)
            {
                foreach (double az in new[] { 0.0, 90.0, 180.0, 270.0 })
                    m.Elements.Add(Ray(plotR, az, yShift, s.PrimaryRefColor,
                                       s.PrimaryRefWidthPt, PolarDash.Solid));
                m.Elements.Add(Ring(plotR, yShift, s.PrimaryRefColor,
                                    s.PrimaryRefWidthPt, PolarDash.Solid));
            }

            // ---- azimuth labels around the rim ------------------------------
            if (s.ShowAzimuthLabels)
            {
                var azes = new SortedSet<double> { 0, 90, 180, 270 };
                if (s.ShowSecondaryRefRadius && s.SecondaryRefBearings >= 3)
                {
                    double step = 360.0 / s.SecondaryRefBearings;
                    for (int i = 0; i < s.SecondaryRefBearings; i++)
                        azes.Add(CircularStats.Mod360(i * step));
                }
                foreach (double az in azes)
                {
                    var p = PolarPlotModel.Polar(plotR + labelMargin * 0.55, az);
                    var el = Label(
                        ((int)Math.Round(az)).ToString(CI) + "°",
                        p.X, p.Y + yShift, s, TextAnchor.Middle);
                    el.Halo = s.LabelHalo;
                    el.HaloWidthPt = s.LabelHaloWidthPt;
                    if (s.AzimuthLabelsRotated)
                    {
                        // Perpendicular to the bearing ray = tangential flow
                        // around the rim; flip the lower half so nothing reads
                        // upside-down.
                        double rot = az;
                        if (az > 90.0 && az < 270.0) rot = az + 180.0;
                        el.RotationDeg = rot;
                    }
                    m.Elements.Add(el);
                }
            }

            // ---- inner ring labels (values on chosen axes) ------------------
            if (s.ShowInnerLabels && s.ShowSecondaryRefCircles && s.SecondaryRefCircles >= 1)
            {
                var axes = new List<double>();
                if (s.InnerLabelNorth) axes.Add(0);
                if (s.InnerLabelEast) axes.Add(90);
                if (s.InnerLabelSouth) axes.Add(180);
                if (s.InnerLabelWest) axes.Add(270);
                // Nudge the labels off the axis line (perpendicular offset) and
                // give them a halo so they stay readable over the plot.
                double nudge = 0.02 + s.LabelFontSizePt / 72.0 * 0.45;
                foreach (double az in axes)
                    for (int i = 1; i <= s.SecondaryRefCircles; i++)
                    {
                        double frac = i / (s.SecondaryRefCircles + 1.0);
                        double val = maxVal * frac;
                        var p = PolarPlotModel.Polar(plotR * frac, az);
                        bool vertAxis = (az < 45 || az > 315) ||
                                        (az > 135 && az < 225);   // N/S axis
                        double dx = vertAxis ? nudge : 0.0;
                        double dy = vertAxis ? 0.0 : nudge;
                        var el = Label(FormatVal(val), p.X + dx, p.Y + dy + yShift,
                                       s, vertAxis ? TextAnchor.Start : TextAnchor.Middle);
                        el.Halo = s.LabelHalo;
                        el.HaloWidthPt = s.LabelHaloWidthPt;
                        m.Elements.Add(el);
                    }
            }

            // ---- titles -----------------------------------------------------
            if (s.ShowPrimaryTitle && !string.IsNullOrWhiteSpace(s.PrimaryTitle))
            {
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Text,
                    Text = s.PrimaryTitle,
                    Center = new PtIn(0, rimR + 0.1 + s.TitleFontSizePt / 72.0 * 0.7),
                    FontFamily = s.TitleFontFamily,
                    FontSizePt = s.TitleFontSizePt,
                    Bold = s.TitleBold,
                    Italic = s.TitleItalic,
                    Fill = s.TitleColor,
                    Anchor = TextAnchor.Middle,
                });
            }
            if (s.ShowSecondaryTitles && secLines > 0)
            {
                // Below the plot AND below the rim labels; own font/style/color.
                double lh = s.SecondaryTitleFontSizePt / 72.0 * 1.5;
                double y0 = -rimR - 0.12;
                for (int i = 0; i < secLines; i++)
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Text,
                        Text = secondaryTitles[i],
                        Center = new PtIn(0, y0 - (i + 0.5) * lh),
                        FontFamily = s.SecondaryTitleFontFamily,
                        FontSizePt = s.SecondaryTitleFontSizePt,
                        Bold = s.SecondaryTitleBold,
                        Italic = s.SecondaryTitleItalic,
                        Fill = s.SecondaryTitleColor,
                        Anchor = TextAnchor.Middle,
                    });
            }
            return m;
        }

        // ---- data builders --------------------------------------------------
        private static void BuildRose(PolarPlotModel m, PolarPlotSettings s,
            double[] bins, double maxVal, double plotR, double yShift)
        {
            int n = bins.Length;
            double bw = 360.0 / n;
            for (int b = 0; b < n; b++)
            {
                if (bins[b] <= 0) continue;
                double rr = plotR * bins[b] / maxVal;
                double c = CircularStats.BinCenterDeg(b, n);
                m.Elements.Add(Wedge(0, rr, c - bw / 2.0, c + bw / 2.0, yShift,
                                     s.BinFill, s.BinOutline, s.BinOutlineWidthPt));
            }
        }

        private static void BuildShade(PolarPlotModel m, PolarPlotSettings s,
            double[] bins, double maxVal, double plotR, double yShift)
        {
            int n = bins.Length;
            double bw = 360.0 / n;
            double minVal = double.PositiveInfinity;
            for (int b = 0; b < n; b++) minVal = Math.Min(minVal, bins[b]);
            if (double.IsInfinity(minVal)) minVal = 0;
            double span = Math.Max(maxVal - minVal, 1e-9);
            for (int b = 0; b < n; b++)
            {
                double t = (bins[b] - minVal) / span;
                var fill = s.LowDensity.Lerp(s.HighDensity, t);
                double c = CircularStats.BinCenterDeg(b, n);
                m.Elements.Add(Wedge(0, plotR, c - bw / 2.0, c + bw / 2.0, yShift,
                                     fill, s.BinOutline, s.BinOutlineWidthPt));
            }
        }

        private static void BuildPeaks(PolarPlotModel m, PolarPlotSettings s,
            double[] bins, double maxVal, double plotR, double yShift)
        {
            int n = bins.Length;
            var pts = new List<PtIn>(n + 1);
            for (int b = 0; b < n; b++)
            {
                double rr = plotR * bins[b] / maxVal;
                double c = CircularStats.BinCenterDeg(b, n);
                var p = PolarPlotModel.Polar(rr, c);
                pts.Add(new PtIn(p.X, p.Y + yShift));
            }
            m.Elements.Add(new PolarElement
            {
                Kind = PolarElementKind.Polygon,
                Points = pts,
                Fill = s.BinFill,
                Stroke = s.BinOutline,
                StrokeWidthPt = s.BinOutlineWidthPt,
            });
        }

        // ---- primitive helpers ----------------------------------------------
        private static PolarElement Wedge(double r0, double r1, double az0, double az1,
            double yShift, RgbaColor fill, RgbaColor stroke, double widthPt)
        {
            var pts = new List<PtIn>();
            int steps = Math.Max(2, (int)Math.Ceiling((az1 - az0) / 3.0));
            if (r0 <= 1e-9)
                pts.Add(new PtIn(0, yShift));
            else
                for (int i = 0; i <= steps; i++)
                {
                    var p = PolarPlotModel.Polar(r0, az0 + (az1 - az0) * i / steps);
                    pts.Add(new PtIn(p.X, p.Y + yShift));
                }
            for (int i = steps; i >= 0; i--)
            {
                var p = PolarPlotModel.Polar(r1, az0 + (az1 - az0) * i / steps);
                pts.Add(new PtIn(p.X, p.Y + yShift));
            }
            return new PolarElement
            {
                Kind = PolarElementKind.Polygon,
                Points = pts,
                Fill = fill,
                Stroke = stroke,
                StrokeWidthPt = widthPt,
            };
        }

        private static PolarElement Ring(double r, double yShift, RgbaColor stroke,
            double widthPt, PolarDash dash) => new PolarElement
            {
                Kind = PolarElementKind.Circle,
                Center = new PtIn(0, yShift),
                Radius = r,
                Stroke = stroke,
                StrokeWidthPt = widthPt,
                Dash = dash,
            };

        private static PolarElement Ray(double r, double az, double yShift,
            RgbaColor stroke, double widthPt, PolarDash dash)
        {
            var p = PolarPlotModel.Polar(r, az);
            return new PolarElement
            {
                Kind = PolarElementKind.Line,
                Points = new List<PtIn> { new PtIn(0, yShift), new PtIn(p.X, p.Y + yShift) },
                Stroke = stroke,
                StrokeWidthPt = widthPt,
                Dash = dash,
            };
        }

        private static PolarElement Label(string text, double x, double y,
            PolarPlotSettings s, TextAnchor anchor, double sizeOverridePt = 0)
            => new PolarElement
            {
                Kind = PolarElementKind.Text,
                Text = text,
                Center = new PtIn(x, y),
                FontFamily = s.LabelFontFamily,
                FontSizePt = sizeOverridePt > 0 ? sizeOverridePt : s.LabelFontSizePt,
                Bold = s.LabelBold,
                Italic = s.LabelItalic,
                Fill = s.LabelColor,
                Anchor = anchor,
            };

        private static bool IsCardinal(double az)
        {
            double a = CircularStats.Mod360(az);
            return Math.Abs(a) < 1e-6 || Math.Abs(a - 90) < 1e-6 ||
                   Math.Abs(a - 180) < 1e-6 || Math.Abs(a - 270) < 1e-6;
        }

        private static string FormatVal(double v)
        {
            if (v >= 1000) return v.ToString("N0", CI);
            if (v == Math.Floor(v)) return v.ToString("F0", CI);
            return v.ToString("G4", CI);
        }
    }
}
