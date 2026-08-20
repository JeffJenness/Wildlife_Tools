using System;
using System.Collections.Generic;
using System.Linq;
using WildlifeTools.PolarPlots;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Renders the cross-tabulation table as a PolarPlotModel (the suite's
    /// shared primitive list): variable names on the top and left, header
    /// row/column, data cells, Other and Sum lines, grid rules. The same
    /// model drives the live preview, PNG/SVG export, Copy Image and the
    /// editable layout graphic.
    /// </summary>
    internal sealed class CrosstabStyle
    {
        public FontSpec HeaderFont = new FontSpec { SizePt = 9.5, Bold = true };
        public FontSpec CellFont = new FontSpec { SizePt = 9.5 };
        public FontSpec VariableFont = new FontSpec { SizePt = 11, Bold = true };
    }

    internal static class CrosstabBuilder
    {
        private static readonly RgbaColor Ink = new RgbaColor(40, 40, 40, 255);
        private static readonly RgbaColor Rule = new RgbaColor(120, 120, 120, 255);
        private static readonly RgbaColor HeadFill = new RgbaColor(226, 226, 226, 255);
        private static readonly RgbaColor SumFill = new RgbaColor(240, 240, 240, 255);
        private static readonly RgbaColor CellFill = new RgbaColor(255, 255, 255, 255);

        /// <summary>Build the table. ``grid`` comes from
        /// CrosstabReport.Grid; ``measureIn`` (text, sizePt, family) ->
        /// inches, null = estimate (headless tests).</summary>
        public static PolarPlotModel Build(List<string[]> grid,
            string colVariable, string rowVariable, CrosstabStyle st,
            Func<string, double, string, double> measureIn)
        {
            double Measure(string t, FontSpec f) =>
                measureIn != null ? measureIn(t ?? "", f.SizePt, f.Family)
                : 0.075 * (t?.Length ?? 0) * f.SizePt / 9.0;

            int nRows = grid.Count;
            int nCols = grid[0].Length;
            const double padX = 0.10;
            double rowH = Math.Max(0.22, st.CellFont.SizePt / 42.0);

            // column widths from measured content
            var colW = new double[nCols];
            for (int c = 0; c < nCols; c++)
            {
                double w = 0.3;
                for (int r = 0; r < nRows; r++)
                {
                    var f = r == 0 || c == 0 ? st.HeaderFont : st.CellFont;
                    w = Math.Max(w, Measure(grid[r][c], f) + 2 * padX);
                }
                colW[c] = w;
            }

            double left = 0.34;                    // rotated row-variable
            double top = 0.34;                     // column-variable line
            double tableW = colW.Sum();
            double tableH = nRows * rowH;
            // the rotated row-variable label runs VERTICALLY: when it is
            // longer than the table it must not overflow the canvas (the
            // preview forgave it, the exported bitmap clipped it)
            double rowLblLen = string.IsNullOrEmpty(rowVariable) ? 0
                : Measure(rowVariable, st.VariableFont) + 0.10;
            // centered on the WHOLE table (headers included), per Jeff
            double rowCenter0 = top + tableH / 2;
            double shiftDown = Math.Max(0,
                rowLblLen / 2 - (rowCenter0 - 0.04));
            top += shiftDown;
            double rowCenter = rowCenter0 + shiftDown;
            // and the column-variable label must not overflow the width
            double colLblLen = string.IsNullOrEmpty(colVariable) ? 0
                : Measure(colVariable, st.VariableFont) + 0.10;
            double colCenter = left + tableW / 2;
            double shiftRight = Math.Max(0,
                colLblLen / 2 - (colCenter - 0.04));
            left += shiftRight;
            colCenter += shiftRight;
            double W = Math.Max(left + tableW,
                                colCenter + colLblLen / 2) + 0.12;
            double H = Math.Max(top + tableH,
                                rowCenter + rowLblLen / 2) + 0.12;
            var m = new PolarPlotModel
            { HalfWidthIn = W / 2, HalfHeightIn = H / 2 };
            PtIn P(double x, double y) => new PtIn(x - W / 2, H / 2 - y);

            // ---- cell backgrounds + text --------------------------------
            double y = top;
            for (int r = 0; r < nRows; r++)
            {
                double x = left;
                bool sumRow = grid[r][0] == "Sum";
                for (int c = 0; c < nCols; c++)
                {
                    bool head = r == 0 || c == 0;
                    bool sumCol = grid[0][c] == "Sum";
                    var fill = head ? HeadFill
                        : sumRow || sumCol ? SumFill : CellFill;
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Polygon,
                        Points = new List<PtIn>
                        {
                            P(x, y), P(x + colW[c], y),
                            P(x + colW[c], y + rowH), P(x, y + rowH),
                        },
                        Fill = fill,
                        Stroke = Rule,
                        StrokeWidthPt = head ? 1.0 : 0.6,
                    });
                    var font = head || sumRow || sumCol
                        ? st.HeaderFont : st.CellFont;
                    bool leftAlign = c == 0 && r > 0;
                    m.Elements.Add(new PolarElement
                    {
                        Kind = PolarElementKind.Text,
                        Center = leftAlign
                            ? P(x + padX, y + rowH / 2 - 0.005)
                            : P(x + colW[c] - padX, y + rowH / 2 - 0.005),
                        Text = grid[r][c],
                        FontFamily = string.IsNullOrEmpty(font.Family)
                            ? "Segoe UI" : font.Family,
                        FontSizePt = font.SizePt,
                        Bold = font.Bold,
                        Italic = font.Italic,
                        Fill = Ink,
                        Anchor = leftAlign ? TextAnchor.Start
                                           : TextAnchor.End,
                    });
                    x += colW[c];
                }
                y += rowH;
            }

            // ---- variable names -----------------------------------------
            if (!string.IsNullOrEmpty(colVariable))
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Text,
                    Center = P(colCenter, 0.16),
                    Text = colVariable,
                    FontFamily = string.IsNullOrEmpty(st.VariableFont.Family)
                        ? "Segoe UI" : st.VariableFont.Family,
                    FontSizePt = st.VariableFont.SizePt,
                    Bold = st.VariableFont.Bold,
                    Italic = st.VariableFont.Italic,
                    Fill = Ink,
                    Anchor = TextAnchor.Middle,
                });
            if (!string.IsNullOrEmpty(rowVariable))
                m.Elements.Add(new PolarElement
                {
                    Kind = PolarElementKind.Text,
                    Center = P(0.16, rowCenter),
                    Text = rowVariable,
                    FontFamily = string.IsNullOrEmpty(st.VariableFont.Family)
                        ? "Segoe UI" : st.VariableFont.Family,
                    FontSizePt = st.VariableFont.SizePt,
                    Bold = st.VariableFont.Bold,
                    Italic = st.VariableFont.Italic,
                    Fill = Ink,
                    Anchor = TextAnchor.Middle,
                    RotationDeg = -90,
                });
            return m;
        }
    }
}
