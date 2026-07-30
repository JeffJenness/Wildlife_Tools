using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Topography along
    /// Lines geoprocessing tool dialog from the bundled Jenness Enterprises
    /// Python toolbox (Topographic Analysis Tools toolset). The tool adds
    /// topographic-roughness attribute fields (length, surface ratio, slope,
    /// direction, sinuosity, fractal dimension, and more) to an existing
    /// polyline feature class by sampling elevation along each line. The tool
    /// itself lives in the .pyt so the same logic is available in ModelBuilder
    /// and the Geoprocessing pane.
    /// </summary>
    internal class TopographyAlongLinesButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("TopographyAlongLines");
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
                    "Unable to open the Topography along Lines tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
