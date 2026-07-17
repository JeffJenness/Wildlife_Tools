using ArcGIS.Core.CIM;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;

namespace WildlifeTools
{
    /// <summary>
    /// Options collected from <see cref="AddCircleWindow"/> for one run of the
    /// "circle behind selected elements" tool.
    /// </summary>
    internal sealed class AddCircleOptions
    {
        public string Name { get; set; }
        public CIMSymbolReference SymbolReference { get; set; }
        public double FillTransparency { get; set; }      // 0..100 (% transparent)
        public double OutlineTransparency { get; set; }   // 0..100 (% transparent)
        public double RadiusPercent { get; set; }         // 100 = circle exactly contains the extent
        public bool UseFocalPoint { get; set; }
    }

    /// <summary>
    /// A snapshot of the current element selection, gathered on the MCT before the
    /// dialog opens: which container the elements live in, the selected elements
    /// (for the extent), the top-level entries to reparent into the new group
    /// (whole groups when a selected element is inside one), and — only when a
    /// single element with a stamped focal point is selected — that focal point.
    /// </summary>
    internal sealed class AddCircleContext
    {
        public IElementContainer Container { get; set; }
        public List<Element> Selected { get; set; } = new();
        public List<Element> TopLevelEntries { get; set; } = new();
        public Envelope Extent { get; set; }
        public SpatialReference SpatialRef { get; set; }
        public bool SingleHasFocal { get; set; }
        public Coordinate2D Focal { get; set; }
        public CIMSymbolReference DefaultSymbol { get; set; }
        /// <summary>Selection spans more than one graphics layer (map) — unsupported.</summary>
        public bool MultiContainer { get; set; }
    }

    /// <summary>
    /// Core logic for the Add Graphic Circle tool: places a circle graphic behind one
    /// or more selected elements (graphics or layout map elements — NOT dataset
    /// features), then groups the circle and the selected element(s) into a new group
    /// element with the circle underneath. All methods run on the MCT (QueuedTask).
    /// See <see cref="AddGraphicCircleButton"/> and <see cref="AddCircleWindow"/>.
    /// </summary>
    internal static class AddCircleOperation
    {
        /// <summary>
        /// Inspects the active layout or map view for selected elements and builds an
        /// <see cref="AddCircleContext"/>, or returns null if nothing is selected.
        /// MCT.
        /// </summary>
        public static AddCircleContext Inspect()
        {
            LayoutView lv = LayoutView.Active;
            if (lv?.Layout is Layout layout)
            {
                IReadOnlyList<Element> sel = lv.GetSelectedElements();
                return (sel == null || sel.Count == 0) ? null : Build(layout, sel);
            }

            if (MapView.Active?.Map is Map map)
            {
                // Graphic elements on a map live in graphics layers; gather any that
                // hold a selection. We operate within a single graphics layer.
                var hits = map.GetLayersAsFlattenedList().OfType<GraphicsLayer>()
                    .Select(gl => new { gl, sel = gl.GetSelectedElements() })
                    .Where(x => x.sel != null && x.sel.Count > 0)
                    .ToList();
                if (hits.Count == 0)
                    return null;
                if (hits.Count > 1)
                    return new AddCircleContext { MultiContainer = true };
                return Build(hits[0].gl, hits[0].sel);
            }

            return null;
        }

        private static AddCircleContext Build(IElementContainer container, IReadOnlyList<Element> sel)
        {
            // Union extent of the SELECTED elements (their visual, rotation-aware bounds).
            double xmin = double.PositiveInfinity, ymin = double.PositiveInfinity;
            double xmax = double.NegativeInfinity, ymax = double.NegativeInfinity;
            SpatialReference sr = null;
            foreach (Element el in sel)
            {
                Envelope b = el.GetBounds(true);
                if (b == null || b.IsEmpty)
                    continue;
                sr ??= b.SpatialReference;
                xmin = Math.Min(xmin, b.XMin); ymin = Math.Min(ymin, b.YMin);
                xmax = Math.Max(xmax, b.XMax); ymax = Math.Max(ymax, b.YMax);
            }
            Envelope extent = double.IsInfinity(xmin)
                ? null
                : EnvelopeBuilderEx.CreateEnvelope(xmin, ymin, xmax, ymax, sr);

            // Top-level entries: whenever a selected element is inside a group, pull in
            // the whole (outermost) group instead of the lone element. De-dup by ref.
            var entries = new List<Element>();
            var seen = new HashSet<Element>(ReferenceEqualityComparer.Instance);
            foreach (Element el in sel)
            {
                Element top = TopLevelEntry(el);
                if (seen.Add(top))
                    entries.Add(top);
            }

            // Focal-point option only for a single selected element that carries one.
            bool singleFocal = false;
            Coordinate2D focal = default;
            if (sel.Count == 1 && CompassRose.TryGetFocalPoint(sel[0], out Coordinate2D f))
            {
                singleFocal = true;
                focal = f;
            }

            return new AddCircleContext
            {
                Container = container,
                Selected = sel.ToList(),
                TopLevelEntries = entries,
                Extent = extent,
                SpatialRef = sr,
                SingleHasFocal = singleFocal,
                Focal = focal,
                DefaultSymbol = DefaultCircleSymbol().MakeSymbolReference(),
            };
        }

