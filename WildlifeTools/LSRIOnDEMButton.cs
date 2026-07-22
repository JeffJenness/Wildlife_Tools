using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the LSRI on DEM
    /// geoprocessing tool dialog from the bundled Jenness Enterprises Python
    /// toolbox (Topographic Analysis Tools toolset): the Land Surface
    /// Ruggedness Index (Beasom, Wiggers, and Giardino 1983), computed
    /// directly from a DEM by generating contours on the fly. The tool
    /// itself lives in the .pyt so the same logic is available in
    /// ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class LSRIOnDEMButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("LSRIOnDEM");
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
                    "Unable to open the LSRI on DEM tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
