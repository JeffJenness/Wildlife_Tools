using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.Linq;
using System.Threading.Tasks;
using System.Windows.Input;

namespace WildlifeTools
{
    /// <summary>
    /// Graphics-group tool for placing the Jennessent Compass Rose SVG as a single picture
    /// graphic element.
    ///
    /// * On a MAP: the built-in rectangle sketch works, so drag a box and the compass fills
    ///   it (kept square); then the tool returns to Explore.
    /// * On a LAYOUT: a MapTool's sketch cannot engage (and its cursor can't be controlled),
    ///   so it is a single-click placement at a default size, after which it hands off to
    ///   Esri's layout Select tool with the new element selected, ready to resize by handles.
    /// </summary>
    internal class CompassRoseGraphicTool : MapTool
    {
        public CompassRoseGraphicTool()
        {
            IsSketchTool = true;
            SketchType = SketchGeometryType.Rectangle;
            SketchOutputMode = SketchOutputMode.Map;
        }

        protected override Task OnToolActivateAsync(bool hasMapViewChanged)
        {
            // Sketch (draw-a-box) only works on a map; on a layout use click-to-place, and
            // turn the sketch off so no misleading draw-box cursor appears there.
            IsSketchTool = LayoutView.Active == null;
            return base.OnToolActivateAsync(hasMapViewChanged);
        }

        // ---- MAP path: built-in rectangle sketch ----
        protected override Task<bool> OnSketchCompleteAsync(Geometry geometry)
        {
            if (LayoutView.Active != null)
                return Task.FromResult(false);      // layouts handled by the mouse overrides
            return QueuedTask.Run(() =>
            {
                MapView mv = MapView.Active;
                Envelope env = geometry?.Extent;
                if (mv?.Map == null || env == null || env.Width <= 0 || env.Height <= 0)
                    return false;
                GraphicsLayer gl = mv.Map.GetLayersAsFlattenedList().OfType<GraphicsLayer>().FirstOrDefault()
                    ?? LayerFactory.Instance.CreateLayer<GraphicsLayer>(new GraphicsLayerCreationParams(), mv.Map);
                ElementFactory.Instance.CreatePictureGraphicElement(
                    gl, CompassRose.Square(env), CompassRose.SvgPath, "Jennessent Compass Rose");
                DeferSetTool("esri_mapping_exploreTool");
                return true;
            });
        }

        // ---- LAYOUT path: single-click placement at a default size ----
        protected override void OnToolMouseDown(MapViewMouseButtonEventArgs e)
        {
            if (LayoutView.Active != null && e.ChangedButton == MouseButton.Left)
                e.Handled = true;                   // claim the click so the layout doesn't pan
        }

        protected override void OnToolMouseUp(MapViewMouseButtonEventArgs e)
        {
            if (LayoutView.Active != null && e.ChangedButton == MouseButton.Left)
                e.Handled = true;
        }

        protected override async Task HandleMouseUpAsync(MapViewMouseButtonEventArgs e)
        {
            LayoutView lv = LayoutView.Active;
            if (lv?.Layout == null)
                return;
            System.Windows.Point clientPt = e.ClientPoint;
            try
            {
                await QueuedTask.Run(() =>
                {
                    // Identical points -> ClientBoxToPage returns a default-size box centered here.
                    Envelope env = CompassRose.ClientBoxToPage(lv, clientPt, clientPt);
                    var el = ElementFactory.Instance.CreatePictureGraphicElement(
                        lv.Layout, CompassRose.Square(env), CompassRose.SvgPath, "Jennessent Compass Rose");
                    try { lv.SelectElement(el); } catch { }   // show resize handles right away
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show("Unable to add the compass rose graphic:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
            // Hand off to Esri's layout Select tool so the user can resize the new element.
            DeferSetTool("esri_layouts_selectByRectangleTool");
        }

        // Switch tools on the dispatcher; awaiting a tool switch inside a tool handler
        // deadlocks the UI thread.
        private static void DeferSetTool(string toolId)
        {
            System.Windows.Application.Current.Dispatcher.BeginInvoke(new Action(() =>
                FrameworkApplication.SetCurrentToolAsync(toolId)));
        }
    }
}