        /// <summary>The outermost group an element belongs to, or the element itself if
        /// it sits directly in the root container. Walks up while the parent is a
        /// <see cref="GroupElement"/>.</summary>
        private static Element TopLevelEntry(Element el)
        {
            Element cur = el;
            while (cur.GetParent(false) is GroupElement grp)
                cur = grp;
            return cur;
        }

        /// <summary>A sensible starting polygon symbol (pale yellow fill, default
        /// outline) for the circle before the user edits it. MCT.</summary>
        public static CIMPolygonSymbol DefaultCircleSymbol()
        {
            CIMColor fill = ColorFactory.Instance.CreateRGBColor(255, 240, 150);
            return SymbolFactory.Instance.ConstructPolygonSymbol(fill, SimpleFillStyle.Solid);
        }

        /// <summary>
        /// Runs the tool: creates the circle graphic, groups it with the selected
        /// element(s)/their groups, and sends the circle to the bottom of the group.
        /// Returns the new group element. MCT.
        /// </summary>
        public static GroupElement Run(AddCircleContext ctx, AddCircleOptions opt)
        {
            if (ctx?.Container == null || ctx.Extent == null)
                throw new InvalidOperationException("No element selection extent is available.");

            // Center: the stamped focal point (single element, opted in) or the extent center.
            double cx, cy;
            if (opt.UseFocalPoint && ctx.SingleHasFocal)
            {
                cx = ctx.Focal.X;
                cy = ctx.Focal.Y;
            }
            else
            {
                cx = (ctx.Extent.XMin + ctx.Extent.XMax) / 2.0;
                cy = (ctx.Extent.YMin + ctx.Extent.YMax) / 2.0;
            }

            // Radius: 100% = the circle that just reaches the extent EDGES (the edge
            // midpoints), not the corners. For a symmetric graphic (e.g. a compass whose
            // cardinal points touch the extent edges) this hugs the graphic; centered,
            // it equals max(width, height) / 2. Using the corners (half-diagonal) instead
            // overshoots by a factor of sqrt(2).
            double r100 = MaxEdgeMidpointDistance(cx, cy, ctx.Extent);
            double radius = r100 * (opt.RadiusPercent / 100.0);
            if (radius <= 0)
                radius = r100 > 0 ? r100 * 0.01 : 1.0;   // guard against a zero/degenerate radius

            Envelope circleEnv = EnvelopeBuilderEx.CreateEnvelope(
                cx - radius, cy - radius, cx + radius, cy + radius, ctx.SpatialRef);

            // Symbol: the user's edited symbol (or the default), with the two
            // transparencies applied to the fill layers and the outline layers.
            CIMPolygonSymbol polySym = opt.SymbolReference?.Symbol as CIMPolygonSymbol
                                       ?? DefaultCircleSymbol();
            ApplyTransparency(polySym, opt.FillTransparency, opt.OutlineTransparency);

            string circleName = string.IsNullOrWhiteSpace(opt.Name) ? null : opt.Name.Trim();
            GraphicElement circle = ElementFactory.Instance.CreatePredefinedShapeGraphicElement(
                ctx.Container, PredefinedShape.Circle, circleEnv, polySym, circleName, false, null);

            // New group: the circle plus the selected element(s) / their whole groups.
            var toGroup = new List<Element>(ctx.TopLevelEntries) { circle };
            string groupName = string.IsNullOrWhiteSpace(opt.Name)
                ? "Graphic Circle Group"
                : opt.Name.Trim() + " Group";
            GroupElement group = ElementFactory.Instance.CreateGroupElement(
                ctx.Container, toGroup, groupName, true, null);

            // Draw the circle underneath everything else in the group (bottom of the
            // group's TOC = back of the draw order).
            try
            {
                circle.SetTOCPositionAbsolute(group, false);
            }
            catch
            {
                // Fall back to locating the circle within the group by name.
                if (circleName != null &&
                    group.GetElementsAsFlattenedList().FirstOrDefault(e => e.Name == circleName) is Element c)
                    c.SetTOCPositionAbsolute(group, false);
            }

            return group;
        }

