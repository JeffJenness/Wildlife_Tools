using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using WildlifeTools.PolarPlots;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Builds the histogram chart as a PolarPlotModel (the suite's shared
    /// resolution-independent primitive list): grouped side-by-side bars per
    /// bin, optional weighted-KDE curves, axes with nice 1-2-5 ticks,
    /// reference lines, title, axis labels and legend. Pure geometry -- no
    /// ArcGIS or WPF types -- so the live preview, SVG export and layout
    /// graphic all render the identical model.
    /// </summary>
    internal static class HistogramBuilder
    {
        public static PolarPlotModel Build(IReadOnlyList<HistDataset> datasets,
                                           HistogramOptions o) =>
            Build(datasets, o, null);

        /// <summary>``measureIn``: optional (text, sizePt, family) -> width
        /// in inches, supplied by the window from real WPF text metrics so
        /// the legend centers exactly; null falls back to an estimate
        /// (headless tests).</summary>
        public static PolarPlotModel Build(IReadOnlyList<HistDataset> datasets,
            HistogramOptions o,
            Func<string, double, string, double> measureIn)
        {
            double MeasureText(string t, FontSpec f) =>
                measureIn != null ? measureIn(t ?? "", f.SizePt, f.Family)
                : 0.075 * (t?.Length ?? 0) * f.SizePt / 9.0;
            double W = Math.Max(o.WidthIn, 2.5), H = Math.Max(o.HeightIn, 2.0);
            var m = new PolarPlotModel { HalfWidthIn = W / 2, HalfHeightIn = H / 2 };
            // top-left coordinate helpers (inches) -> model space
            PtIn P(double x, double y) => new PtIn(x - W / 2, H / 2 - y);

            var visible = datasets.Where(d => d.Stats.SumWeights > 0).ToList();

            // ---- categorical mode --------------------------------------
            // One slot per CLASS instead of per bin; classes matched by
            // name across datasets; no density curves (there is no order
            // along the axis to smooth over). The window refuses to mix
            // categorical and continuous datasets, so any = all here.
            bool categorical = visible.Any(d => d.IsCategorical);
            var cats = categorical
                ? HistogramMath.UnionCategories(visible) : new List<string>();
            double catLabelW = 0;              // widest class name, inches
            bool tiltLabels = false;
            if (categorical)
                foreach (var c in cats)
                    catLabelW = Math.Max(catLabelW, MeasureText(c, o.TickFont));

            // ---- margins ------------------------------------------------
            double top = o.ShowTitle && o.Title.Length > 0 ? 0.42 : 0.12;
            double yTickW = 0.60;
            double left = (o.ShowYLabel ? 0.26 : 0.04) + yTickW;
            double right = 0.15;
            int legendRows = 0;
            double legendRowH = Math.Max(0.24, o.LegendFont.SizePt / 36.0);
            if (o.ShowLegend && visible.Count > 0)
                legendRows = LegendRows(visible, W - 0.4, o.LegendFont,
                                        MeasureText, out _);
            double xTickH = 0.24;                      // x tick labels
            if (categorical && cats.Count > 0)
            {
                // class names sit flat when they fit their slot; otherwise
                // they tilt 45 degrees, and the margin grows to hold them
                double slotW = Math.Max(W - left - right, 0.5) / cats.Count;
                tiltLabels = catLabelW > slotW * 0.92;
                if (tiltLabels)
                    xTickH = 0.12 + Math.Min(catLabelW, 2.4) * 0.7071 + 0.08;
            }
            double bottom = xTickH
                + (o.ShowXLabel ? 0.30 : 0.04)
                + legendRows * legendRowH + (legendRows > 0 ? 0.10 : 0);
            double plotX = left, plotY = top;
            double plotW = Math.Max(W - left - right, 0.5);
            double plotH = Math.Max(H - top - bottom, 0.5);

            double axMin = o.AxisMin, axMax = o.AxisMax;
            if (axMax <= axMin) { axMin = 0; axMax = 1; }
            int bins = categorical ? Math.Max(cats.Count, 1) : Math.Max(o.Bins, 1);
            if (categorical) { axMin = 0; axMax = bins; }
            double binWidth = (axMax - axMin) / bins;

            // ---- per-dataset bin values in DISPLAY units ----------------
            // Cells with MIXED cell sizes: convert the other rasters'
            // counts to equivalent cells of the FIRST dataset, so relative
            // bar heights stay area-true.
            bool normCells = o.Unit == YUnit.Native && !o.YPercent
                && visible.Count > 1
                && visible.All(x => x.Basis == WeightBasis.Cells
                                    && x.CellAreaM2 > 0)
                && visible.Select(x => x.CellAreaM2).Distinct().Count() > 1;
            double refCellArea = normCells ? visible[0].CellAreaM2 : 0;
            double CellFactor(HistDataset x) =>
                normCells ? x.CellAreaM2 / refCellArea : 1.0;

            var binVals = new List<double[]>();
            var totalsUnit = new List<double>();
            foreach (var d in visible)
            {
                var tot = categorical
                    ? HistogramMath.CategoryTotals(d, cats)
                    : HistogramMath.BinTotals(d, bins, axMin, axMax, out _);
                double totalUnit = d.TotalInUnit(o.Unit, o.CustomMultiplier);
                if (double.IsNaN(totalUnit)) totalUnit = d.Stats.SumWeights;
                totalUnit *= CellFactor(d);
                totalsUnit.Add(totalUnit);
                var vals = new double[bins];
                for (int b = 0; b < bins; b++)
                {
                    if (o.YPercent)
                        vals[b] = d.Stats.SumWeights > 0
                            ? tot[b] / d.Stats.SumWeights * 100.0 : 0;
                    else
                    {
                        double u = HistogramMath.ToUnit(tot[b], d, o.Unit,
                                                        o.CustomMultiplier);
                        vals[b] = (double.IsNaN(u) ? tot[b] : u)
                                  * CellFactor(d);
                    }
                }
                binVals.Add(vals);
            }

            // ---- KDE curves (display-unit scaled) -----------------------
            const int GridN = 257;
            var curves = new List<double[]>();
            for (int i = 0; i < visible.Count; i++)
            {
                var d = visible[i];
                if (categorical || !(d.ShowKde || o.CurvesOnly))
                { curves.Add(null); continue; }
                var f = HistogramMath.Kde(d, axMin, axMax, GridN,
                                          o.KdeBandwidthMult, out _);
                double scale = o.YPercent
                    ? binWidth * 100.0
                    : binWidth * totalsUnit[i];
                for (int g = 0; g < GridN; g++) f[g] *= scale;
                curves.Add(f);
            }

            // ---- y range ------------------------------------------------
            double yMax = 0;
            for (int i = 0; i < binVals.Count; i++)
            {
                if ((!o.CurvesOnly || categorical) && visible[i].ShowBars)
                    foreach (var v in binVals[i]) yMax = Math.Max(yMax, v);
                if (curves[i] != null)
                    foreach (var v in curves[i]) yMax = Math.Max(yMax, v);
            }
            if (yMax <= 0) yMax = 1;
            int targetTicks = Math.Max(2, (int)(plotH / 0.42));
            double yStep = HistogramMath.NiceStep(yMax, targetTicks);
            double yTop = Math.Ceiling(yMax / yStep) * yStep;
            if (yTop <= 0) yTop = yStep;

            double XPos(double v) => plotX + (v - axMin) / (axMax - axMin) * plotW;
            double YPos(double val) => plotY + plotH - val / yTop * plotH;

            var axis = new RgbaColor(60, 60, 60, 255);

            // ---- reference lines + y ticks ------------------------------
            for (double yv = 0; yv <= yTop + yStep * 0.001; yv += yStep)
            {
                double y = YPos(yv);
                if (o.ShowRefLines && yv > 0)
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Line,
                        Points = new List<PtIn> { P(plotX, y), P(plotX + plotW, y) },
                        Stroke = o.RefLineColor,
                        StrokeWidthPt = 0.7,
                    });
                string lbl = o.YPercent
                    ? HistogramMath.AxisNumber(yv, yStep) + "%"
                    : HistogramMath.AxisNumber(yv, yStep);
                m.Elements.Add(Text(P(plotX - 0.07, y), lbl, o.TickFont,
                                    axis, TextAnchor.End));
            }

            // ---- bars ---------------------------------------------------
            double slot = plotW / bins;
            double gap = Math.Min(Math.Max(o.GapFraction, 0), 0.9);
            double groupW = slot * (1 - gap);
            int nDs = Math.Max(visible.Count, 1);
            double barW = groupW / nDs;
            if (!o.CurvesOnly || categorical)
                for (int b = 0; b < bins; b++)
                {
                    double gx = plotX + b * slot + slot * gap / 2;
                    for (int i = 0; i < visible.Count; i++)
                    {
                        if (!visible[i].ShowBars) continue;
                        double v = binVals[i][b];
                        if (v <= 0) continue;
                        double x0 = gx + i * barW;
                        double y0 = YPos(v), y1 = plotY + plotH;
                        m.Elements.Add(new PolarElement
                        {
                            Kind = PolarElementKind.Polygon,
                            Points = new List<PtIn>
                            {
                                P(x0, y0), P(x0 + barW, y0),
                                P(x0 + barW, y1), P(x0, y1),
                            },
                            Fill = visible[i].Fill,
                            Stroke = visible[i].Outline,
                            StrokeWidthPt = visible[i].OutlineWidthPt,
                        });
                    }
                }

            // ---- KDE curves ---------------------------------------------
            for (int i = 0; i < visible.Count; i++)
            {
                if (curves[i] == null) continue;
                var pts = new List<PtIn>(GridN);
                for (int g = 0; g < GridN; g++)
                {
                    double xv = axMin + (axMax - axMin) * g / (GridN - 1);
                    pts.Add(P(XPos(xv), YPos(Math.Min(curves[i][g], yTop))));
                }
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Polyline,
                    Points = pts,
                    Stroke = o.CurvesOnly ? OpaqueFill(visible[i]) : visible[i].Outline,
                    StrokeWidthPt = Math.Max(visible[i].OutlineWidthPt, 1.6),
                });
            }

            // ---- axes ---------------------------------------------------
            m.Elements.Add(new PolarElement
            {
                Kind = PolarElementKind.Polyline,
                Points = new List<PtIn>
                {
                    P(plotX, plotY), P(plotX, plotY + plotH),
                    P(plotX + plotW, plotY + plotH),
                },
                Stroke = axis,
                StrokeWidthPt = 1.1,
            });

            // ---- x ticks: class names under each slot (categorical) ----
            if (categorical)
                for (int b = 0; b < bins; b++)
                {
                    double xc = plotX + (b + 0.5) * slot;
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Line,
                        Points = new List<PtIn>
                        { P(xc, plotY + plotH), P(xc, plotY + plotH + 0.05) },
                        Stroke = axis,
                        StrokeWidthPt = 0.9,
                    });
                    if (tiltLabels)
                    {
                        // anchored at its END just under the tick, rotated
                        // 45 degrees counter-clockwise so it reads up
                        // toward the axis
                        var t = Text(P(xc + 0.04, plotY + plotH + 0.10), cats[b],
                                     o.TickFont, axis, TextAnchor.End);
                        t.RotationDeg = -45;
                        m.Elements.Add(t);
                    }
                    else
                        m.Elements.Add(Text(P(xc, plotY + plotH + 0.16), cats[b],
                                            o.TickFont, axis, TextAnchor.Middle));
                }
            // ---- x ticks: bin edges, thinned to fit ---------------------
            int every = Math.Max(1, (int)Math.Ceiling(0.55 / slot));
            for (int e = 0; e <= bins && !categorical; e += every)
            {
                double xv = axMin + e * binWidth;
                double x = XPos(xv);
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Line,
                    Points = new List<PtIn>
                    { P(x, plotY + plotH), P(x, plotY + plotH + 0.05) },
                    Stroke = axis,
                    StrokeWidthPt = 0.9,
                });
                m.Elements.Add(Text(P(x, plotY + plotH + 0.16),
                    HistogramMath.AxisNumber(xv, binWidth), o.TickFont,
                    axis, TextAnchor.Middle));
            }

            // ---- title + axis labels ------------------------------------
            if (o.ShowTitle && o.Title.Length > 0)
                m.Elements.Add(Text(P(plotX + plotW / 2, 0.20), o.Title,
                                    o.TitleFont, axis, TextAnchor.Middle));
            if (o.ShowXLabel && o.XLabel.Length > 0)
                m.Elements.Add(Text(P(plotX + plotW / 2,
                                      plotY + plotH + xTickH + 0.18),
                                    o.XLabel, o.LabelFont, axis,
                                    TextAnchor.Middle));
            if (o.ShowYLabel)
            {
                string yl = o.YLabel;
                if (string.IsNullOrEmpty(yl))
                {
                    yl = o.YPercent ? "Percent"
                        : HistogramMath.UnitLabel(o.Unit, visible);
                    if (normCells)
                        yl = string.Format(CultureInfo.InvariantCulture,
                            "Cells ({0:0.#} m equivalent)",
                            Math.Sqrt(refCellArea));
                }
                if (yl.Length > 0)
                {
                    var e2 = Text(P(0.16, plotY + plotH / 2), yl,
                                  o.LabelFont, axis, TextAnchor.Middle);
                    e2.RotationDeg = -90;
                    m.Elements.Add(e2);
                }
            }

            // ---- legend -------------------------------------------------
            if (o.ShowLegend && visible.Count > 0)
            {
                LegendRows(visible, W - 0.4, o.LegendFont, MeasureText,
                           out var rows);
                double ly = H - legendRows * legendRowH - 0.02;
                foreach (var row in rows)
                {
                    double rowWidth = row.Sum(t => t.width);
                    // centered under the PLOT (the X axis), not the canvas
                    double lx = plotX + (plotW - rowWidth) / 2;
                    foreach (var (idx, width) in row)
                    {
                        var d = visible[idx];
                        if (o.CurvesOnly && !categorical)
                            m.Elements.Add(new PolarElement
                            {
                                Kind = PolarElementKind.Line,
                                Points = new List<PtIn>
                                { P(lx, ly + 0.08), P(lx + 0.24, ly + 0.08) },
                                Stroke = OpaqueFill(d),
                                StrokeWidthPt = 2.0,
                            });
                        else
                            m.Elements.Add(new PolarElement
                            {
                                Kind = PolarElementKind.Polygon,
                                Points = new List<PtIn>
                                {
                                    P(lx, ly), P(lx + 0.24, ly),
                                    P(lx + 0.24, ly + 0.15), P(lx, ly + 0.15),
                                },
                                Fill = d.Fill,
                                Stroke = d.Outline,
                                StrokeWidthPt = Math.Min(d.OutlineWidthPt, 1.2),
                            });
                        m.Elements.Add(Text(P(lx + 0.30, ly + 0.075),
                                            d.Name, o.LegendFont, axis,
                                            TextAnchor.Start));
                        lx += width;
                    }
                    ly += legendRowH;
                }
            }
            return m;
        }

        private static RgbaColor OpaqueFill(HistDataset d) =>
            new RgbaColor(d.Fill.R, d.Fill.G, d.Fill.B, 255);

        private static PolarElement Text(PtIn at, string text, FontSpec f,
            RgbaColor color, TextAnchor anchor) => new PolarElement
        {
            Kind = PolarElementKind.Text,
            Center = at,
            Text = text,
            FontFamily = string.IsNullOrEmpty(f.Family) ? "Segoe UI"
                                                        : f.Family,
            FontSizePt = f.SizePt,
            Bold = f.Bold,
            Italic = f.Italic,
            Fill = color,
            Anchor = anchor,
        };

        /// <summary>Pack legend entries into rows fitting maxW; returns the
        /// row count and, via out, the (dataset index, entry width) rows.
        /// Entry width = swatch + measured text + trailing gap, so the
        /// centering is exact.</summary>
        private static int LegendRows(IReadOnlyList<HistDataset> ds, double maxW,
            FontSpec f, Func<string, FontSpec, double> measure,
            out List<List<(int idx, double width)>> rows)
        {
            rows = new List<List<(int, double)>>();
            var row = new List<(int, double)>();
            double x = 0;
            for (int i = 0; i < ds.Count; i++)
            {
                double w = 0.30 + measure(ds[i].Name ?? "", f) + 0.22;
                if (x + w > maxW && row.Count > 0)
                {
                    rows.Add(row);
                    row = new List<(int, double)>();
                    x = 0;
                }
                row.Add((i, w));
                x += w;
            }
            if (row.Count > 0) rows.Add(row);
            return rows.Count;
        }
    }
}
