# Esri Enhancement Request — Expose the layout sketch/interaction environment to public add-in tools

*Drafted 2026-07-02 · Jenness Enterprises · for submission to ArcGIS Ideas (community.esri.com) and/or My Esri technical support.*

---

**Title:** Let public SDK tools (`MapTool`) sketch on a layout with the same rubber‑band, cursor, and resize handles Esri's own layout tools use

## Summary of the problem

The ArcGIS Pro SDK `MapTool` class provides a rich, built‑in interactive sketch environment — set `IsSketchTool = true` and `SketchType = SketchGeometryType.Rectangle`, and the framework draws a live rubber‑band box, shows the correct crosshair/draw cursor, and returns the geometry in `OnSketchCompleteAsync`. This works beautifully in a **map view**.

It does **not** work in a **layout view**. When the same tool is active over a layout, the sketch never engages (a layout page has no map coordinate system for it to operate in): the crosshair appears on activation but reverts to the pan cursor as soon as the mouse moves, no rubber‑band is drawn, and the drag pans the page instead of sketching.

Esri's own layout element tools (New Rectangle, New North Arrow, New Map Frame, etc.) do all of this perfectly — but they are implemented as **internal, non‑public classes** (e.g., `ArcGIS.Desktop.Internal.Layouts.Ribbon.NewRectangleTool`) built on a layout sketch subsystem that is not exposed in the public SDK. Third‑party developers cannot subclass or reach it.

## Current behavior

- Public `MapTool` + sketch, **map view** → live rubber‑band, correct cursor, `OnSketchCompleteAsync` returns the geometry. Works.
- Public `MapTool` + sketch, **layout view** → sketch does not engage; cursor reverts to pan; page pans instead of sketching. The only recourse is low‑level `OnToolMouseDown/Move/Up` handling plus the `Cursor` property, which the layout view overrides on hover — so the interaction ends up cursorless/feedback‑less and confusing.
- Esri's built‑in layout tools → full rubber‑band, cursor, snapping, and post‑create resize handles, via a **private** API.

## Requested enhancement

Expose the layout interaction/sketch environment to public add‑in tools, so a developer‑authored tool can create and place elements on a layout with the same first‑class experience Esri's own tools have. Any of these would solve it:

- Make `MapTool`'s existing sketch (`IsSketchTool` / `SketchType` / `OnSketchCompleteAsync`) function in a **layout view**, returning the sketch geometry in page coordinates; **or**
- Provide a public **`LayoutTool`** base class (parallel to `MapTool`) that surfaces the layout rubber‑band sketch, cursor control, snapping, and the standard "single‑click = default size / drag = box" placement; **or**
- At minimum, expose a public API to (a) set and retain a custom cursor over a layout view and (b) request a rubber‑band rectangle/box on a layout and receive its page‑coordinate result.

Backward compatible: existing tools are unaffected; this only adds capability that today is Esri‑internal only.

## Why it matters

Custom cartographic tools — for placing branded north arrows, logos, standardized map‑surround graphics, agency title blocks, and other layout elements — are a common and valuable category of add‑in. Today, developers must choose between a broken/confusing draw experience on layouts or a fallback single‑click placement that lacks the interactive sizing users expect from every built‑in layout tool. Exposing the layout sketch environment would let third‑party and organizational tools deliver the same professional, consistent placement UX as Esri's own layout tools.

## Workaround in use today (and its shortcomings)

Because the sketch cannot be driven on a layout, our tools fall back to: claim the mouse‑down (`e.Handled = true`) so the page doesn't pan, place the element at a default size on a single click, auto‑select it, and then programmatically switch the active tool to Esri's layout Select tool (`esri_layouts_selectByRectangleTool`) so the user can resize via its handles. It works, but it is a workaround: there is no draw‑a‑box sizing during creation and no controllable cursor — precisely the capabilities this request asks Esri to expose.

---

## Submission notes

- **Best venue:** ArcGIS Ideas (community.esri.com → Ideas) — upvotes carry weight in Esri's prioritization; this affects many cartographic add‑in authors.
- Can also be filed via **My Esri / technical support** as a product enhancement.
- A 3–4 sentence version for the Ideas description box can be distilled from "Summary of the problem" + "Requested enhancement."
