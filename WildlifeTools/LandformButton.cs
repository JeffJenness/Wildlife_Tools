using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Landform
    /// Classification geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox: classifies a DEM into landform categories (canyons,
    /// valleys, plains, slopes, ridges, peaks) from a small- and large-
    /// neighborhood TPI plus slope, using a saved classification system
    /// (e.g. Weiss 10-class). The tool lives in the .pyt so it is also
    /// available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class LandformButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("LandformClassification");
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
                    "Unable to open the Landform Classification tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
