using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Aspect Tools-menu ribbon button that opens the Aspect Zonal Statistics
    /// as Table geoprocessing tool from the bundled Jenness Enterprises Python
    /// toolbox: the circular analog of Esri's Zonal Statistics as Table,
    /// targeted at aspect rasters (circular statistics only, flat cells
    /// excluded and counted) and requiring no Spatial Analyst or Image
    /// Analyst extension. It lives in the .pyt so the same logic is available
    /// in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class AspectZonalStatisticsButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("AspectZonalStatisticsAsTable");
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
                    "Unable to open the Aspect Zonal Statistics as Table tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
