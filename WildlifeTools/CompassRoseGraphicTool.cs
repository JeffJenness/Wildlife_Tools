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

using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Dialogs;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Internal.Layouts.Ribbon;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.Linq;
using System.Threading.Tasks;
using WildlifeTools.CustomGraphics;

namespace WildlifeTools
{
    /// <summary>
    /// The Add Graphics tool: places a picture as a single graphic element on a layout or
    /// on a map's graphics layer. WHICH picture is whatever the Add Graphics gallery
    /// chose last (<see cref="GraphicLibrary.Current"/>): one of the add-in's compass
    /// designs or an image the user registered. The tool is the top half of the Add
    /// Graphics split button; the gallery below it sets the choice and activates the
    /// tool (the same arrangement as Esri's Insert &gt; North Arrow).
    ///
    /// Built on Esri's own element tool base,
    /// ArcGIS.Desktop.Internal.Layouts.Ribbon.NewElementToolBase — the same class behind
    /// Insert &gt; Rectangle etc. Unlike a MapTool, it drives the REAL element sketch:
    /// live rubber-band box, proper insert cursor, single-click placement, and it works on
    /// layouts with no map frame (where a MapTool receives no mouse events at all).
    /// NOTE: the base class is public but lives in an Internal namespace — undocumented and
    /// unsupported, so revalidate after each ArcGIS Pro upgrade.
    /// </summary>
    internal class CompassRoseGraphicTool : NewElementToolBase
    {
        /// <summary>The artwork to place: the Add Graphics gallery's current choice.</summary>
        protected virtual CompassArt Art =>
            CompassArt.From(GraphicLibrary.Current(GraphicKind.Graphic));

        public CompassRoseGraphicTool()
            : base(SketchGeometryType.Rectangle, ServiceContracts.LayoutElementType.Picture,
                   CompassRose.InsertCursor, "Add Graphic")
        {
        }

        protected override async Task OnActivateImpl(bool hasViewChanged)
        {
            // On a map view the element sketch targets the map's graphics layer; make sure
            // one exists (creating it if needed) so the tool works out of the box.
            if (LayoutView.Active == null && MapView.Active?.Map is Map map)
            {
                try
                {
                    await QueuedTask.Run(() =>
                    {
                        if (map.TargetGraphicsLayer == null)
                        {
                            GraphicsLayer gl =
                                map.GetLayersAsFlattenedList().OfType<GraphicsLayer>().FirstOrDefault()
                                ?? LayerFactory.Instance.CreateLayer<GraphicsLayer>(
                                       new GraphicsLayerCreationParams(), map);
                            map.TargetGraphicsLayer = gl;
                        }
                    });
                }
                catch
                {
                }
            }
            await base.OnActivateImpl(hasViewChanged);
        }

        protected override async Task<bool> OnSketchFinished(
            ArcGIS.Desktop.Internal.Mapping.View.ViewSketchCompleteEventArgs e)
        {
            if (e != null && e.GeometryType != SketchType)
                return false;                       // mirrors the base class's guard
            string geomStr = e?.GeometryString;
            try
            {
                await QueuedTask.Run(() =>
                {
                    CompassArt art = Art;       // one choice for the whole placement
                    // Geometry XML (e.g. <PolygonN>), not JSON — see SketchGeometryFromString.
                    Geometry sketch = CompassRose.SketchGeometryFromString(geomStr);
                    Envelope box = sketch?.Extent;

                    LayoutView lv = LayoutView.Active;
                    if (lv?.Layout is Layout layout)
                    {
                        box = CompassRose.DefaultIfTiny(box, layout);
                        var el = ElementFactory.Instance.CreatePictureGraphicElement(
                            layout, CompassRose.FitAspect(box, art.Aspect), art.ArtPath, art.GraphicName);
                        // The artwork's focal point (a compass's hub), for the
                        // Circle Background tool.
                        CompassRose.StampFocalPoint(el, art.FocalX, art.FocalY);
                        try { lv.SelectElement(el); } catch { }
                    }
                    else if (MapView.Active?.Map is Map map)
                    {
                        GraphicsLayer gl = map.TargetGraphicsLayer
                            ?? map.GetLayersAsFlattenedList().OfType<GraphicsLayer>().FirstOrDefault();
                        if (gl == null)
                            return;
                        // A bare click gives a degenerate box; default to view extent / 15.
                        Envelope ve = MapView.Active.Extent;
                        double longSide = Math.Max(ve.Width, ve.Height);
                        if (box == null || box.Width < longSide * 0.005 || box.Height < longSide * 0.005)
                        {
                            double def = longSide / 15.0;
                            double cx = box != null ? (box.XMin + box.XMax) / 2.0 : (ve.XMin + ve.XMax) / 2.0;
                            double cy = box != null ? (box.YMin + box.YMax) / 2.0 : (ve.YMin + ve.YMax) / 2.0;
                            box = EnvelopeBuilderEx.CreateEnvelope(
                                cx - def / 2.0, cy - def / 2.0, cx + def / 2.0, cy + def / 2.0,
                                ve.SpatialReference);
                        }
                        var el = ElementFactory.Instance.CreatePictureGraphicElement(
                            gl, CompassRose.FitAspect(box, art.Aspect), art.ArtPath, art.GraphicName);
                        CompassRose.StampFocalPoint(el, art.FocalX, art.FocalY);
                    }
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show("Unable to add the graphic:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
            // Same post-insert behavior as Esri's element tools (their FinishSketch does
            // exactly this): switch to the element Select tool so the user can move/resize.
            ArcGIS.Desktop.Framework.FrameworkApplication.CurrentTool = "esri_layouts_selectByRectangleTool";
            return true;
        }
    }
}
