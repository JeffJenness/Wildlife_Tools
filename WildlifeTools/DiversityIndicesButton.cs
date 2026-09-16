using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Corridor Designer Tools-group ribbon button that opens the Diversity
    /// Indices geoprocessing tool from the bundled Jenness Enterprises Python
    /// toolbox. The tool computes neighborhood (focal) diversity of a categorical
    /// raster -- the effective number of species (Hill numbers), Shannon's index,
    /// or Simpson's measures -- over any neighborhood shape, on projected or
    /// geographic rasters, with no Spatial Analyst. It lives in the .pyt so the
    /// same logic is available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class DiversityIndicesButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("DiversityIndices");
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
                    "Unable to open the Diversity Indices tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
