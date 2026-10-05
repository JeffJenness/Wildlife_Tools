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

using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.Collections.Generic;

namespace WildlifeTools
{
    /// <summary>
    /// The drop-shadow settings of a Graphic Background, modeled on Photoshop's
    /// Drop Shadow "Structure" panel: color, opacity, light angle, distance,
    /// spread and size, plus the number of layers the soft edge is built from.
    /// Distance and Size arrive ALREADY in container units (page units on a
    /// layout, map units on a map).
    /// </summary>
    internal sealed class ShadowOptions
    {
        public bool Enabled { get; set; }
        public double R { get; set; }
        public double G { get; set; }
        public double B { get; set; }
        /// <summary>Darkest opacity of the shadow, 0..100 percent.</summary>
        public double OpacityPct { get; set; } = 60;
        /// <summary>Direction the light comes FROM, degrees counterclockwise from
        /// east (Photoshop's convention). The shadow falls on the opposite side.</summary>
        public double AngleDeg { get; set; } = 135;
        /// <summary>How far the shadow is shifted away from the light.</summary>
        public double Distance { get; set; }
        /// <summary>0..100: how much of the soft edge is made solid. 0 = the
        /// softest edge; 100 = a hard-edged shadow grown by Size.</summary>
        public double SpreadPct { get; set; }
        /// <summary>Width of the soft edge on each side of the shadow's outline.</summary>
        public double Size { get; set; }
        public int Layers { get; set; } = 10;
    }

    /// <summary>
    /// Builds a soft drop shadow out of ordinary polygon graphics (Jeff,
    /// 2026-10-04: "we can hardly do worse than the current Esri offering").
    ///
    /// A blurred shadow is a shape whose opacity ramps from nothing at its outer
    /// edge to full strength inside. Vector graphics cannot blur, but stacked
    /// translucent polygons ADD UP: where k layers overlap, the light let through
    /// is the product of what each lets through. So the shadow is N nested copies
    /// of the background's shape, all shifted by the same offset, each buffered a
    /// little less than the one beneath it, the outermost reaching Size beyond
    /// the shifted outline and the innermost Size inside it. The ring between
    /// two successive outlines is covered by one more layer than the ring outside
    /// it, so the opacity climbs in N steps.
    ///
    /// Each layer's own opacity is solved so the running total follows a smooth
    /// S-curve (smoothstep, close to the profile of a blurred edge) and reaches
    /// exactly the requested opacity where every layer overlaps:
    ///     cumulative C(k) = Opacity * smooth(k / N),
    ///     layer alpha a(k) = 1 - (1 - C(k)) / (1 - C(k-1)).
    ///
    /// Spread narrows the soft band from the inside (the solid core grows) until
    /// at 100% every layer coincides and the shadow is hard-edged.
    ///
    /// The background's own shape is cut out of every layer ("layer knocks out
    /// drop shadow"), so a partly transparent background is not darkened by a
    /// shadow lying underneath it.
    /// </summary>
    internal static class BackgroundShadow
    {
        public const int MaxLayers = 60;

        /// <summary>One layer of the stack: how far to buffer the shifted shape
        /// (negative = inward) and the layer's own opacity, 0..1.</summary>
        internal readonly record struct Layer(double Buffer, double Alpha);

        /// <summary>The (buffer, alpha) plan, outermost layer first. Pure math.</summary>
        internal static List<Layer> Plan(ShadowOptions opt)
        {
            var plan = new List<Layer>();
            double opacity = Math.Clamp(opt.OpacityPct, 0.0, 100.0) / 100.0;
            if (opacity <= 0)
                return plan;
            opacity = Math.Min(opacity, 0.995);            // 1.0 would divide by zero below
            double size = Math.Max(0.0, opt.Size);
            double spread = Math.Clamp(opt.SpreadPct, 0.0, 100.0) / 100.0;
            double rOut = size;                            // outer limit of the soft band
            double rIn = size * (2.0 * spread - 1.0);      // inner limit: -size .. +size
            int n = Math.Clamp(opt.Layers, 1, MaxLayers);
            if (n == 1 || rOut - rIn <= 1e-12)
            {
                plan.Add(new Layer(rOut, opacity));        // a hard-edged shadow
                return plan;
            }
            double prev = 0.0;
            for (int k = 1; k <= n; k++)
            {
                double t = (double)k / n;
                double cum = opacity * (t * t * (3.0 - 2.0 * t));
                double alpha = 1.0 - (1.0 - cum) / (1.0 - prev);
                prev = cum;
                double r = rOut - (k - 1) * (rOut - rIn) / (n - 1);
                if (alpha > 0)
                    plan.Add(new Layer(r, alpha));
            }
            return plan;
        }

        /// <summary>The offset of the shadow: Distance away from the light.</summary>
        internal static (double dx, double dy) Offset(ShadowOptions opt)
        {
            double a = opt.AngleDeg * Math.PI / 180.0;
            return (-opt.Distance * Math.Cos(a), -opt.Distance * Math.Sin(a));
        }

        /// <summary>
        /// Creates the shadow's layer graphics for <paramref name="shape"/> (the
        /// background's polygon) in <paramref name="container"/>, and returns them
        /// grouped as one element, or null when no layer is visible. MCT.
        /// </summary>
        public static Element Build(IElementContainer container, Polygon shape,
            ShadowOptions opt, string baseName)
        {
            if (opt == null || !opt.Enabled || shape == null || shape.IsEmpty)
                return null;
            (double dx, double dy) = Offset(opt);
            Geometry moved = (dx == 0 && dy == 0) ? shape : GeometryEngine.Instance.Move(shape, dx, dy);

            var made = new List<Element>();
            string name = string.IsNullOrWhiteSpace(baseName) ? "Background" : baseName.Trim();
            int i = 0;
            foreach (Layer layer in Plan(opt))
            {
                i++;
                Geometry g = moved;
                try
                {
                    if (Math.Abs(layer.Buffer) > 1e-12)
                        g = GeometryEngine.Instance.Buffer(moved, layer.Buffer);
                    if (g == null || g.IsEmpty)
                        continue;                      // a negative buffer ate the shape
                    g = GeometryEngine.Instance.Difference(g, shape);   // knock out
                }
                catch
                {
                    continue;
                }
                if (g is not Polygon poly || poly.IsEmpty)
                    continue;                          // wholly hidden under the background

                CIMColor color = ColorFactory.Instance.CreateRGBColor(
                    opt.R, opt.G, opt.B, layer.Alpha * 100.0);
                CIMPolygonSymbol sym = SymbolFactory.Instance.ConstructPolygonSymbol(
                    color, SimpleFillStyle.Solid, null);          // no outline
                made.Add(ElementFactory.Instance.CreateGraphicElement(
                    container, poly, sym, $"{name} Shadow {i}", false, null));
            }
            if (made.Count == 0)
                return null;
            if (made.Count == 1)
                return made[0];
            return ElementFactory.Instance.CreateGroupElement(
                container, made, name + " Shadow", false, null);
        }
    }
}