        /// <summary>The greatest distance from (cx,cy) to any of the four edge midpoints
        /// of <paramref name="extent"/>. Centered, this equals max(width, height) / 2 —
        /// the radius that just reaches the farthest extent edge.</summary>
        private static double MaxEdgeMidpointDistance(double cx, double cy, Envelope extent)
        {
            double xmid = (extent.XMin + extent.XMax) / 2.0;
            double ymid = (extent.YMin + extent.YMax) / 2.0;
            double d = 0;
            foreach ((double x, double y) in new[]
            {
                (xmid, extent.YMin), (xmid, extent.YMax),
                (extent.XMin, ymid), (extent.XMax, ymid),
            })
                d = Math.Max(d, Math.Sqrt((x - cx) * (x - cx) + (y - cy) * (y - cy)));
            return d;
        }

        // ------------------------------------------------------------------
        //  Transparency: fill transparency -> the interior fill layers only;
        //  outline transparency -> the boundary stroke layers only. Applied as
        //  each layer color's alpha (alpha 0..100, opaque = 100).
        // ------------------------------------------------------------------

        private static void ApplyTransparency(CIMPolygonSymbol sym, double fillTranspPct, double outlineTranspPct)
        {
            if (sym?.SymbolLayers == null)
                return;
            double fillAlpha = Math.Clamp(100.0 - fillTranspPct, 0.0, 100.0);
            double strokeAlpha = Math.Clamp(100.0 - outlineTranspPct, 0.0, 100.0);
            foreach (CIMSymbolLayer layer in sym.SymbolLayers)
            {
                if (layer is CIMFill)
                    SetLayerAlpha(layer, fillAlpha);
                else if (layer is CIMStroke)
                    SetLayerAlpha(layer, strokeAlpha);
            }
        }

        private static void SetLayerAlpha(CIMSymbolLayer layer, double alpha)
        {
            switch (layer)
            {
                case CIMSolidFill f:
                    SetAlpha(f.Color, alpha);
                    break;
                case CIMSolidStroke s:
                    SetAlpha(s.Color, alpha);
                    break;
                case CIMGradientFill g:
                    // A gradient fill has no single transparency; set the alpha on every
                    // color in its ramp. ArcGIS interpolates alpha along with the colors,
                    // so a uniform alpha yields uniform transparency across the gradient.
                    SetColorRampAlpha(g.ColorRamp, alpha);
                    break;
                case CIMHatchFill h when h.LineSymbol is CIMLineSymbol ls:
                    foreach (CIMSymbolLayer inner in ls.SymbolLayers ?? Array.Empty<CIMSymbolLayer>())
                        SetLayerAlpha(inner, alpha);
                    break;
                default:
                    // Best effort for other layer types: a single Color and/or a ColorRamp
                    // (e.g. gradient strokes).
                    Type lt = layer.GetType();
                    if (lt.GetProperty("Color") is PropertyInfo cp &&
                        typeof(CIMColor).IsAssignableFrom(cp.PropertyType) &&
                        cp.GetValue(layer) is CIMColor c)
                        SetAlpha(c, alpha);
                    if (lt.GetProperty("ColorRamp") is PropertyInfo rp &&
                        typeof(CIMColorRamp).IsAssignableFrom(rp.PropertyType) &&
                        rp.GetValue(layer) is CIMColorRamp ramp)
                        SetColorRampAlpha(ramp, alpha);
                    break;
            }
        }

        private static void SetAlpha(CIMColor color, double alpha) => color?.SetAlphaValue(alpha);

        /// <summary>Sets the given alpha (0..100, opaque = 100) on every color in a color
        /// ramp, covering the standard ramp shapes: continuous (From/To color), fixed
        /// (Colors array), multipart (nested ramps), and random HSV (Min/Max alpha).
        /// Reflection keeps it robust to whichever ramp type the symbol editor produced.</summary>
        private static void SetColorRampAlpha(CIMColorRamp ramp, double alpha)
        {
            if (ramp == null)
                return;
            if (ramp is CIMRandomHSVColorRamp rnd)
            {
                // A random ramp generates its colors; steer their alpha via its range.
                rnd.MinAlpha = (float)alpha;
                rnd.MaxAlpha = (float)alpha;
                return;
            }
            foreach (PropertyInfo p in ramp.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
            {
                object val;
                try { val = p.CanRead ? p.GetValue(ramp) : null; }
                catch { continue; }
                switch (val)
                {
                    case CIMColor c:
                        SetAlpha(c, alpha);
                        break;
                    case CIMColor[] colors:
                        foreach (CIMColor cc in colors) SetAlpha(cc, alpha);
                        break;
                    case CIMColorRamp r:
                        SetColorRampAlpha(r, alpha);
                        break;
                    case CIMColorRamp[] ramps:
                        foreach (CIMColorRamp rr in ramps) SetColorRampAlpha(rr, alpha);
                        break;
                }
            }
        }
    }
}
