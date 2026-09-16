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
    /// Cartography-group tool: draw a box (or single-click for a default size) on the active
    /// layout to add a north arrow whose marker symbol is the Jennessent Compass Rose, tied
    /// to the layout's map frame (or unassociated, "Map frame: &lt;None&gt;", if the layout
    /// has none yet). Enabled only in a layout view via "esri_layouts_layoutPaneCondition".
    ///
    /// Built on Esri's own element tool base,
    /// ArcGIS.Desktop.Internal.Layouts.Ribbon.NewElementToolBase (see
    /// <see cref="CompassRoseGraphicTool"/> for why — real sketch, works on frameless
    /// layouts). Public but undocumented API: revalidate after each Pro upgrade.
    /// </summary>
    internal class CompassRoseNorthArrowTool : NewElementToolBase
    {
        /// <summary>Which compass artwork this tool places; subclasses override to supply
        /// a variant (see <see cref="CompassRoseDotNorthArrowTool"/>).</summary>
        protected virtual CompassArt Art => CompassArt.Classic;

        public CompassRoseNorthArrowTool()
            : base(SketchGeometryType.Rectangle, ServiceContracts.LayoutElementType.MarkerNorthArrow,
                   CompassRose.InsertCursor, "Compass North Arrow")
        {
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
                    LayoutView lv = LayoutView.Active;
                    if (lv?.Layout is not Layout layout)
                        return;
                    // Geometry XML (e.g. <PolygonN>), not JSON — see SketchGeometryFromString.
                    Geometry sketch = CompassRose.SketchGeometryFromString(geomStr);
                    Envelope box = CompassRose.DefaultIfTiny(sketch?.Extent, layout);
                    Envelope env = CompassRose.FitAspect(box, Art.Aspect);

                    // A null map frame is fine: the arrow is created unassociated and the
                    // user can tie it to a map frame later via Element Properties.
                    MapFrame mapFrame = null;
                    try
                    {
                        mapFrame = lv.ActiveMapFrame
                            ?? layout.GetElementsAsFlattenedList().OfType<MapFrame>().FirstOrDefault();
                    }
                    catch
                    {
                    }
                    MapPoint center = MapPointBuilderEx.CreateMapPoint(
                        (env.XMin + env.XMax) / 2.0, (env.YMin + env.YMax) / 2.0);
                    var na = CompassRose.CreateNorthArrow(layout, mapFrame, center, env.Height, Art);
                    try { if (na != null) lv.SelectElement(na); } catch { }
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show("Unable to add the compass north arrow:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
            // Same post-insert behavior as Esri's element tools (their FinishSketch does
            // exactly this): switch to the element Select tool so the user can move/resize.
            ArcGIS.Desktop.Framework.FrameworkApplication.CurrentTool = "esri_layouts_selectByRectangleTool";
            return true;
        }
    }
}
