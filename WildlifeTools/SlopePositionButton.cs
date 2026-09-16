using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Slope Position
    /// Classification geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox: classifies a DEM into slope-position classes (valley,
    /// slope, ridge, ...) from a TPI plus slope, using a saved classification
    /// system. The tool lives in the .pyt so it is also available in
    /// ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class SlopePositionButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("SlopePositionClassification");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife and Forestry Tools");
                    return;
                }
                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Slope Position Classification tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
