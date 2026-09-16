using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.Collections.Generic;

namespace WildlifeTools
{
    /// <summary>
    /// Options collected from <see cref="AddRectangleWindow"/> for one run of the
    /// Rectangle Background tool.
    /// </summary>
    internal sealed class AddRectangleOptions
    {
        public string Name { get; set; }
        public CIMSymbolReference SymbolReference { get; set; }
        public double FillTransparency { get; set; }      // 0..100 (% transparent)
        public double OutlineTransparency { get; set; }   // 0..100 (% transparent)
        public double HorizontalExtentPct { get; set; }   // 100 = exactly the extent width
        public double VerticalExtentPct { get; set; }     // 100 = exactly the extent height
        /// <summary>True: the rectangle is the extent grown by the two margins below
        /// (each side); false: the two percentages apply.</summary>
        public bool UseMargins { get; set; }
        /// <summary>Margins beyond the extent, ALREADY in container units.</summary>
        public double HorizontalMargin { get; set; }
        public double VerticalMargin { get; set; }
        public double RoundingPct { get; set; }           // corner radius as % of the smaller
                                                          // dimension; capped at 50 (= capsule /
                                                          // perfect circle on a square)
    }

    /// <summary>
    /// Core logic for the Rectangle Background tool: places a (optionally rounded)
    /// rectangle graphic behind the selected element(s) and groups it with them, the
    /// rectangle underneath. The selection snapshot, transparency handling, and
    /// grouping are shared with the Circle Background tool
    /// (<see cref="AddCircleOperation"/>). The custom focal point is deliberately
    /// ignored — the rectangle is always centered on the selection's extent center.
    /// All methods run on the MCT.
    /// </summary>
    internal static class AddRectangleOperation
    {
        /// <summary>Vertices per quarter-circle corner arc (96 per full circle).</summary>
        private const int ArcPointsPerCorner = 24;

        /// <summary>
        /// Runs the tool: builds the rounded-rectangle polygon, creates the graphic
        /// element, groups it behind the selection. Returns the new group. MCT.
        /// </summary>
        public static GroupElement Run(AddCircleContext ctx, AddRectangleOptions opt)
        {
            if (ctx?.Container == null || ctx.Extent == null)
                throw new InvalidOperationException("No element selection extent is available.");

            double cx = (ctx.Extent.XMin + ctx.Extent.XMax) / 2.0;
            double cy = (ctx.Extent.YMin + ctx.Extent.YMax) / 2.0;

            // Rectangle size from the extent percentages; guard degenerate extents
            // (e.g. a perfectly horizontal line element has zero height).
            double w = opt.UseMargins
                ? ctx.Extent.Width + 2.0 * opt.HorizontalMargin
                : ctx.Extent.Width * (opt.HorizontalExtentPct / 100.0);
            double h = opt.UseMargins
                ? ctx.Extent.Height + 2.0 * opt.VerticalMargin
                : ctx.Extent.Height * (opt.VerticalExtentPct / 100.0);
            if (w <= 0 && h <= 0) { w = h = 1.0; }
            else if (w <= 0) { w = h * 0.01; }
            else if (h <= 0) { h = w * 0.01; }

            // Corner radius: rounding% of the SMALLER dimension, capped at 50% (at the
            // cap the straight edge on that side vanishes — a square becomes a perfect
            // circle, a non-square rectangle a capsule).
            double roundPct = Math.Clamp(opt.RoundingPct, 0.0, 50.0);
            double r = Math.Min(w, h) * (roundPct / 100.0);

            Polygon poly = RoundedRectangle(cx, cy, w, h, r, ctx.SpatialRef);

            CIMPolygonSymbol polySym = opt.SymbolReference?.Symbol as CIMPolygonSymbol
                                       ?? AddCircleOperation.DefaultCircleSymbol();
            AddCircleOperation.ApplyTransparency(polySym, opt.FillTransparency, opt.OutlineTransparency);

            string name = string.IsNullOrWhiteSpace(opt.Name) ? null : opt.Name.Trim();
            GraphicElement rect = ElementFactory.Instance.CreateGraphicElement(
                ctx.Container, poly, polySym, name, false, null);

            return AddCircleOperation.GroupBehind(ctx, rect, name, "Graphic Rectangle Group");
        }

        /// <summary>
        /// A rounded-rectangle polygon centered on (cx, cy): four straight edges, each
        /// shortened by the corner radius <paramref name="r"/> at both ends, joined by
        /// four quarter-circle arcs of radius r centered r inside each corner
        /// (densified at <see cref="ArcPointsPerCorner"/> vertices per corner). The
        /// ring is traced clockwise (ArcGIS exterior-ring convention). r = 0 gives a
        /// plain rectangle.
        /// </summary>
        private static Polygon RoundedRectangle(
            double cx, double cy, double w, double h, double r, SpatialReference sr)
        {
            double xmin = cx - w / 2.0, xmax = cx + w / 2.0;
            double ymin = cy - h / 2.0, ymax = cy + h / 2.0;

            var pts = new List<Coordinate2D>();
            if (r <= 0)
            {
                pts.Add(new Coordinate2D(xmin, ymax));
                pts.Add(new Coordinate2D(xmax, ymax));
                pts.Add(new Coordinate2D(xmax, ymin));
                pts.Add(new Coordinate2D(xmin, ymin));
                pts.Add(new Coordinate2D(xmin, ymax));
                return PolygonBuilderEx.CreatePolygon(pts, sr);
            }

            // Corner arc centers, r inside each corner.
            double left = xmin + r, right = xmax - r;
            double bottom = ymin + r, top = ymax - r;

            // Clockwise from the start of the top edge. Each corner sweeps 90 degrees
            // clockwise; Arc() emits the full arc including both endpoints, which also
            // supplies the straight-edge endpoints (duplicates are avoided by starting
            // each edge from the previous arc's last point).
            pts.Add(new Coordinate2D(left, ymax));            // top edge: left -> right
            Arc(pts, right, top, r, 90, 0);                   // NE corner: 90° -> 0°
            Arc(pts, right, bottom, r, 0, -90);               // SE corner: 0° -> -90°
            Arc(pts, left, bottom, r, -90, -180);             // SW corner: -90° -> -180°
            Arc(pts, left, top, r, 180, 90);                  // NW corner: 180° -> 90°
            pts.Add(new Coordinate2D(left, ymax));            // close back to the start

            return PolygonBuilderEx.CreatePolygon(pts, sr);
        }

        /// <summary>Appends a quarter arc of radius r about (ax, ay) from
        /// <paramref name="startDeg"/> to <paramref name="endDeg"/> (swept clockwise —
        /// decreasing angle), both endpoints included. Angles are standard math
        /// convention (0° = +x, counter-clockwise positive).</summary>
        private static void Arc(List<Coordinate2D> pts, double ax, double ay, double r,
            double startDeg, double endDeg)
        {
            for (int k = 0; k <= ArcPointsPerCorner; k++)
            {
                double deg = startDeg + (endDeg - startDeg) * k / ArcPointsPerCorner;
                double rad = deg * Math.PI / 180.0;
                pts.Add(new Coordinate2D(ax + r * Math.Cos(rad), ay + r * Math.Sin(rad)));
            }
        }
    }
}
