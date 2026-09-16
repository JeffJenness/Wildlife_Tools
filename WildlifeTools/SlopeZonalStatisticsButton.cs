using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Slope Zonal
    /// Statistics as Table geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox: the slope-targeted sibling of Esri's Zonal
    /// Statistics as Table, computing the mean/SD/CI on the slope ANGLES
    /// (percent slopes pass through the arctangent before averaging) and
    /// requiring no Spatial Analyst or Image Analyst extension. It lives in
    /// the .pyt so the same logic is available in ModelBuilder and the
    /// Geoprocessing pane.
    /// </summary>
    internal class SlopeZonalStatisticsButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("SlopeZonalStatisticsAsTable");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife and Forestry Tools");
                    return;
                }

                // Open the geoprocessing tool dialog with no preset parameter values.
                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Slope Zonal Statistics as Table tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
