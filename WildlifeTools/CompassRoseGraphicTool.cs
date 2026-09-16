using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Dialogs;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Internal.Layouts.Ribbon;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.Linq;
using System.Threading.Tasks;

namespace WildlifeTools
{
    /// <summary>
    /// Graphics-group tool for placing the Jennessent Compass Rose SVG as a single picture
    /// graphic element on a layout or a map's graphics layer.
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
        /// <summary>Which compass artwork this tool places; subclasses override to supply
        /// a variant (see <see cref="CompassRoseDotGraphicTool"/>).</summary>
        protected virtual CompassArt Art => CompassArt.Classic;

        public CompassRoseGraphicTool()
            : base(SketchGeometryType.Rectangle, ServiceContracts.LayoutElementType.Picture,
                   CompassRose.InsertCursor, "Compass Rose")
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
                    // Geometry XML (e.g. <PolygonN>), not JSON — see SketchGeometryFromString.
                    Geometry sketch = CompassRose.SketchGeometryFromString(geomStr);
                    Envelope box = sketch?.Extent;

                    LayoutView lv = LayoutView.Active;
                    if (lv?.Layout is Layout layout)
                    {
                        box = CompassRose.DefaultIfTiny(box, layout);
                        var el = ElementFactory.Instance.CreatePictureGraphicElement(
                            layout, CompassRose.FitAspect(box, Art.Aspect), Art.SvgPath, Art.GraphicName);
                        // Compass center, for relative-placement tools.
                        CompassRose.StampFocalPoint(el, Art.FocalX, Art.FocalY);
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
                            gl, CompassRose.FitAspect(box, Art.Aspect), Art.SvgPath, Art.GraphicName);
                        CompassRose.StampFocalPoint(el, Art.FocalX, Art.FocalY);
                    }
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show("Unable to add the compass rose graphic:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
            // Same post-insert behavior as Esri's element tools (their FinishSketch does
            // exactly this): switch to the element Select tool so the user can move/resize.
            ArcGIS.Desktop.Framework.FrameworkApplication.CurrentTool = "esri_layouts_selectByRectangleTool";
            return true;
        }
    }
}
