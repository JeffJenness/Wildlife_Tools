using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Aspect Tools-menu ribbon button that opens the Aspect Focal Statistics
    /// geoprocessing tool dialog from the bundled Jenness Enterprises Python
    /// toolbox. The tool computes neighborhood (focal) statistics on an aspect
    /// raster using CIRCULAR statistics (aspect is a circular variable, so
    /// ordinary Focal Statistics is invalid), needs no Spatial Analyst, and works
    /// on projected or geographic aspect rasters. It lives in the .pyt so the
    /// same logic is available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class AspectFocalStatisticsButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("AspectFocalStatistics");
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
                    "Unable to open the Aspect Focal Statistics tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
