using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Ancillary Corridor Tools ribbon button that opens the Cumulative
    /// Surface geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox. Any mix of polygon layers and rasters (dragged into
    /// the dialog together) becomes one integer raster counting how many of
    /// those layers occur on each cell -- how many species' corridors or
    /// patches would be served by preserving that ground.
    /// </summary>
    internal class CumulativeSurfaceButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("CumulativeSurface");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife and Forestry Tools");
                    return;
                }

                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Cumulative Surface tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
