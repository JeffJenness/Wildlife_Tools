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
            double coreW = padL + maxR + padRt;
            double coreH = padB + maxR + padT;
            double titleTop = s.ShowPrimaryTitle ? s.TitleFontSizePt / 72.0 * 1.8 + 0.1 : 0.0;
            int secLines = secondaryTitles?.Count ?? 0;
            double titleBottom = (s.ShowSecondaryTitles && secLines > 0)
                ? secLines * s.SecondaryTitleFontSizePt / 72.0 * 1.5 + 0.2 : 0.0;
            m.HalfWidthIn = coreW / 2.0;
            m.HalfHeightIn = coreH / 2.0 + Math.Max(titleTop, titleBottom);
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
                    void Add(double r, double unitVal, double sizeFactor = 1.0)
                    {
                        var el = Label(s, st.Fmt(unitVal), Pt(r, AngOf(unitVal)),
                                       sizeFactor);
                        m.Elements.Add(el);
                    }
                    if (s.ShowRangeWedge && st.Max > st.Min - 1e-12)
                    { Add(0.74 * R, st.Min, 0.9); Add(0.74 * R, st.Max, 0.9); }
                    if (s.ShowPercentileWedge)
                    {
                        Add(0.83 * R, st.Quantile(s.PercentileLo / 100.0), 0.9);
                        Add(0.83 * R, st.Quantile(s.PercentileHi / 100.0), 0.9);
                    }
                    if (s.ShowSdWedge && st.SdDeg > 0)
                    {
                        Add(0.915 * R, SlopeStats.FromDegrees(sdLoDeg, st.Unit), 0.9);
                        Add(0.915 * R, SlopeStats.FromDegrees(sdHiDeg, st.Unit), 0.9);
                    }
                    if (s.ShowMedianLine && !double.IsNaN(st.Median) &&
                        Math.Abs(SlopeStats.ToDegrees(st.Median, st.Unit) -
                                 SlopeStats.ToDegrees(st.Mean, st.Unit)) > 1.5)
                        Add(0.99 * R, st.Median, 0.9);
                    if (s.ShowMeanLine) Add(1.045 * R, st.Mean, 1.0);
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
            return m;
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
