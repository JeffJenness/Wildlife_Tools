using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the VRM
    /// (Vector Ruggedness Measure) geoprocessing tool dialog from the
    /// bundled Jenness Enterprises Python toolbox (Topographic Analysis
    /// Tools toolset): terrain roughness from the 3-D dispersion of unit
    /// vectors orthogonal to the surface (Sappington et al. 2007). The tool
    /// itself lives in the .pyt so the same logic is available in
    /// ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class VRMButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("VRM");
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
                    "Unable to open the VRM tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
