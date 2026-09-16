using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.Collections.Generic;
using System.Linq;

namespace WildlifeTools
{
    /// <summary>
    /// Options collected from <see cref="AddPolygonWindow"/> for one run of the
    /// Polygon Background tool.
    /// </summary>
    internal sealed class AddPolygonOptions
    {
        public string Name { get; set; }
        public CIMSymbolReference SymbolReference { get; set; }
        public double FillTransparency { get; set; }      // 0..100 (% transparent)
        public double OutlineTransparency { get; set; }   // 0..100 (% transparent)
        /// <summary>True: trace the symbolized shape (symbol widths included — the
        /// ArcObjects QueryBounds analog); false: the element's source geometry.</summary>
        public bool UseSymbolizedShape { get; set; }
        /// <summary>Buffer distance ALREADY CONVERTED to container units (page units on
        /// a layout, map units on a map). Zero = trace the shape exactly.</summary>
        public double BufferDistance { get; set; }
        /// <summary>Remove any interior holes from the final background polygon.</summary>
        public bool RemoveHoles { get; set; }
    }

    /// <summary>
    /// Core logic for the Polygon Background tool: builds a polygon that fits the
    /// actual shape of the selected element(s) — from each element's source geometry
    /// (Element.GetGeometry, the ArcObjects IElement.Geometry analog) or its symbolized
    /// outline (GraphicFactory.GetGraphicOutline, the QueryBounds analog) — optionally
    /// buffered, then groups it behind the selection. Selection snapshot, transparency,
    /// and grouping are shared with the other Background tools
    /// (<see cref="AddCircleOperation"/>). All methods run on the MCT.
    /// </summary>
    internal static class AddPolygonOperation
    {
        /// <summary>Runs the tool and returns the new group element. MCT.</summary>
        public static GroupElement Run(AddCircleContext ctx, AddPolygonOptions opt)
        {
            if (ctx?.Container == null || ctx.Selected.Count == 0)
                throw new InvalidOperationException("No element selection is available.");

            // One shape per selected element.
            var shapes = new List<Geometry>();
            foreach (Element el in ctx.Selected)
            {
                Geometry g = ShapeOf(el, ctx.Container, opt.UseSymbolizedShape);
                if (g != null && !g.IsEmpty)
                    shapes.Add(g);
            }
            if (shapes.Count == 0)
                throw new InvalidOperationException("None of the selected elements provided a usable shape.");

            // Buffer each shape (turns lines/points into polygons too), then union.
            var polys = new List<Geometry>();
            foreach (Geometry g in shapes)
            {
                Geometry p = g;
                if (opt.BufferDistance != 0)
                {
                    try
                    {
                        p = GeometryEngine.Instance.Buffer(g, opt.BufferDistance);
                    }
                    catch (Exception)
                    {
                        throw new InvalidOperationException(
                            "The buffer could not be created. A negative buffer must not " +
                            "shrink the shape away entirely, and point or line shapes need " +
                            "a buffer size greater than zero.");
                    }
                }
                if (p is Polygon poly && !poly.IsEmpty)
                    polys.Add(poly);
                else if (p != null && !p.IsEmpty)
                    throw new InvalidOperationException(
                        "A selected element's shape is a point or line. Specify a buffer " +
                        "size greater than zero so a polygon can be built around it.");
            }
            if (polys.Count == 0)
                throw new InvalidOperationException(
                    "The buffered shape is empty. Use a smaller negative buffer, or a positive one.");

            Geometry unioned = polys.Count == 1 ? polys[0] : GeometryEngine.Instance.Union(polys);
            if (unioned is not Polygon result || result.IsEmpty)
                throw new InvalidOperationException("The combined shape is not a valid polygon.");

            // Optionally drop interior holes — whether inherited from the source shapes
            // or formed by the union (e.g. several elements enclosing a gap).
            if (opt.RemoveHoles)
                result = StripHoles(result);

            CIMPolygonSymbol polySym = opt.SymbolReference?.Symbol as CIMPolygonSymbol
                                       ?? AddCircleOperation.DefaultCircleSymbol();
            AddCircleOperation.ApplyTransparency(polySym, opt.FillTransparency, opt.OutlineTransparency);

            string name = string.IsNullOrWhiteSpace(opt.Name) ? null : opt.Name.Trim();
            GraphicElement bg = ElementFactory.Instance.CreateGraphicElement(
                ctx.Container, result, polySym, name, false, null);

            return AddCircleOperation.GroupBehind(ctx, bg, name, "Graphic Polygon Group");
        }

