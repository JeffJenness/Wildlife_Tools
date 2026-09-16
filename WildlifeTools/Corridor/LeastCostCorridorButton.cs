using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Corridor Designer Tools-group ribbon button that opens the
    /// Least-Cost Corridor geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox: combines cost-distance surfaces (from
    /// Invert Raster) and Wildland Block start/end points (from Identify
    /// Termini Polygons) into corridor polygons. A modernized, generalized
    /// port of the CorridorDesigner CorridorModel tool -- kept out of the
    /// Land Facet Corridor Modeling menu because its traditional and still
    /// most common use is a species habitat-suitability surface, not a
    /// land facet.
    /// </summary>
    internal class LeastCostCorridorButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("LeastCostCorridor");
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
                    "Unable to open the Least-Cost Corridor tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
