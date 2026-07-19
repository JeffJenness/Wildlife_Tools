using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Cartography-group ribbon button that opens the Multi-Sun Hillshade
    /// geoprocessing tool dialog from the bundled Jenness Enterprises Python
    /// toolbox (Cartography Tools toolset): a weighted average of standard
    /// hillshades from two to eight sun positions. The tool itself lives in
    /// the .pyt so the same logic is available in ModelBuilder and the
    /// Geoprocessing pane.
    /// </summary>
    internal class MultiSunHillshadeButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("MultiSunHillshade");
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
                    "Unable to open the Multi-Sun Hillshade tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
