using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Find Raster
    /// Extreme Values geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox: the peaks and low points of a DEM as a point feature
    /// class, with topographic prominence / depression depth and an
    /// edge-of-data flag. Lives in the .pyt so the same logic is available in
    /// ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class FindRasterExtremesButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("FindRasterExtremeValues");
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
                    "Unable to open the Find Raster Extreme Values tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
