using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the TRI (Terrain
    /// Ruggedness Index) geoprocessing tool dialog from the bundled Jenness
    /// Enterprises Python toolbox (Topographic Analysis Tools toolset): a
    /// neighborhood ruggedness measure with four selectable methods (Riley
    /// et al. 1999; Wilson et al. 2007; a slope/distance-weighted variant;
    /// or neighborhood standard deviation). The tool itself lives in the .pyt
    /// so the same logic is available in ModelBuilder and the Geoprocessing
    /// pane.
    /// </summary>
    internal class TRIButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("TRI");
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
                    "Unable to open the TRI tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
