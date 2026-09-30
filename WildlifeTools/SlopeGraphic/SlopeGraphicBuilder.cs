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
using WildlifeTools.PolarPlots;

namespace WildlifeTools.SlopeGraphic
{
    /// <summary>
    /// Builds the Slope Graphic as a <see cref="PolarPlotModel"/> so the
    /// existing WPF / SVG / CIM renderers draw it unchanged. Geometry follows
    /// the original ArcView Grid Tools graphic: quarter-plane axes, a range
    /// wedge (0.70R), the SD wedge above it (0.875R), and the mean line
    /// longest (R), each extending past the one behind it; new elements are
    /// the percentile wedge (0.79R), dashed median line (0.95R), protractor
    /// arc with ticks (1.06R), 95% CI arc (1.13R), and value labels. Angles
    /// are degrees above horizontal; all boundary values are computed in the
    /// SOURCE unit and converted to angles for drawing.
    /// </summary>
    internal static class SlopeGraphicBuilder
    {
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        public static PolarPlotModel Build(SlopeGraphicSettings s, SlopeStats st,
                                           IReadOnlyList<string> secondaryTitles)
        {
            var m = new PolarPlotModel();
            double R = Math.Max(s.SizeIn, 0.5);

            // ---- canvas layout ---------------------------------------------
            double maxR = 1.28 * R;                  // protractor labels
            double padL = 0.15, padB = 0.15, padT = 0.40, padRt = 0.45;
            var legend = LegendRows(s, st);
            double legendFont = s.LabelFontSizePt * 0.9;
            double legendSw = 0.30;                  // symbol sample width
            double legendGap = 0.08;
            double legendTextW = 0.0;
            foreach (var row in legend)
                legendTextW = Math.Max(legendTextW,
                    row.text.Length * legendFont * 0.52 / 72.0);
            double legendW = legend.Count > 0
                ? 0.10 + legendSw + legendGap + legendTextW + 0.10 : 0.0;
            double coreW = padL + maxR + padRt + legendW;
            double coreH = padB + maxR + padT;
            double titleTop = s.ShowPrimaryTitle ? s.TitleFontSizePt / 72.0 * 1.8 + 0.1 : 0.0;
            int secLines = secondaryTitles?.Count ?? 0;
            double titleBottom = (s.ShowSecondaryTitles && secLines > 0)
                ? secLines * s.SecondaryTitleFontSizePt / 72.0 * 1.5 + 0.2 : 0.0;
            m.HalfWidthIn = coreW / 2.0;
            // The canvas holds the title band above and the secondary-title
            // band below; the graphic is shifted at the end so neither is
            // mirrored as blank space on the other side.
            m.HalfHeightIn = coreH / 2.0 + (titleTop + titleBottom) / 2.0;
            double ox = -coreW / 2.0 + padL;
            double oy = -coreH / 2.0 + padB;

            PtIn Pt(double r, double aDeg)
            {
                double rad = aDeg * Math.PI / 180.0;
                return new PtIn(ox + r * Math.Cos(rad), oy + r * Math.Sin(rad));
            }

            // ---- angles: mean/SD/CI are ALREADY degree-space; only the
            // order statistics (min/max/median/percentiles, source units)
            // convert through AngOf. ------------------------------------------
            bool has = st != null && !double.IsNaN(st.MeanDeg);
            double maxU = has ? SlopeStats.MaxValid(st.Unit) : 90.0;
            double AngOf(double unitVal) =>
                has ? SlopeStats.ToDegrees(Math.Min(Math.Max(unitVal, 0.0), maxU), st.Unit) : 0.0;
            double sdLoDeg = has ? Math.Max(st.MeanDeg - st.SdDeg, 0.0) : 0.0;
            double sdHiDeg = has ? Math.Min(st.MeanDeg + st.SdDeg, 90.0) : 0.0;

            // ---- wedges (back to front) ------------------------------------
            if (has && s.ShowRangeWedge && st.Max > st.Min - 1e-12)
                m.Elements.Add(WedgeSector(Pt, 0.70 * R, AngOf(st.Min), AngOf(st.Max),
                    s.RangeFill, s.RangeOutline, 1.0));
            if (has && s.ShowPercentileWedge)
            {
                double lo = st.Quantile(Math.Min(Math.Max(s.PercentileLo, 0), 100) / 100.0);
                double hi = st.Quantile(Math.Min(Math.Max(s.PercentileHi, 0), 100) / 100.0);
                if (hi >= lo)
                    m.Elements.Add(WedgeSector(Pt, 0.79 * R, AngOf(lo), AngOf(hi),
                        s.PercentileFill, s.PercentileOutline, 1.0));
            }
            if (has && s.ShowSdWedge && st.SdDeg > 0)
                m.Elements.Add(WedgeSector(Pt, 0.875 * R, sdLoDeg, sdHiDeg,
                    s.SdFill, s.SdOutline, 1.0));

            // ---- protractor -------------------------------------------------
            if (s.ShowProtractor)
            {
                var arc = new List<PtIn>();
                for (int a = 0; a <= 90; a += 2) arc.Add(Pt(1.06 * R, a));
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Polyline,
                    Points = arc,
                    Stroke = s.ProtractorColor,
                    StrokeWidthPt = 0.75,
                });
                for (int a = 0; a <= 90; a += 5)
                {
                    bool major = a % 10 == 0;
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Line,
                        Points = new List<PtIn>
                        {
                            Pt(1.06 * R, a),
                            Pt((1.06 + (major ? 0.045 : 0.025)) * R, a),
                        },
                        Stroke = s.ProtractorColor,
                        StrokeWidthPt = major ? 1.0 : 0.6,
                    });
                    if (major && s.ShowProtractorLabels)
                    {
                        var el = Label(s, a.ToString(CI) + "°", Pt(1.20 * R, a),
                                       sizeFactor: 0.9);
                        el.Fill = s.ProtractorColor;
                        m.Elements.Add(el);
                    }
                }
            }

            // ---- axes -------------------------------------------------------
            m.Elements.Add(new PolarElement
            {
                Kind = PolarElementKind.Polyline,
                Points = new List<PtIn> { Pt(R, 90), Pt(0, 0), Pt(R, 0) },
                Stroke = s.AxisColor,
                StrokeWidthPt = s.AxisWidthPt,
            });

            if (has)
            {
                // ---- median (dashed) + mean lines --------------------------
                if (s.ShowMedianLine && !double.IsNaN(st.Median))
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Line,
                        Points = new List<PtIn> { Pt(0, 0), Pt(0.95 * R, AngOf(st.Median)) },
                        Stroke = s.MedianColor,
                        StrokeWidthPt = Math.Max(s.MeanWidthPt * 0.75, 0.8),
                        Dash = PolarDash.Dash,
                    });
                if (s.ShowMeanLine)
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Line,
                        Points = new List<PtIn> { Pt(0, 0), Pt(R, st.MeanDeg) },
                        Stroke = s.MeanColor,
                        StrokeWidthPt = s.MeanWidthPt,
                    });

                // ---- 95% CI arc (same idiom as the polar mean-direction arc)
                if (s.ShowCiArc && !double.IsNaN(st.CiHalfDeg95))
                {
                    double aLo = Math.Max(st.MeanDeg - st.CiHalfDeg95, 0.0);
                    double aHi = Math.Min(st.MeanDeg + st.CiHalfDeg95, 90.0);
                    if (aHi - aLo > 1e-6)
                    {
                        double rArc = 1.13 * R;
                        var arc = new List<PtIn>();
                        int steps = Math.Max(6, (int)Math.Ceiling(aHi - aLo));
                        for (int i = 0; i <= steps; i++)
                            arc.Add(Pt(rArc, aLo + (aHi - aLo) * i / steps));
                        m.Elements.Add(new PolarElement
                        {
                            Kind = PolarElementKind.Polyline,
                            Points = arc,
                            Stroke = s.CiColor,
                            StrokeWidthPt = 1.5,
                        });
                        foreach (double a in new[] { aLo, aHi })
                            m.Elements.Add(new PolarElement
                            {
                                Kind = PolarElementKind.Line,
                                Points = new List<PtIn>
                                { Pt(rArc - 0.035 * R, a),
                                  Pt(rArc + 0.035 * R, a) },
                                Stroke = s.CiColor,
                                StrokeWidthPt = 1.5,
                            });
                    }
                }

                // ---- boundary value labels ---------------------------------
                if (s.ShowBoundaryLabels)
                {
                    // Labels are placed in order of importance (mean, median,
                    // SD, percentiles, range); one that would overlap a label
                    // already placed is left off, and every label is kept
                    // clear of the two axes (near 0 and 90 degrees the
                    // boundaries crowd together along them).
                    var placed = new List<double[]>();
                    void Add(double r, double unitVal, double sizeFactor = 1.0)
                    {
                        string text = st.Fmt(unitVal);
                        double em = s.LabelFontSizePt * sizeFactor / 72.0;
                        double hw = text.Length * 0.55 * em / 2.0 + 0.02;
                        double hh = 0.4 * em + 0.01;
                        var p = Pt(r, AngOf(unitVal));
                        double x = Math.Max(p.X, ox + hw + 0.03);
                        double y = Math.Max(p.Y, oy + hh + 0.02);
                        foreach (var q in placed)
                            if (x - hw < q[2] && x + hw > q[0] &&
                                y - hh < q[3] && y + hh > q[1])
                                return;
                        placed.Add(new[] { x - hw, y - hh, x + hw, y + hh });
                        m.Elements.Add(Label(s, text, new PtIn(x, y), sizeFactor));
                    }
                    if (s.ShowMeanLine) Add(1.045 * R, st.Mean, 1.0);
                    if (s.ShowMedianLine && !double.IsNaN(st.Median) &&
                        Math.Abs(SlopeStats.ToDegrees(st.Median, st.Unit) -
                                 SlopeStats.ToDegrees(st.Mean, st.Unit)) > 1.5)
                        Add(0.99 * R, st.Median, 0.9);
                    if (s.ShowSdWedge && st.SdDeg > 0)
                    {
                        Add(0.915 * R, SlopeStats.FromDegrees(sdLoDeg, st.Unit), 0.9);
                        Add(0.915 * R, SlopeStats.FromDegrees(sdHiDeg, st.Unit), 0.9);
                    }
                    if (s.ShowPercentileWedge)
                    {
                        Add(0.83 * R, st.Quantile(s.PercentileLo / 100.0), 0.9);
                        Add(0.83 * R, st.Quantile(s.PercentileHi / 100.0), 0.9);
                    }
                    if (s.ShowRangeWedge && st.Max > st.Min - 1e-12)
                    { Add(0.74 * R, st.Min, 0.9); Add(0.74 * R, st.Max, 0.9); }
                }
            }

            // ---- legend (upper right, to the right of the protractor) -------
            if (legend.Count > 0)
            {
                double lh = legendFont / 72.0 * 1.7;
                double xs = ox + maxR + padRt - 0.25;    // symbol sample left
                double xt = xs + legendSw + legendGap;   // text left
                double y = oy + maxR - lh / 2.0;
                foreach (var row in legend)
                {
                    row.draw(m, xs, xs + legendSw, y);
                    var el = Label(s, row.text, new PtIn(xt, y), 0.9);
                    el.Anchor = TextAnchor.Start;
                    el.Halo = new RgbaColor(0, 0, 0, 0);
                    m.Elements.Add(el);
                    y -= lh;
                }
            }

            // ---- titles -----------------------------------------------------
            if (s.ShowPrimaryTitle && !string.IsNullOrWhiteSpace(s.PrimaryTitle))
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Text,
                    Text = s.PrimaryTitle,
                    Center = new PtIn(0, coreH / 2.0 + 0.05 + s.TitleFontSizePt / 72.0 * 0.7),
                    FontFamily = s.TitleFontFamily,
                    FontSizePt = s.TitleFontSizePt,
                    Bold = s.TitleBold,
                    Italic = s.TitleItalic,
                    Fill = s.TitleColor,
                    Anchor = TextAnchor.Middle,
                });
            if (s.ShowSecondaryTitles && secLines > 0)
            {
                double lh = s.SecondaryTitleFontSizePt / 72.0 * 1.5;
                double y0 = -coreH / 2.0 - 0.08;
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
            m.Shift(0.0, (titleBottom - titleTop) / 2.0);
            return m;
        }

        // ---- legend rows ----------------------------------------------------
        private sealed class LegendRow
        {
            public string text;
            public Action<PolarPlotModel, double, double, double> draw;  // (model, x0, x1, yc)
        }

        /// <summary>One row for each element that is drawn, in the order the
        /// elements sit from front to back.</summary>
        private static List<LegendRow> LegendRows(SlopeGraphicSettings s, SlopeStats st)
        {
            var rows = new List<LegendRow>();
            if (!s.ShowLegend || st == null || double.IsNaN(st.MeanDeg)) return rows;
            double sq = 0.13;

            void LineRow(string text, RgbaColor col, double widthPt, PolarDash dash) =>
                rows.Add(new LegendRow
                {
                    text = text,
                    draw = (m, x0, x1, yc) => m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Line,
                        Points = new List<PtIn> { new PtIn(x0, yc), new PtIn(x1, yc) },
                        Stroke = col,
                        StrokeWidthPt = widthPt,
                        Dash = dash,
                    }),
                });
            void FillRow(string text, RgbaColor fill, RgbaColor outline) =>
                rows.Add(new LegendRow
                {
                    text = text,
                    draw = (m, x0, x1, yc) => m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Polygon,
                        Points = new List<PtIn>
                        {
                            new PtIn(x0, yc - sq / 2), new PtIn(x1, yc - sq / 2),
                            new PtIn(x1, yc + sq / 2), new PtIn(x0, yc + sq / 2),
                        },
                        Fill = fill,
                        Stroke = outline,
                        StrokeWidthPt = 0.75,
                    }),
                });

            if (s.ShowMeanLine)
                LineRow("Mean slope", s.MeanColor, s.MeanWidthPt, PolarDash.Solid);
            if (s.ShowMedianLine && !double.IsNaN(st.Median))
                LineRow("Median slope", s.MedianColor,
                        Math.Max(s.MeanWidthPt * 0.75, 0.8), PolarDash.Dash);
            if (s.ShowCiArc && !double.IsNaN(st.CiHalfDeg95))
                rows.Add(new LegendRow
                {
                    text = "95% CI of the mean",
                    draw = (m, x0, x1, yc) =>
                    {
                        m.Elements.Add(new PolarElement
                        {
                            Kind = PolarElementKind.Line,
                            Points = new List<PtIn> { new PtIn(x0, yc), new PtIn(x1, yc) },
                            Stroke = s.CiColor,
                            StrokeWidthPt = 1.5,
                        });
                        foreach (double x in new[] { x0, x1 })
                            m.Elements.Add(new PolarElement
                            {
                                Kind = PolarElementKind.Line,
                                Points = new List<PtIn>
                                { new PtIn(x, yc - 0.045), new PtIn(x, yc + 0.045) },
                                Stroke = s.CiColor,
                                StrokeWidthPt = 1.5,
                            });
                    },
                });
            if (s.ShowSdWedge && st.SdDeg > 0)
                FillRow("Mean ± 1 SD", s.SdFill, s.SdOutline);
            if (s.ShowPercentileWedge)
                FillRow(Ordinal(s.PercentileLo) + " to " + Ordinal(s.PercentileHi) +
                        " percentiles", s.PercentileFill, s.PercentileOutline);
            if (s.ShowRangeWedge && st.Max > st.Min - 1e-12)
                FillRow("Range (minimum to maximum)", s.RangeFill, s.RangeOutline);
            return rows;
        }

        private static string Ordinal(double p)
        {
            string n = (Math.Abs(p - Math.Round(p)) < 1e-9)
                ? ((int)Math.Round(p)).ToString(CI) : p.ToString("0.#", CI);
            int last = (int)Math.Round(p) % 10, last2 = (int)Math.Round(p) % 100;
            if (Math.Abs(p - Math.Round(p)) > 1e-9) return n + "th";
            string sfx = (last2 >= 11 && last2 <= 13) ? "th"
                : last == 1 ? "st" : last == 2 ? "nd" : last == 3 ? "rd" : "th";
            return n + sfx;
        }

        // ---- helpers --------------------------------------------------------
        private static PolarElement WedgeSector(Func<double, double, PtIn> pt,
            double r, double aLo, double aHi, RgbaColor fill, RgbaColor outline,
            double outlineWidthPt)
        {
            if (aHi < aLo) { double t = aLo; aLo = aHi; aHi = t; }
            var pts = new List<PtIn> { pt(0, 0) };
            int steps = Math.Max(2, (int)Math.Ceiling((aHi - aLo) / 0.75));
            for (int i = 0; i <= steps; i++)
                pts.Add(pt(r, aLo + (aHi - aLo) * i / steps));
            pts.Add(pt(0, 0));
            return new PolarElement
            {
                Kind = PolarElementKind.Polygon,
                Points = pts,
                Fill = fill,
                Stroke = outline,
                StrokeWidthPt = outlineWidthPt,
            };
        }

        private static PolarElement Label(SlopeGraphicSettings s, string text,
            PtIn at, double sizeFactor = 1.0) => new PolarElement
            {
                Kind = PolarElementKind.Text,
                Text = text,
                Center = at,
                FontFamily = s.LabelFontFamily,
                FontSizePt = s.LabelFontSizePt * sizeFactor,
                Bold = s.LabelBold,
                Italic = s.LabelItalic,
                Fill = s.LabelColor,
                Anchor = TextAnchor.Middle,
                Halo = s.LabelHalo,
                HaloWidthPt = s.LabelHaloWidthPt,
            };
    }
}
