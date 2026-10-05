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
        /// <summary>Optional drop shadow beneath the background (null = none).</summary>
        public ShadowOptions Shadow { get; set; }
        public double FillTransparency { get; set; }      // 0..100 (% transparent)
        public double OutlineTransparency { get; set; }   // 0..100 (% transparent)
        public double RadiusPercent { get; set; }         // 100 = circle exactly contains the selected objects
        /// <summary>True: the radius is the containing circle's radius PLUS
        /// <see cref="RadiusMargin"/>; false: <see cref="RadiusPercent"/> applies.</summary>
        public bool UseMargin { get; set; }
        /// <summary>Margin beyond the containing circle, ALREADY in container units.</summary>
        public double RadiusMargin { get; set; }
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

        /// <summary>True when the container is a layout (coordinates are page units);
        /// false for a map's graphics layer (map coordinate system units).</summary>
        public bool IsLayout { get; set; }

        /// <summary>Map only: the name of the map coordinate system's linear (or
        /// angular, for geographic) unit — shown after the buffer text box.</summary>
        public string MapUnitName { get; set; }

        /// <summary>Layout only: meters per page unit (from the page's Units), for
        /// converting a user buffer distance in mm/cm/inches/points to page units.</summary>
        public double MetersPerPageUnit { get; set; } = 0.0254;
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

            // Coordinate-unit info for the Polygon Background buffer UI.
            bool isLayout = container is Layout;
            string mapUnitName = null;
            double metersPerPageUnit = 0.0254;
            if (container is Layout lay)
            {
                try { metersPerPageUnit = lay.GetPage().Units.ConversionFactor; } catch { }
            }
            else if (container is GraphicsLayer glyr)
            {
                try { mapUnitName = (glyr.GetSpatialReference() ?? sr)?.Unit?.Name; } catch { }
                mapUnitName ??= sr?.Unit?.Name;
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
                IsLayout = isLayout,
                MapUnitName = mapUnitName,
                MetersPerPageUnit = metersPerPageUnit,
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

        /// <summary>Deserializes a background tool's saved polygon-symbol reference
        /// (CIM JSON persisted with the project via <see cref="Module1"/>); null if the
        /// JSON is absent, invalid, or not a polygon symbol.</summary>
        internal static CIMSymbolReference SavedSymbolOrNull(string json)
        {
            if (string.IsNullOrEmpty(json))
                return null;
            try
            {
                CIMSymbolReference r = CIMSymbolReference.FromJson(json, null);
                return r?.Symbol is CIMPolygonSymbol ? r : null;
            }
            catch
            {
                return null;
            }
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

            // Radius: 100% (or a zero margin) = the smallest circle about the center
            // that CONTAINS THE OBJECTS THEMSELVES as drawn -- not their extent
            // rectangle (Jeff, 2026-10-04). A rectangle is contained through its
            // corners; a compass rose or north arrow is hugged at its points. The
            // old rule reached only the extent's edges, which left a rectangle's
            // corners sticking out; the extent's corners would leave a round
            // element floating in a circle 41% too wide.
            double r100 = MaxShapeDistance(ctx, cx, cy);
            double radius = opt.UseMargin
                ? r100 + opt.RadiusMargin                  // linear margin beyond the containing circle
                : r100 * (opt.RadiusPercent / 100.0);
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

            // Optional drop shadow: a stack of translucent copies of the circle,
            // grouped beneath it. The predefined circle is an envelope to Pro, so
            // the shadow works from a true disc of the same center and radius.
            Element shadow = null;
            try
            {
                if (opt.Shadow != null && opt.Shadow.Enabled &&
                    GeometryEngine.Instance.Buffer(
                        MapPointBuilderEx.CreateMapPoint(cx, cy, ctx.SpatialRef), radius) is Polygon disc)
                    shadow = BackgroundShadow.Build(ctx.Container, disc, opt.Shadow, circleName ?? "Circle");
            }
            catch { shadow = null; }      // a shadow problem must not lose the background

            return GroupBehind(ctx, circle, circleName, "Graphic Circle Group", shadow);
        }

        /// <summary>
        /// Groups the freshly created background element with the selection's top-level
        /// entries into a new group, and sends the background to the bottom of the
        /// group's draw order. Shared by the Circle and Rectangle Background tools. MCT.
        /// </summary>
        /// <param name="beneath">Optional element (a drop shadow) that joins the
        /// group and is sent below the background itself.</param>
        internal static GroupElement GroupBehind(AddCircleContext ctx, GraphicElement background,
            string backgroundName, string defaultGroupName, Element beneath = null)
        {
            var toGroup = new List<Element>(ctx.TopLevelEntries) { background };
            if (beneath != null)
                toGroup.Add(beneath);
            string groupName = string.IsNullOrWhiteSpace(backgroundName)
                ? defaultGroupName
                : backgroundName.Trim() + " Group";
            GroupElement group = ElementFactory.Instance.CreateGroupElement(
                ctx.Container, toGroup, groupName, true, null);

            // Bottom of the group's TOC = back of the draw order.
            try
            {
                background.SetTOCPositionAbsolute(group, false);
            }
            catch
            {
                // Fall back to locating the background within the group by name.
                if (backgroundName != null &&
                    group.GetElementsAsFlattenedList().FirstOrDefault(e => e.Name == backgroundName) is Element c)
                    c.SetTOCPositionAbsolute(group, false);
            }
            if (beneath != null)
            {
                // sent to the bottom AFTER the background, so it ends up below it
                try { beneath.SetTOCPositionAbsolute(group, false); } catch { }
            }

            return group;
        }

        /// <summary>
        /// The greatest distance from (cx,cy) to any vertex of the selected elements'
        /// DRAWN shapes (symbol widths included; curves densified; rotation applied) --
        /// the radius of the smallest circle about that center that contains them.
        /// Groups are opened so each member is traced by itself. An element with no
        /// traceable outline (text, legends, pictures, map frames) contributes its
        /// drawn bounds; one of our own compass graphics contributes the four points
        /// where its artwork meets its bounds. Falls back to the extent's corners if
        /// nothing can be traced.
        /// </summary>
        private static double MaxShapeDistance(AddCircleContext ctx, double cx, double cy)
        {
            var coords = new List<Coordinate2D>();
            try
            {
                foreach (Element sel in ctx.Selected)
                {
                    IEnumerable<Element> members = sel is GroupElement grp
                        ? grp.GetElementsAsFlattenedList().Where(m => m is not GroupElement)
                        : new[] { sel };
                    foreach (Element el in members)
                    {
                        // One of our compass graphics / north arrows (it carries a
                        // stamped focal point). Pro reports no outline for these,
                        // only a bounding box, and the box's corners are empty
                        // space: the artwork is a star whose points touch the
                        // box's EDGES. Fit those points, not the corners (Jeff,
                        // 2026-10-04: the corner fit drew a circle far larger
                        // than the north arrow).
                        if (CompassRose.TryGetFocalPoint(el, out _))
                        {
                            Envelope b = null;
                            try { b = el.GetBounds(true); } catch { }
                            if (b != null && !b.IsEmpty)
                            {
                                double xm = (b.XMin + b.XMax) / 2.0, ym = (b.YMin + b.YMax) / 2.0;
                                coords.Add(new Coordinate2D(xm, b.YMin));
                                coords.Add(new Coordinate2D(xm, b.YMax));
                                coords.Add(new Coordinate2D(b.XMin, ym));
                                coords.Add(new Coordinate2D(b.XMax, ym));
                                continue;
                            }
                        }
                        AddMcpOperation.CollectCoordinates(
                            AddPolygonOperation.ShapeOf(el, ctx.Container, true), coords);
                    }
                }
            }
            catch
            {
                coords.Clear();
            }
            if (coords.Count == 0)
                return MaxCornerDistance(cx, cy, ctx.Extent);
            double d2 = 0;
            foreach (Coordinate2D c in coords)
                d2 = Math.Max(d2, (c.X - cx) * (c.X - cx) + (c.Y - cy) * (c.Y - cy));
            return d2 > 0 ? Math.Sqrt(d2) : MaxCornerDistance(cx, cy, ctx.Extent);
        }

        /// <summary>The greatest distance from (cx,cy) to any of the four corners of
        /// <paramref name="extent"/>: the radius of the smallest circle about that
        /// center that contains the whole extent. Centered, this is half the
        /// extent's diagonal.</summary>
        private static double MaxCornerDistance(double cx, double cy, Envelope extent)
        {
            double d = 0;
            foreach ((double x, double y) in new[]
            {
                (extent.XMin, extent.YMin), (extent.XMin, extent.YMax),
                (extent.XMax, extent.YMin), (extent.XMax, extent.YMax),
            })
                d = Math.Max(d, Math.Sqrt((x - cx) * (x - cx) + (y - cy) * (y - cy)));
            return d;
        }

        // ------------------------------------------------------------------
        //  Transparency: fill transparency -> the interior fill layers only;
        //  outline transparency -> the boundary stroke layers only. Applied as
        //  each layer color's alpha (alpha 0..100, opaque = 100).
        // ------------------------------------------------------------------

        internal static void ApplyTransparency(CIMPolygonSymbol sym, double fillTranspPct, double outlineTranspPct)
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
