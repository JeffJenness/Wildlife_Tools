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
    /// Cartography-group tool: single-click on the active layout to add a north arrow whose
    /// marker symbol is the Jennessent Compass Rose, at a default size, tied to the layout's
    /// map frame. The new arrow is selected and the tool hands off to Esri's layout Select
    /// tool so it can be resized by its handles immediately. Enabled only in a layout view
    /// via the built-in "esri_layouts_layoutPaneCondition" (see Config.daml).
    /// </summary>
    internal class CompassRoseNorthArrowTool : MapTool
    {
        public CompassRoseNorthArrowTool()
        {
            // Click-to-place (no draw-box sketch, which a MapTool cannot drive on a layout).
            IsSketchTool = false;
        }

        protected override void OnToolMouseDown(MapViewMouseButtonEventArgs e)
        {
            if (e.ChangedButton == MouseButton.Left)
                e.Handled = true;                   // claim the click so the layout doesn't pan
        }

        protected override void OnToolMouseUp(MapViewMouseButtonEventArgs e)
        {
            if (e.ChangedButton == MouseButton.Left)
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
                    Layout layout = lv.Layout;
                    MapFrame mapFrame = lv.ActiveMapFrame
                        ?? layout.GetElementsAsFlattenedList().OfType<MapFrame>().FirstOrDefault();
                    if (mapFrame == null)
                        throw new InvalidOperationException("The layout has no map frame to attach a north arrow to.");
                    MapPoint center = MapPointBuilderEx.CreateMapPoint(
                        (env.XMin + env.XMax) / 2.0, (env.YMin + env.YMax) / 2.0);
                    var na = CompassRose.CreateNorthArrow(layout, mapFrame, center, env.Height);
                    try { if (na != null) lv.SelectElement(na); } catch { }   // show resize handles
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show("Unable to add the compass north arrow:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
            // Hand off to Esri's layout Select tool so the user can resize the new arrow.
            System.Windows.Application.Current.Dispatcher.BeginInvoke(new Action(() =>
                FrameworkApplication.SetCurrentToolAsync("esri_layouts_selectByRectangleTool")));
        }
    }
}
