using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Cartography-group ribbon button that opens the Blurred Hillshade
    /// geoprocessing tool dialog from the bundled Jenness Enterprises Python
    /// toolbox (Cartography Tools toolset): a deliberately softened hillshade,
    /// the blurring component of the Swiss method. The tool itself lives in
    /// the .pyt so the same logic is available in ModelBuilder and the
    /// Geoprocessing pane.
    /// </summary>
    internal class BlurredHillshadeButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("BlurredHillshade");
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
                    "Unable to open the Blurred Hillshade tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
