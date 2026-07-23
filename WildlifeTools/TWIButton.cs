using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Topographic
    /// Wetness Index geoprocessing tool dialog from the bundled Jenness
    /// Enterprises Python toolbox (Topographic Analysis Tools toolset):
    /// TWI = ln(specific catchment area / (tan(slope) + epsilon)), with sink
    /// filling, D8 flow direction, and flow accumulation computed internally
    /// (no Spatial Analyst). The tool itself lives in the .pyt so the same
    /// logic is available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class TWIButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("TopographicWetnessIndex");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife Tools");
                    return;
                }

                // Open the geoprocessing tool dialog with no preset parameter values.
                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Topographic Wetness Index tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
