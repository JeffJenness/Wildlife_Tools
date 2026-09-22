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
    /// Options collected from <see cref="AddMcpWindow"/> for one run of the
    /// Minimum Convex Polygon tool.
    /// </summary>
    internal sealed class AddMcpOptions
    {
        public string Name { get; set; }
        public CIMSymbolReference SymbolReference { get; set; }
        public double FillTransparency { get; set; }      // 0..100 (% transparent)
        public double OutlineTransparency { get; set; }   // 0..100 (% transparent)
        /// <summary>True: hull the symbolized shapes; false: the source geometries.</summary>
        public bool UseSymbolizedShape { get; set; }
        /// <summary>Buffer distance ALREADY CONVERTED to container units, applied to
        /// the hull (expands the MCP). Zero = the exact MCP.</summary>
        public double BufferDistance { get; set; }
    }

    /// <summary>
    /// Core logic for the Minimum Convex Polygon tool: traces the selected element
    /// shapes exactly like the Polygon Background tool
    /// (<see cref="AddPolygonOperation.ShapeOf"/> — source geometry or symbolized
    /// outline, element rotation applied), pools all their vertices, and wraps them in
    /// a minimum convex polygon (GeometryEngine.ConvexHull). An optional buffer then
    /// expands the hull. The result is grouped behind the selection like the other
    /// Background tools. All methods run on the MCT.
    /// </summary>
    internal static class AddMcpOperation
    {
        /// <summary>Runs the tool and returns the new group element. MCT.</summary>
        public static GroupElement Run(AddCircleContext ctx, AddMcpOptions opt)
        {
            if (ctx?.Container == null || ctx.Selected.Count == 0)
                throw new InvalidOperationException("No element selection is available.");

            // Pool the vertices of every element's traced shape.
            var coords = new List<Coordinate2D>();
            foreach (Element el in ctx.Selected)
            {
                Geometry g = AddPolygonOperation.ShapeOf(el, ctx.Container, opt.UseSymbolizedShape);
                CollectCoordinates(g, coords);
            }
            if (coords.Count == 0)
                throw new InvalidOperationException("None of the selected elements provided a usable shape.");

            // The classic straight-edged MCP: convex hull of the pooled vertices.
            Multipoint pool = MultipointBuilderEx.CreateMultipoint(coords, ctx.SpatialRef);
            Geometry hull = GeometryEngine.Instance.ConvexHull(pool);

            // Optional buffer expands the hull (also polygonizes a degenerate hull —
            // a single point or a collinear selection yields a point/line hull).
            if (opt.BufferDistance != 0)
            {
                try
                {
                    hull = GeometryEngine.Instance.Buffer(hull, opt.BufferDistance);
                }
                catch (Exception)
                {
                    throw new InvalidOperationException(
                        "The buffer could not be created. A negative buffer must not " +
                        "shrink the polygon away entirely.");
                }
            }
            if (hull is not Polygon result || result.IsEmpty)
                throw new InvalidOperationException(
                    "The minimum convex polygon is degenerate (the selection's shape is a " +
                    "single point or a straight line). Specify a buffer size greater than zero.");

            CIMPolygonSymbol polySym = opt.SymbolReference?.Symbol as CIMPolygonSymbol
                                       ?? AddCircleOperation.DefaultCircleSymbol();
            AddCircleOperation.ApplyTransparency(polySym, opt.FillTransparency, opt.OutlineTransparency);

            string name = string.IsNullOrWhiteSpace(opt.Name) ? null : opt.Name.Trim();
            GraphicElement bg = ElementFactory.Instance.CreateGraphicElement(
                ctx.Container, result, polySym, name, false, null);

            return AddCircleOperation.GroupBehind(ctx, bg, name, "Minimum Convex Polygon Group");
        }

        /// <summary>
        /// Appends a geometry's vertices to the pool. Curved shapes (e.g. symbolized
        /// outlines with round joins) are densified first so arc bulges contribute
        /// points beyond their chord endpoints and the hull does not clip them.
        /// </summary>
        private static void CollectCoordinates(Geometry g, List<Coordinate2D> coords)
        {
            if (g == null || g.IsEmpty)
                return;
            switch (g)
            {
                case MapPoint p:
                    coords.Add(new Coordinate2D(p.X, p.Y));
                    return;
                case Envelope env:
                    coords.Add(new Coordinate2D(env.XMin, env.YMin));
                    coords.Add(new Coordinate2D(env.XMin, env.YMax));
                    coords.Add(new Coordinate2D(env.XMax, env.YMax));
                    coords.Add(new Coordinate2D(env.XMax, env.YMin));
                    return;
                case Multipoint mp:
                    foreach (MapPoint pt in mp.Points)
                        coords.Add(new Coordinate2D(pt.X, pt.Y));
                    return;
                case Multipart part:
                {
                    Multipart source = part;
                    if (part.HasCurves)
                    {
                        try
                        {
                            Envelope e = part.Extent;
                            double step = Math.Max(e.Width, e.Height) / 200.0;
                            if (step > 0 &&
                                GeometryEngine.Instance.DensifyByLength(part, step) is Multipart dense)
                                source = dense;
                        }
                        catch
                        {
                            // Fall back to the control points below.
                        }
                    }
                    foreach (MapPoint pt in source.Points)
                        coords.Add(new Coordinate2D(pt.X, pt.Y));
                    return;
                }
            }
        }
    }
}
