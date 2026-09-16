using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Arc-Chord Ratio
    /// (ACR) rugosity geoprocessing tool dialog from the bundled Jenness
    /// Enterprises Python toolbox (Topographic Analysis Tools toolset). ACR
    /// (Du Preez 2015) is a surface-roughness index decoupled from slope --
    /// the 3-D surface area divided by the area of the cell's plane of best
    /// fit -- with an optional local-detrending step (Dilts et al. 2023). The
    /// tool itself lives in the .pyt so the same logic is available in
    /// ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class ACRButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("ArcChordRatio");
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
                    "Unable to open the Arc-Chord Ratio tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
