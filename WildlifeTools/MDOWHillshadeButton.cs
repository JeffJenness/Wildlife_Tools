using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Cartography-group ribbon button that opens the MDOW Hillshade
    /// (Multi-Directional Oblique-Weighted, Mark 1992) geoprocessing tool dialog
    /// from the bundled Jenness Enterprises Python toolbox (Cartography Tools
    /// toolset). The tool itself lives in the .pyt so the same logic is
    /// available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class MDOWHillshadeButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("MDOWHillshade");
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
                    "Unable to open the MDOW Hillshade tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
