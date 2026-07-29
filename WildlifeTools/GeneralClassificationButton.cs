using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the General Grid
    /// Classification geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox: classifies any single raster into categories using a
    /// saved 'general' classification system (threshold rules on the grid's
    /// raw or standardized values). The tool lives in the .pyt so it is also
    /// available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class GeneralClassificationButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("GeneralRasterClassification");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife Tools");
                    return;
                }
                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the General Raster Classification tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