        /// <summary>
        /// The shape of one element. Symbolized mode uses Esri's graphic-outline query
        /// (includes stroke widths, marker sizes, etc.) for graphic elements, falling
        /// back to the element's rotated bounds for non-graphic elements (map frames,
        /// map surrounds, ...). Source mode uses the element's own geometry — with two
        /// exceptions found live on 2026-09-16 (Jeff's legend + text MCP came out as
        /// the legend's oversized frame with a slanted top): a NON-graphic element's
        /// GetGeometry is its FRAME, which a legend's drawn content may fill only
        /// partly, and a text element's geometry is a single anchor POINT. Both take
        /// the drawn, rotation-aware bounds instead, which is what a background must
        /// wrap. MCT.
        /// </summary>
        internal static Geometry ShapeOf(Element el, IElementContainer container, bool symbolized)
        {
            if (symbolized)
            {
                if (el is GraphicElement ge)
                {
                    try
                    {
                        Geometry outline = GraphicFactory.Instance.GetGraphicOutline(container, ge.GetGraphic());
                        if (outline != null && !outline.IsEmpty)
                            return Rotated(outline, el);
                    }
                    catch
                    {
                        // Fall through to the bounds fallback below.
                    }
                }
                return BoundsPolygon(el);
            }

            // Source geometry: only a graphic element's own polygon/polyline/multipoint
            // is a meaningful shape. Map surrounds (legends, scale bars, north arrows),
            // map frames and tables report their frame; text and point graphics report
            // an anchor point. Those get the drawn bounds.
            if (el is not GraphicElement)
                return BoundsPolygon(el);
            Geometry geom = null;
            try { geom = el.GetGeometry(); } catch { }
            if (geom == null || geom.IsEmpty || geom is MapPoint)
                return BoundsPolygon(el);
            Envelope ext = geom.Extent;
            if (ext == null || ext.IsEmpty || (ext.Width <= 0 && ext.Height <= 0))
                return BoundsPolygon(el);
            return Rotated(geom, el);
        }

        /// <summary>The element's drawn, rotation-aware bounds as a polygon (null when
        /// the element has none). GetBounds(true) already applies the element's
        /// rotation, so it must not pass through Rotated().</summary>
        private static Geometry BoundsPolygon(Element el)
        {
            Envelope b = null;
            try { b = el.GetBounds(true); } catch { }
            return (b == null || b.IsEmpty) ? null : PolygonBuilderEx.CreatePolygon(b);
        }

        /// <summary>
        /// Returns the polygon with only its exterior rings — interior holes removed.
        /// Esri simple-polygon convention: exterior rings wind clockwise, holes
        /// counterclockwise; a ring's winding is read from its shoelace signed area
        /// (counterclockwise positive, so exterior = non-positive). Curve segments are
        /// judged by their chords, which is ample for an orientation sign.
        /// </summary>
        private static Polygon StripHoles(Polygon poly)
        {
            if (poly == null || poly.PartCount <= 1)
                return poly;
            var builder = new PolygonBuilderEx(poly.SpatialReference);
            bool kept = false;
            foreach (var part in poly.Parts)
            {
                double area2 = 0;
                foreach (Segment seg in part)
                {
                    Coordinate2D p = seg.StartCoordinate, q = seg.EndCoordinate;
                    area2 += p.X * q.Y - q.X * p.Y;
                }
                if (area2 <= 0)                       // clockwise (or degenerate) = exterior
                {
                    builder.AddPart(part.ToList());
                    kept = true;
                }
            }
            return kept && builder.ToGeometry() is Polygon stripped && !stripped.IsEmpty
                ? stripped : poly;
        }

        /// <summary>
        /// Applies the element's rotation to a traced shape. Pro stores an element's
        /// geometry (and its CIMGraphic) UNROTATED, with the rotation kept as a separate
        /// element property applied at draw time about the anchor point — so shapes from
        /// GetGeometry / GetGraphicOutline must be rotated here (verified: unrotated
        /// without this). GetBounds(true) is already rotation-aware and must NOT pass
        /// through this. Positive element rotation is counterclockwise, matching
        /// GeometryEngine.Rotate's convention.
        /// </summary>
        private static Geometry Rotated(Geometry g, Element el)
        {
            double deg = 0;
            try { deg = el.GetRotation(); } catch { }
            if (deg == 0 || g == null || g.IsEmpty)
                return g;
            try
            {
                Coordinate2D anchor = el.GetAnchorPoint();
                MapPoint pivot = MapPointBuilderEx.CreateMapPoint(anchor.X, anchor.Y, g.SpatialReference);
                return GeometryEngine.Instance.Rotate(g, pivot, deg * Math.PI / 180.0);
            }
            catch
            {
                return g;
            }
        }
    }
}
