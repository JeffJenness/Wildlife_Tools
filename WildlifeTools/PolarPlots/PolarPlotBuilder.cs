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
using System.Windows;
using System.Windows.Media;

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
                                           IReadOnlyList<string> secondaryTitles,
                                           IReadOnlyList<double> rawAzimuths = null,
                                           IReadOnlyList<double> rawWeights = null,
                                           IReadOnlyList<double> magnitudes = null,
                                           CircularStats stats = null)
        {
            var m = new PolarPlotModel();
            int n = bins?.Length ?? 0;
            bool external = s.IsExternal;
            // External plots: bins ride outward from an inner circle to the
            // outer diameter; all other plots fill a single data circle.
            double plotR = external
                ? Math.Max(s.OuterDiameterIn, 0.75) / 2.0
                : Math.Max(s.PlotDiameterIn, 0.5) / 2.0;
            double innerR = external
                ? Math.Min(Math.Max(s.InnerDiameterIn, 0.2) / 2.0, plotR * 0.9)
                : 0.0;
            double maxVal = 0.0;
            for (int i = 0; i < n; i++) maxVal = Math.Max(maxVal, bins[i]);
            if (maxVal <= 0.0) maxVal = 1.0;

            // Wind-rose magnitude classes: computed up front because the legend
            // affects the canvas size. Falls back to a plain rose when no
            // usable magnitude values exist.
            WindBands wind = (s.PlotType == PolarPlotType.WindRose)
                ? WindBands.Compute(s, magnitudes) : null;

            // Layout margins (inches): room for rim labels + titles. The plot
            // circle stays centered on the origin; the canvas simply grows by
            // the larger of the top (primary title) and bottom (secondary
            // titles / wind legend) bands, keeping every band clear of the rim
            // labels. External plots put their azimuth labels INSIDE the inner
            // circle, so no rim band is needed for them.
            // The azimuth labels are placed from their measured size, so each
            // one sits the same small gap from the circle at any font size
            // and plot diameter; the rim band is as wide as they need.
            var azLabels = s.ShowAzimuthLabels
                ? LayoutAzimuthLabels(s, external, innerR, plotR)
                : new List<AzLabel>();
            double labelMargin = 0.12;
            if (!external)
                foreach (var lab in azLabels)
                    labelMargin = Math.Max(labelMargin, lab.FarR - plotR + 0.04);
            double rimR = plotR + labelMargin;
            double titleTop = s.ShowPrimaryTitle ? s.TitleFontSizePt / 72.0 * 1.8 + 0.1 : 0.0;
            int secLines = (secondaryTitles?.Count ?? 0);
            double titleBottom = (s.ShowSecondaryTitles && secLines > 0)
                ? secLines * s.SecondaryTitleFontSizePt / 72.0 * 1.5 + 0.2 : 0.0;
            double legendRow = 0.18;
            double legendBottom = (wind != null)
                ? 0.24 + (wind.Breaks.Length - 1) * legendRow : 0.0;
            // Shade by Density: the value of a wedge is its SHADE, not its
            // length, so a color legend below the plot (under any secondary
            // titles) takes the place of the ring-value labels. It follows
            // the same checkbox as those labels.
            bool shadeLegend = s.PlotType == PolarPlotType.ShadeByDensity &&
                               s.ShowInnerLabels && n > 0;
            double lf = s.LabelFontSizePt / 72.0;
            double shadeLegendH = shadeLegend ? 1.5 * lf + 0.14 + 1.5 * lf + 0.10 : 0.0;
            // Bands above and below the rim: the primary title on top; the
            // secondary titles, then any legend, stacked below. The canvas
            // holds exactly these, and the finished plot is shifted (end of
            // Build) so no band is mirrored as blank space on the far side.
            double topBand = titleTop;
            double bottomBand = titleBottom + shadeLegendH + legendBottom;
            m.HalfWidthIn = rimR + 0.06;
            m.HalfHeightIn = rimR + 0.06 + (topBand + bottomBand) / 2.0;
            const double yShift = 0.0;

            // Radius of a value ring at fraction f of the value range: from the
            // center for ordinary plots, from the inner circle for external.
            // Equal-area scaling (Sanderson & Peacock 2020) maps value
            // fractions through a square root so petal AREA, not radius, is
            // proportional to frequency; a ring at radius fraction f then
            // represents the value f^2 * max.
            double RingR(double frac) => innerR + (plotR - innerR) * frac;
            double RingValue(double frac) => maxVal * (s.EqualArea ? frac * frac : frac);

            // ---- secondary reference rings (drawn first, under the data) -----
            if (s.ShowSecondaryRefCircles && s.SecondaryRefCircles >= 1)
            {
                for (int i = 1; i <= s.SecondaryRefCircles; i++)
                {
                    double rr = RingR(i / (s.SecondaryRefCircles + 1.0));
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
                    m.Elements.Add(Ray(innerR, plotR, az, yShift, s.SecondaryRefColor,
                                       s.SecondaryRefWidthPt, s.SecondaryRefDash));
                }
            }

            // ---- the data ---------------------------------------------------
            if (n > 0 || s.PlotType == PolarPlotType.Bearings)
            {
                switch (s.PlotType)
                {
                    case PolarPlotType.PeaksAndValleys:
                        BuildPeaks(m, s, bins, maxVal, plotR, yShift); break;
                    case PolarPlotType.ShadeByDensity:
                        BuildShade(m, s, bins, maxVal, plotR, yShift); break;
                    case PolarPlotType.ExternalPeaksAndValleys:
                        BuildExternalPeaks(m, s, bins, maxVal, innerR, plotR, yShift); break;
                    case PolarPlotType.ExternalBins:
                        BuildExternalRose(m, s, bins, maxVal, innerR, plotR, yShift); break;
                    case PolarPlotType.Bearings:
                        BuildBearings(m, s, rawAzimuths, rawWeights, plotR, yShift); break;
                    case PolarPlotType.WindRose:
                        if (wind != null)
                            BuildWindRose(m, s, rawAzimuths, rawWeights, magnitudes,
                                          wind, maxVal, plotR, yShift);
                        else
                            BuildRose(m, s, bins, maxVal, plotR, yShift);
                        break;
                    case PolarPlotType.SmoothedDensity:
                        BuildKde(m, s, rawAzimuths, rawWeights, plotR, yShift); break;
                    case PolarPlotType.DotPlot:
                        BuildDotPlot(m, s, rawAzimuths, plotR, yShift); break;
                    default:
                        BuildRose(m, s, bins, maxVal, plotR, yShift); break;
                }
            }

            // ---- primary reference: N/E/S/W rays + outer circle at max ------
            if (s.ShowPrimaryRefLines)
            {
                foreach (double az in new[] { 0.0, 90.0, 180.0, 270.0 })
                    m.Elements.Add(Ray(external ? innerR : 0.0, plotR, az, yShift,
                                       s.PrimaryRefColor, s.PrimaryRefWidthPt,
                                       PolarDash.Solid));
                m.Elements.Add(Ring(plotR, yShift, s.PrimaryRefColor,
                                    s.PrimaryRefWidthPt, PolarDash.Solid));
                if (external)   // the inner circle the bins ride on
                    m.Elements.Add(Ring(innerR, yShift, s.PrimaryRefColor,
                                        s.PrimaryRefWidthPt, PolarDash.Solid));
            }

            // ---- mean-direction overlay (arrow, length = r-bar; optional
            // 95% confidence arc, von Mises large-sample s.e. 1/sqrt(n r-bar
            // kappa), Fisher 1993 / Mardia & Jupp 2000) ----------------------
            if (s.ShowMeanArrow && stats != null)
                BuildMeanOverlay(m, s, stats, external ? innerR : 0.0, plotR, yShift);

            // ---- azimuth labels around the rim ------------------------------
            foreach (var lab in azLabels)
            {
                var p = PolarPlotModel.Polar(lab.R, lab.Az);
                var el = Label(lab.Text, p.X, p.Y + yShift, s, TextAnchor.Middle);
                el.Halo = s.LabelHalo;
                el.HaloWidthPt = s.LabelHaloWidthPt;
                el.RotationDeg = lab.RotationDeg;
                m.Elements.Add(el);
            }

            // ---- inner ring labels (values on chosen axes) ------------------
            // Suppressed for the smoothed-density and dot plots, whose radii
            // are not on the binned-count scale the rings would imply, and
            // for Shade by Density, whose wedges all reach the circle (its
            // values are in the color legend instead).
            bool ringsHaveValues = s.PlotType != PolarPlotType.SmoothedDensity &&
                                   s.PlotType != PolarPlotType.DotPlot &&
                                   s.PlotType != PolarPlotType.ShadeByDensity;
            if (ringsHaveValues &&
                s.ShowInnerLabels && s.ShowSecondaryRefCircles && s.SecondaryRefCircles >= 1)
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
                        double val = RingValue(frac);
                        var p = PolarPlotModel.Polar(RingR(frac), az);
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

            // ---- Shade by Density color legend (below the plot) -------------
            if (shadeLegend)
            {
                double minV = double.PositiveInfinity;
                for (int i = 0; i < n; i++) minV = Math.Min(minV, bins[i]);
                double maxV = Math.Max(maxVal, minV);
                double yTop = -rimR - 0.10 - (titleBottom > 0 ? titleBottom - 0.10 : 0.0);
                var hdr = Label("Bin value", 0, yTop - 0.75 * lf, s, TextAnchor.Middle);
                hdr.Bold = true;
                m.Elements.Add(hdr);
                double barTop = yTop - 1.5 * lf, barBot = barTop - 0.14;
                double barW = Math.Min(1.8, 2.0 * rimR * 0.8);
                double x0 = -barW / 2.0;
                const int cells = 24;
                double cw = barW / cells;
                for (int i = 0; i < cells; i++)
                {
                    // Same color rule as the wedges: low -> high color across
                    // the lowest to the highest bin value.
                    var col = s.LowDensity.Lerp(s.HighDensity, (i + 0.5) / cells);
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Polygon,
                        Points = new List<PtIn>
                        {
                            new PtIn(x0 + i * cw, barBot), new PtIn(x0 + (i + 1) * cw, barBot),
                            new PtIn(x0 + (i + 1) * cw, barTop), new PtIn(x0 + i * cw, barTop),
                        },
                        Fill = col,
                        Stroke = col,               // hides hairline seams
                        StrokeWidthPt = 0.3,
                    });
                }
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Polyline,
                    Points = new List<PtIn>
                    {
                        new PtIn(x0, barBot), new PtIn(x0 + barW, barBot),
                        new PtIn(x0 + barW, barTop), new PtIn(x0, barTop),
                        new PtIn(x0, barBot),
                    },
                    Stroke = s.PrimaryRefColor,
                    StrokeWidthPt = 0.5,
                });
                double yLab = barBot - 0.75 * lf - 0.03;
                m.Elements.Add(Label(FormatVal(minV), x0, yLab, s, TextAnchor.Start));
                m.Elements.Add(Label(FormatVal((minV + maxV) / 2.0), 0, yLab, s,
                                     TextAnchor.Middle));
                m.Elements.Add(Label(FormatVal(maxV), x0 + barW, yLab, s, TextAnchor.End));
            }

            // ---- wind-rose band legend (bottom-left) ------------------------
            if (wind != null)
            {
                double sq = 0.11;                       // swatch square size
                double x0 = -m.HalfWidthIn + 0.08;
                double yTop = -rimR - 0.16 - (titleBottom > 0 ? titleBottom - 0.10 : 0.0);
                int k = wind.Breaks.Length - 1;
                var hdr = Label(string.IsNullOrWhiteSpace(s.MagnitudeField)
                        ? "Bands" : s.MagnitudeField,
                    x0, yTop, s, TextAnchor.Start);
                hdr.Bold = true;
                m.Elements.Add(hdr);
                for (int c = 0; c < k; c++)
                {
                    double yc = yTop - (c + 1) * legendRow;
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Polygon,
                        Points = new List<PtIn>
                        {
                            new PtIn(x0, yc - sq / 2), new PtIn(x0 + sq, yc - sq / 2),
                            new PtIn(x0 + sq, yc + sq / 2), new PtIn(x0, yc + sq / 2),
                        },
                        Fill = wind.BandColor(s, c),
                        Stroke = s.BinOutline,
                        StrokeWidthPt = Math.Min(s.BinOutlineWidthPt, 0.5),
                    });
                    m.Elements.Add(Label(
                        FormatVal(wind.Breaks[c]) + " – " + FormatVal(wind.Breaks[c + 1]),
                        x0 + sq + 0.06, yc, s, TextAnchor.Start));
                }
            }
            // Laid out around the origin; shift so the top band holds only
            // the title and the bottom band only the titles and legend.
            m.Shift(0.0, (bottomBand - topBand) / 2.0);
            return m;
        }

        /// <summary>Equal-interval magnitude classes for the wind rose; null
        /// when no usable magnitude values exist.</summary>
        private sealed class WindBands
        {
            public double[] Breaks;                 // length = classes + 1

            public static WindBands Compute(PolarPlotSettings s,
                                            IReadOnlyList<double> mags)
            {
                if (mags == null || mags.Count == 0) return null;
                double lo = double.PositiveInfinity, hi = double.NegativeInfinity;
                foreach (double v in mags)
                {
                    if (double.IsNaN(v)) continue;
                    if (v < lo) lo = v;
                    if (v > hi) hi = v;
                }
                if (double.IsInfinity(lo)) return null;
                int k = Math.Min(Math.Max(s.WindClasses, 1), 9);
                if (hi <= lo) { hi = lo + 1.0; k = 1; }
                var b = new double[k + 1];
                for (int i = 0; i <= k; i++) b[i] = lo + (hi - lo) * i / k;
                return new WindBands { Breaks = b };
            }

            public int ClassOf(double v)
            {
                if (double.IsNaN(v)) return -1;
                int k = Breaks.Length - 1;
                if (v >= Breaks[k]) return k - 1;   // top edge inclusive
                int c = (int)((v - Breaks[0]) / (Breaks[k] - Breaks[0]) * k);
                return Math.Min(Math.Max(c, 0), k - 1);
            }

            public RgbaColor BandColor(PolarPlotSettings s, int c)
            {
                int k = Breaks.Length - 1;
                double t = (k <= 1) ? 1.0 : (double)c / (k - 1);
                return s.WindLow.Lerp(s.WindHigh, t);
            }
        }

        // ---- data builders --------------------------------------------------
        /// <summary>Value fraction -> radius fraction; square-rooted when the
        /// equal-area option is on so petal area tracks frequency.</summary>
        private static double ValFrac(PolarPlotSettings s, double v, double maxVal)
        {
            double f = maxVal > 0 ? v / maxVal : 0.0;
            if (f < 0) f = 0; if (f > 1) f = 1;
            return s.EqualArea ? Math.Sqrt(f) : f;
        }

        private static void BuildRose(PolarPlotModel m, PolarPlotSettings s,
            double[] bins, double maxVal, double plotR, double yShift)
        {
            int n = bins.Length;
            double bw = 360.0 / n;
            for (int b = 0; b < n; b++)
            {
                if (bins[b] <= 0) continue;
                double rr = plotR * ValFrac(s, bins[b], maxVal);
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
                double rr = plotR * ValFrac(s, bins[b], maxVal);
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

        private static PolarElement Ray(double rFrom, double rTo, double az,
            double yShift, RgbaColor stroke, double widthPt, PolarDash dash)
        {
            var a = PolarPlotModel.Polar(rFrom, az);
            var b = PolarPlotModel.Polar(rTo, az);
            return new PolarElement
            {
                Kind = PolarElementKind.Line,
                Points = new List<PtIn> { new PtIn(a.X, a.Y + yShift),
                                          new PtIn(b.X, b.Y + yShift) },
                Stroke = stroke,
                StrokeWidthPt = widthPt,
                Dash = dash,
            };
        }

        // ---- external + bearings data builders ------------------------------
        private static void BuildExternalRose(PolarPlotModel m, PolarPlotSettings s,
            double[] bins, double maxVal, double innerR, double outerR, double yShift)
        {
            int n = bins.Length;
            double bw = 360.0 / n;
            for (int b = 0; b < n; b++)
            {
                if (bins[b] <= 0) continue;
                double rr = innerR + (outerR - innerR) * ValFrac(s, bins[b], maxVal);
                double c = CircularStats.BinCenterDeg(b, n);
                m.Elements.Add(Wedge(innerR, rr, c - bw / 2.0, c + bw / 2.0, yShift,
                                     s.BinFill, s.BinOutline, s.BinOutlineWidthPt));
            }
        }

        private static void BuildExternalPeaks(PolarPlotModel m, PolarPlotSettings s,
            double[] bins, double maxVal, double innerR, double outerR, double yShift)
        {
            // A donut band: outer ring follows the bin values outward from the
            // inner circle; the inner circle is the hole (even-odd fill).
            int n = bins.Length;
            var outer = new List<PtIn>(n);
            for (int b = 0; b < n; b++)
            {
                double rr = innerR + (outerR - innerR) * ValFrac(s, bins[b], maxVal);
                double c = CircularStats.BinCenterDeg(b, n);
                var p = PolarPlotModel.Polar(rr, c);
                outer.Add(new PtIn(p.X, p.Y + yShift));
            }
            var hole = new List<PtIn>(73);
            for (int i = 0; i <= 72; i++)
            {
                var p = PolarPlotModel.Polar(innerR, i * 5.0);
                hole.Add(new PtIn(p.X, p.Y + yShift));
            }
            m.Elements.Add(new PolarElement
            {
                Kind = PolarElementKind.Polygon,
                Rings = new List<List<PtIn>> { outer, hole },
                Fill = s.BinFill,
                Stroke = s.BinOutline,
                StrokeWidthPt = s.BinOutlineWidthPt,
            });
        }

        private static void BuildBearings(PolarPlotModel m, PolarPlotSettings s,
            IReadOnlyList<double> azimuths, IReadOnlyList<double> weights,
            double plotR, double yShift)
        {
            if (azimuths == null || azimuths.Count == 0) return;
            // Each observation is its own line from the center; when weighted,
            // line length is proportional to the observation's weight.
            double maxW = 1.0;
            bool weighted = false;
            if (weights != null && weights.Count == azimuths.Count)
            {
                for (int i = 0; i < weights.Count; i++)
                    if (weights[i] > maxW) { maxW = weights[i]; weighted = true; }
                if (!weighted)
                    for (int i = 0; i < weights.Count && !weighted; i++)
                        weighted = Math.Abs(weights[i] - 1.0) > 1e-12;
            }
            double widthPt = Math.Max(s.BearingLineWidthPt, 0.2);
            for (int i = 0; i < azimuths.Count; i++)
            {
                double w = (weighted && weights != null && i < weights.Count)
                    ? weights[i] / maxW : 1.0;
                if (w <= 0) continue;
                m.Elements.Add(Ray(0.0, plotR * w, azimuths[i], yShift,
                                   s.BearingLine, widthPt, PolarDash.Solid));
            }
        }

        // ---- phase 3 data builders ------------------------------------------
        private static void BuildWindRose(PolarPlotModel m, PolarPlotSettings s,
            IReadOnlyList<double> azimuths, IReadOnlyList<double> weights,
            IReadOnlyList<double> mags, WindBands wind, double maxVal,
            double plotR, double yShift)
        {
            // Stacked petals: each direction bin's wedge is subdivided into the
            // magnitude classes, lowest class innermost, cumulative radius on
            // the SAME scale as the plain rose (bin totals), so the petal
            // outline matches the ordinary rose of the same data.
            int nb = Math.Max(1, s.BinCount);
            int k = wind.Breaks.Length - 1;
            var sums = new double[nb, k];
            double bw = 360.0 / nb;
            for (int i = 0; i < azimuths.Count; i++)
            {
                if (mags == null || i >= mags.Count) break;
                int c = wind.ClassOf(mags[i]);
                if (c < 0) continue;                  // missing magnitude
                // Nearest-CENTER binning, identical to CircularStats.Bin():
                // bin b is centered on b*bw, so the drawn petal (also centered
                // on b*bw) contains exactly the data it claims and the wind
                // rose lines up with the plain rose of the same data.
                int b = (int)Math.Floor((CircularStats.Mod360(azimuths[i]) + bw / 2.0) / bw);
                if (b >= nb) b -= nb;
                double w = (weights != null && i < weights.Count) ? weights[i] : 1.0;
                sums[b, c] += w;
            }
            for (int b = 0; b < nb; b++)
            {
                double center = CircularStats.BinCenterDeg(b, nb);
                double cum = 0.0, r0 = 0.0;
                for (int c = 0; c < k; c++)
                {
                    if (sums[b, c] <= 0) continue;
                    cum += sums[b, c];
                    double r1 = plotR * ValFrac(s, cum, maxVal);
                    if (r1 - r0 > 1e-6)
                        m.Elements.Add(Wedge(r0, r1, center - bw / 2.0,
                            center + bw / 2.0, yShift, wind.BandColor(s, c),
                            s.BinOutline, s.BinOutlineWidthPt));
                    r0 = r1;
                }
            }
        }

        private static void BuildKde(PolarPlotModel m, PolarPlotSettings s,
            IReadOnlyList<double> azimuths, IReadOnlyList<double> weights,
            double plotR, double yShift)
        {
            if (azimuths == null || azimuths.Count == 0) return;
            // von Mises kernel density (Fisher 1993): the data are binned onto
            // a fine 0.5-degree grid, then circularly convolved with the
            // kernel exp(kappa*(cos(d)-1)); the silhouette is scaled to its
            // own maximum, so the normalization constant cancels.
            const int G = 720;
            var grid = new double[G];
            for (int i = 0; i < azimuths.Count; i++)
            {
                int g = (int)Math.Floor(CircularStats.Mod360(azimuths[i]) / 360.0 * G);
                if (g >= G) g = 0;
                grid[g] += (weights != null && i < weights.Count) ? weights[i] : 1.0;
            }
            double sigmaDeg = Math.Min(Math.Max(s.SmoothingDeg, 1.0), 90.0);
            double sigmaRad = sigmaDeg * Math.PI / 180.0;
            double kappa = 1.0 / (sigmaRad * sigmaRad);
            int half = Math.Min(G / 2, (int)Math.Ceiling(4.0 * sigmaDeg / (360.0 / G)));
            var kernel = new double[2 * half + 1];
            for (int j = -half; j <= half; j++)
            {
                double d = j * (360.0 / G) * Math.PI / 180.0;
                kernel[j + half] = Math.Exp(kappa * (Math.Cos(d) - 1.0));
            }
            var dens = new double[G];
            double dmax = 0.0;
            for (int g = 0; g < G; g++)
            {
                double sum = 0.0;
                for (int j = -half; j <= half; j++)
                {
                    int src = g + j;
                    if (src < 0) src += G; else if (src >= G) src -= G;
                    sum += grid[src] * kernel[j + half];
                }
                dens[g] = sum;
                if (sum > dmax) dmax = sum;
            }
            if (dmax <= 0) return;
            var pts = new List<PtIn>(G);
            for (int g = 0; g < G; g++)
            {
                double az = (g + 0.5) * 360.0 / G;
                double rr = plotR * ValFrac(s, dens[g], dmax);
                var p = PolarPlotModel.Polar(rr, az);
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

        private static void BuildDotPlot(PolarPlotModel m, PolarPlotSettings s,
            IReadOnlyList<double> azimuths, double plotR, double yShift)
        {
            if (azimuths == null || azimuths.Count == 0) return;
            // One dot per observation (unweighted), stacked inward from the
            // plotting circle (Fisher 1993, Batschelet 1981). The angular
            // column width is derived from the dot size: one dot fits across a
            // column at the plotting radius, so the grouping is visually
            // self-evident. Tall stacks compress their spacing to stay inside.
            double dotD = Math.Min(Math.Max(s.DotDiameterIn, 0.02), plotR / 2.0);
            int cols = Math.Max(8, (int)Math.Round(2.0 * Math.PI * plotR / dotD));
            var counts = new int[cols];
            foreach (double az in azimuths)
            {
                int c = (int)Math.Floor(CircularStats.Mod360(az) / 360.0 * cols);
                if (c >= cols) c = 0;
                counts[c]++;
            }
            int maxStack = 0;
            foreach (int c in counts) if (c > maxStack) maxStack = c;
            if (maxStack == 0) return;
            double step = dotD;
            double avail = plotR - dotD;              // keep the innermost dot off center
            if (maxStack * step > avail && maxStack > 1)
                step = avail / maxStack;
            var stacked = new int[cols];
            foreach (double az in azimuths)
            {
                int c = (int)Math.Floor(CircularStats.Mod360(az) / 360.0 * cols);
                if (c >= cols) c = 0;
                double rr = plotR - dotD / 2.0 - stacked[c] * step;
                stacked[c]++;
                if (rr < dotD / 2.0) rr = dotD / 2.0;
                double center = (c + 0.5) * 360.0 / cols;
                var p = PolarPlotModel.Polar(rr, center);
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Circle,
                    Center = new PtIn(p.X, p.Y + yShift),
                    Radius = dotD / 2.0,
                    Fill = s.BinFill,
                    Stroke = s.BinOutline,
                    StrokeWidthPt = Math.Min(s.BinOutlineWidthPt, 0.75),
                });
            }
        }

        private static void BuildMeanOverlay(PolarPlotModel m, PolarPlotSettings s,
            CircularStats st, double baseR, double plotR, double yShift)
        {
            if (double.IsNaN(st.MeanDirectionDeg) || st.MeanResultantLength <= 0)
                return;
            // Axial data: the mean axis runs both ways, so the arrow and
            // the confidence arc are drawn at both ends.
            var ends = st.Axial
                ? new[] { st.MeanDirectionDeg, CircularStats.Mod360(st.MeanDirectionDeg + 180.0) }
                : new[] { st.MeanDirectionDeg };
            foreach (double az in ends)
                BuildMeanEnd(m, s, st, baseR, plotR, yShift, az);
        }

        private static void BuildMeanEnd(PolarPlotModel m, PolarPlotSettings s,
            CircularStats st, double baseR, double plotR, double yShift, double az)
        {
            double tipR = baseR + (plotR - baseR) * st.MeanResultantLength;
            var col = s.MeanArrowColor;
            double shaftPt = 1.8;

            // Shaft.
            m.Elements.Add(Ray(baseR, tipR, az, yShift, col, shaftPt, PolarDash.Solid));
            // Arrowhead (filled triangle).
            double headLen = Math.Min(0.12, Math.Max(0.05, (tipR - baseR) * 0.35));
            double halfW = headLen * 0.42;
            double rad = az * Math.PI / 180.0;
            double dxU = Math.Sin(rad), dyU = Math.Cos(rad);       // outward
            double pxU = dyU, pyU = -dxU;                          // perpendicular
            double bx = dxU * (tipR - headLen), by = dyU * (tipR - headLen);
            m.Elements.Add(new PolarElement
            {
                Kind = PolarElementKind.Polygon,
                Points = new List<PtIn>
                {
                    new PtIn(dxU * tipR, dyU * tipR + yShift),
                    new PtIn(bx + pxU * halfW, by + pyU * halfW + yShift),
                    new PtIn(bx - pxU * halfW, by - pyU * halfW + yShift),
                },
                Fill = col,
                Stroke = col,
                StrokeWidthPt = 0.5,
            });

            // 95% confidence arc just outside the plotting circle -- the same
            // CircularStats.MeanCiHalfDeg95 the statistics report prints, so
            // the arc and the reported interval always agree.
            if (s.ShowMeanCI)
            {
                double halfDeg = st.MeanCiHalfDeg95;
                if (!double.IsNaN(halfDeg))
                {
                    double rArc = plotR + 0.055;
                    var arc = new List<PtIn>();
                    int steps = Math.Max(6, (int)Math.Ceiling(halfDeg));
                    for (int i = -steps; i <= steps; i++)
                    {
                        var p = PolarPlotModel.Polar(rArc, az + halfDeg * i / steps);
                        arc.Add(new PtIn(p.X, p.Y + yShift));
                    }
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Polyline,
                        Points = arc,
                        Stroke = col,
                        StrokeWidthPt = 1.5,
                    });
                    foreach (int sgn in new[] { -1, 1 })    // end ticks
                    {
                        var p1 = PolarPlotModel.Polar(rArc - 0.035, az + sgn * halfDeg);
                        var p2 = PolarPlotModel.Polar(rArc + 0.035, az + sgn * halfDeg);
                        m.Elements.Add(new PolarElement
                        {
                            Kind = PolarElementKind.Line,
                            Points = new List<PtIn>
                            {
                                new PtIn(p1.X, p1.Y + yShift),
                                new PtIn(p2.X, p2.Y + yShift),
                            },
                            Stroke = col,
                            StrokeWidthPt = 1.5,
                        });
                    }
                }
            }
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

        // ---- azimuth label layout -------------------------------------------
        private sealed class AzLabel
        {
            public double Az, R, RotationDeg, FarR;
            public string Text;
        }

        /// <summary>The bearings that get a degree label: the secondary
        /// reference bearings when those rays are drawn (so 13 bearings label
        /// 0, 28, 55, ... and not the cardinals), otherwise N, E, S and W.</summary>
        private static List<double> LabelBearings(PolarPlotSettings s)
        {
            var azes = new List<double>();
            if (s.ShowSecondaryRefRadius && s.SecondaryRefBearings >= 3)
            {
                double step = 360.0 / s.SecondaryRefBearings;
                for (int i = 0; i < s.SecondaryRefBearings; i++)
                    azes.Add(CircularStats.Mod360(i * step));
            }
            else
                azes.AddRange(new[] { 0.0, 90.0, 180.0, 270.0 });
            return azes;
        }

        /// <summary>Places each degree label from its measured size: outside
        /// the rim, its nearest edge a fixed gap beyond the circle; on the
        /// External types, inside the inner circle, its nearest edge the same
        /// gap within it. The gap scales with the font (about half a letter
        /// height) and clears the confidence arc when that is drawn.</summary>
        private static List<AzLabel> LayoutAzimuthLabels(PolarPlotSettings s,
            bool external, double innerR, double plotR)
        {
            var list = new List<AzLabel>();
            double em = s.LabelFontSizePt / 72.0;
            double gap = 0.03 + 0.45 * em + s.PrimaryRefWidthPt / 72.0 / 2.0;
            if (!external && s.ShowMeanArrow && s.ShowMeanCI)
                gap = Math.Max(gap, 0.055 + 0.035 + 0.03);   // the arc and its ticks
            foreach (double az in LabelBearings(s))
            {
                string text = ((int)Math.Round(az)).ToString(CI) + "°";
                double rot = 0.0;
                if (s.AzimuthLabelsRotated)
                {
                    // Perpendicular to the bearing ray = tangential flow
                    // around the rim; flip the lower half so nothing reads
                    // upside-down.
                    rot = az;
                    if (az > 90.0 && az < 270.0) rot = az + 180.0;
                }
                var corners = LabelInkCorners(text, s, rot);
                double a = az * Math.PI / 180.0;
                double ux = Math.Sin(a), uy = Math.Cos(a);      // outward
                double minDot = double.PositiveInfinity, maxDot = double.NegativeInfinity;
                foreach (var c in corners)
                {
                    double d = c.X * ux + c.Y * uy;
                    minDot = Math.Min(minDot, d);
                    maxDot = Math.Max(maxDot, d);
                }
                double r = external
                    ? Math.Max(innerR - gap - maxDot, innerR * 0.3)
                    : plotR + gap - minDot;
                double far = 0.0;
                foreach (var c in corners)
                    far = Math.Max(far, Math.Sqrt(
                        Math.Pow(r * ux + c.X, 2) + Math.Pow(r * uy + c.Y, 2)));
                list.Add(new AzLabel { Az = az, R = r, RotationDeg = rot,
                                       Text = text, FarR = far });
            }
            return list;
        }

        /// <summary>The four corners of a label's INK box, in inches relative
        /// to the point the renderers center it on, turned by its rotation
        /// (clockwise positive, model +y up). Measured with the label's own
        /// font; falls back to an estimate if measuring fails.</summary>
        private static List<PtIn> LabelInkCorners(string text, PolarPlotSettings s,
                                                  double rotationDeg)
        {
            double left, right, top, bottom;          // inches, model +y up
            try
            {
                var tf = new Typeface(new FontFamily(s.LabelFontFamily ?? "Segoe UI"),
                    s.LabelItalic ? FontStyles.Italic : FontStyles.Normal,
                    s.LabelBold ? FontWeights.Bold : FontWeights.Normal,
                    FontStretches.Normal);
                var ft = new FormattedText(text, CI, FlowDirection.LeftToRight, tf,
                    s.LabelFontSizePt * 96.0 / 72.0, Brushes.Black, 1.0);
                // Same placement as PolarPlotWpf.DrawText: the line box is
                // centered on the anchor point.
                var b = ft.BuildGeometry(new Point(-ft.Width / 2.0, -ft.Height / 2.0)).Bounds;
                if (b.IsEmpty || b.Width <= 0) throw new InvalidOperationException();
                left = b.Left / 96.0; right = b.Right / 96.0;
                top = -b.Top / 96.0; bottom = -b.Bottom / 96.0;
            }
            catch
            {
                double em = s.LabelFontSizePt / 72.0;
                double w = text.Length * 0.55 * em;
                left = -w / 2.0; right = w / 2.0;
                top = 0.30 * em; bottom = -0.40 * em;
            }
            var pts = new List<PtIn>
            {
                new PtIn(left, top), new PtIn(right, top),
                new PtIn(right, bottom), new PtIn(left, bottom),
            };
            if (Math.Abs(rotationDeg) < 1e-9) return pts;
            double t = rotationDeg * Math.PI / 180.0;
            double cs = Math.Cos(t), sn = Math.Sin(t);
            var rotated = new List<PtIn>(4);
            foreach (var p in pts)       // clockwise with +y up
                rotated.Add(new PtIn(p.X * cs + p.Y * sn, -p.X * sn + p.Y * cs));
            return rotated;
        }

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
