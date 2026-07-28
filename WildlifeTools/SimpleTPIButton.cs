using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Topographic
    /// Position Index (TPI) geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox: each cell's elevation minus the mean
    /// elevation of its neighborhood, with optional raw and standardized
    /// (DEM-scale / neighborhood-scale) outputs. The tool lives in the .pyt so
    /// it is also available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class SimpleTPIButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("SimpleTPI");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife Tools");
                    return;
                }
                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Topographic Position Index tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
