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
using System.Globalization;
using System.Windows;
using System.Windows.Media;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Renders a <see cref="PolarPlotModel"/> to a WPF <see cref="DrawingGroup"/>
    /// for the live preview (wrap it in a DrawingImage). Same coordinate mapping
    /// as the SVG renderer: model inches -> device-independent px at 96/in, center
    /// origin -> top-left, +y up -> +y down. Text is converted to geometry so the
    /// preview stays crisp at any zoom.
    /// </summary>
    internal static class PolarPlotWpf
    {
        private const double PxPerIn = 96.0;
        private const double PtToPx = 96.0 / 72.0;
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;

        public static DrawingGroup Render(PolarPlotModel m, double pixelsPerDip = 1.0,
                                          bool showRulers = false, bool rulersCm = false)
        {
            var dg = new DrawingGroup();
            using (var dc = dg.Open())
            {
                double X(double xin) => (xin + m.HalfWidthIn) * PxPerIn;
                double Y(double yin) => (m.HalfHeightIn - yin) * PxPerIn;

                // Transparent background rectangle so the DrawingImage has the
                // full plot bounds (keeps the preview from cropping to content).
                dc.DrawRectangle(Brushes.Transparent, null,
                    new Rect(0, 0, 2 * m.HalfWidthIn * PxPerIn, 2 * m.HalfHeightIn * PxPerIn));

                foreach (var e in m.Elements)
                {
                    switch (e.Kind)
                    {
                        case PolarElementKind.Circle:
                            dc.DrawEllipse(Fill(e), Pen(e), new Point(X(e.Center.X), Y(e.Center.Y)),
                                e.Radius * PxPerIn, e.Radius * PxPerIn);
                            break;
                        case PolarElementKind.Line:
                            if (e.Points.Count >= 2)
                                dc.DrawLine(Pen(e),
                                    new Point(X(e.Points[0].X), Y(e.Points[0].Y)),
                                    new Point(X(e.Points[1].X), Y(e.Points[1].Y)));
                            break;
                        case PolarElementKind.Polyline:
                        case PolarElementKind.Polygon:
                            dc.DrawGeometry(Fill(e), Pen(e), PolyGeom(e, X, Y,
                                e.Kind == PolarElementKind.Polygon));
                            break;
                        case PolarElementKind.Text:
                            DrawText(dc, e, X(e.Center.X), Y(e.Center.Y), pixelsPerDip);
                            break;
                    }
                }

                if (showRulers)
                    DrawRulers(dc, m, pixelsPerDip, rulersCm);
            }
            dg.Freeze();
            return dg;
        }

        /// <summary>
        /// Rulers along the inside of the TOP and LEFT edges of the canvas
        /// (preview only -- never exported), in inches or centimeters to match
        /// the dialog's unit choice. Ticks every 1/4 unit, numbers at whole
        /// units, so the plot-diameter setting is easy to judge.
        /// </summary>
        private static void DrawRulers(DrawingContext dc, PolarPlotModel m, double ppd,
                                       bool cm)
        {
            double inPerUnit = cm ? 1.0 / 2.54 : 1.0;   // model space is inches
            double pxPerUnit = inPerUnit * PxPerIn;
            double wPx = 2 * m.HalfWidthIn * PxPerIn;
            double hPx = 2 * m.HalfHeightIn * PxPerIn;
            var pen = new Pen(new SolidColorBrush(Color.FromRgb(140, 140, 140)), 1.0);
            pen.Freeze();
            var textBrush = new SolidColorBrush(Color.FromRgb(110, 110, 110));
            textBrush.Freeze();
            var tf = new Typeface("Segoe UI");

            // Top ruler.
            dc.DrawLine(pen, new Point(0, 0.5), new Point(wPx, 0.5));
            for (double x = 0; x * pxPerUnit <= wPx + 1e-6; x += 0.25)
            {
                double px = x * pxPerUnit;
                bool whole = Math.Abs(x - Math.Round(x)) < 1e-9;
                bool half = !whole && Math.Abs(x * 2 - Math.Round(x * 2)) < 1e-9;
                double len = whole ? 10 : (half ? 7 : 4);
                dc.DrawLine(pen, new Point(px, 0), new Point(px, len));
                if (whole)
                {
                    string t = ((int)Math.Round(x)).ToString(CI) +
                               (x < 1e-9 ? (cm ? " cm" : " in") : "");
                    var ft = new FormattedText(t,
                        CI, FlowDirection.LeftToRight, tf, 9, textBrush, ppd);
                    dc.DrawText(ft, new Point(px + 2, 10));
                }
            }
            // Left ruler.
            dc.DrawLine(pen, new Point(0.5, 0), new Point(0.5, hPx));
            for (double y = 0; y * pxPerUnit <= hPx + 1e-6; y += 0.25)
            {
                double py = y * pxPerUnit;
                bool whole = Math.Abs(y - Math.Round(y)) < 1e-9;
                bool half = !whole && Math.Abs(y * 2 - Math.Round(y * 2)) < 1e-9;
                double len = whole ? 10 : (half ? 7 : 4);
                dc.DrawLine(pen, new Point(0, py), new Point(len, py));
                if (whole && y > 0)
                {
                    var ft = new FormattedText(((int)Math.Round(y)).ToString(CI),
                        CI, FlowDirection.LeftToRight, tf, 9, textBrush, ppd);
                    dc.DrawText(ft, new Point(12, py + 1));
                }
            }
        }

        private static Geometry PolyGeom(PolarElement e, Func<double, double> X,
            Func<double, double> Y, bool closed)
        {
            var g = new StreamGeometry { FillRule = FillRule.EvenOdd };
            using (var ctx = g.Open())
            {
                bool filled = closed && Fill(e) != null;
                var rings = e.Rings ?? new System.Collections.Generic.List<
                    System.Collections.Generic.List<PtIn>> { e.Points };
                foreach (var ring in rings)
                {
                    if (ring == null || ring.Count == 0) continue;
                    ctx.BeginFigure(new Point(X(ring[0].X), Y(ring[0].Y)),
                        filled, closed);
                    for (int i = 1; i < ring.Count; i++)
                        ctx.LineTo(new Point(X(ring[i].X), Y(ring[i].Y)), true, false);
                }
            }
            g.Freeze();
            return g;
        }

        private static void DrawText(DrawingContext dc, PolarElement e,
            double px, double py, double ppd)
        {
            if (string.IsNullOrEmpty(e.Text)) return;
            var tf = new Typeface(new FontFamily(e.FontFamily ?? "Segoe UI"),
                e.Italic ? FontStyles.Italic : FontStyles.Normal,
                e.Bold ? FontWeights.Bold : FontWeights.Normal,
                FontStretches.Normal);
            var ft = new FormattedText(e.Text, CI, FlowDirection.LeftToRight, tf,
                e.FontSizePt * PtToPx, Brush(e.Fill), ppd);
            double x = px, y = py - ft.Height / 2.0;    // vertical center
            if (e.Anchor == TextAnchor.Middle) x -= ft.Width / 2.0;
            else if (e.Anchor == TextAnchor.End) x -= ft.Width;
            var geo = ft.BuildGeometry(new Point(x, y));
            if (Math.Abs(e.RotationDeg) > 1e-6)
                geo.Transform = new RotateTransform(e.RotationDeg, px, py);
            if (!e.Halo.IsTransparent && e.HaloWidthPt > 0)
            {
                var haloPen = new Pen(Brush(e.Halo), e.HaloWidthPt * PtToPx * 2.0)
                { LineJoin = PenLineJoin.Round };
                haloPen.Freeze();
                dc.DrawGeometry(null, haloPen, geo);
            }
            dc.DrawGeometry(Brush(e.Fill), null, geo);
        }

        /// <summary>Writes the model as a PNG image at <paramref name="dpi"/>
        /// on a white background, sized exactly as the model (no rulers).
        /// Shared by every chart window's Save Graphic.</summary>
        public static void SavePng(PolarPlotModel m, string path, double dpi = 300.0)
        {
            // The drawing is in device-independent units (96 per inch); a
            // bitmap made at `dpi` scales it by dpi/96 itself and records the
            // resolution, so the PNG opens at the plot's true size in Word.
            var dg = Render(m);
            int pxW = (int)Math.Ceiling(m.HalfWidthIn * 2 * dpi);
            int pxH = (int)Math.Ceiling(m.HalfHeightIn * 2 * dpi);
            var dv = new DrawingVisual();
            using (var dc = dv.RenderOpen())
            {
                // The white background covers the whole (ceiled) bitmap, or
                // the last row and column of pixels stay transparent.
                dc.DrawRectangle(Brushes.White, null,
                    new Rect(0, 0, pxW * PxPerIn / dpi + 1, pxH * PxPerIn / dpi + 1));
                dc.DrawDrawing(dg);
            }
            var rtb = new System.Windows.Media.Imaging.RenderTargetBitmap(
                pxW, pxH, dpi, dpi, PixelFormats.Pbgra32);
            rtb.Render(dv);
            var enc = new System.Windows.Media.Imaging.PngBitmapEncoder();
            enc.Frames.Add(System.Windows.Media.Imaging.BitmapFrame.Create(rtb));
            using (var fs = System.IO.File.Create(path)) enc.Save(fs);
        }

        /// <summary>The Save Graphic dialog for a chart model: PNG (300 dpi,
        /// white background) or SVG, chosen by the file type. Returns the
        /// saved path, or null when canceled.</summary>
        public static string SaveGraphicDialog(PolarPlotModel m, string fileBase,
                                               string title)
        {
            var dlg = new Microsoft.Win32.SaveFileDialog
            {
                Title = title,
                Filter = "PNG image, 300 dpi (*.png)|*.png|SVG vector (*.svg)|*.svg",
                FileName = fileBase + ".png",
                InitialDirectory = ImageSaveFolder.Initial(),
                DefaultExt = ".png",
                AddExtension = true,
            };
            if (dlg.ShowDialog() != true) return null;
            string path = dlg.FileName;
            string ext = System.IO.Path.GetExtension(path);
            if (string.IsNullOrEmpty(ext))
            {
                ext = dlg.FilterIndex == 2 ? ".svg" : ".png";   // typed without one
                path += ext;
            }
            ImageSaveFolder.Remember(path);
            if (ext.Equals(".svg", StringComparison.OrdinalIgnoreCase))
                System.IO.File.WriteAllText(path, PolarPlotSvg.Render(m));
            else
                SavePng(m, path);
            return path;
        }

        private static Brush Fill(PolarElement e) =>
            e.Fill.IsTransparent ? null : Brush(e.Fill);

        private static Pen Pen(PolarElement e)
        {
            if (e.Stroke.IsTransparent || e.StrokeWidthPt <= 0) return null;
            var pen = new Pen(Brush(e.Stroke), e.StrokeWidthPt * PtToPx)
            {
                LineJoin = PenLineJoin.Round,
                StartLineCap = PenLineCap.Round,
                EndLineCap = PenLineCap.Round,
            };
            switch (e.Dash)
            {
                case PolarDash.Dot: pen.DashStyle = new DashStyle(new double[] { 1, 2 }, 0); break;
                case PolarDash.Dash: pen.DashStyle = new DashStyle(new double[] { 4, 3 }, 0); break;
                case PolarDash.DashDot: pen.DashStyle = new DashStyle(new double[] { 4, 2, 1, 2 }, 0); break;
            }
            pen.Freeze();
            return pen;
        }

        private static Brush Brush(RgbaColor c)
        {
            var b = new SolidColorBrush(Color.FromArgb(c.A, c.R, c.G, c.B));
            b.Freeze();
            return b;
        }
    }
}
