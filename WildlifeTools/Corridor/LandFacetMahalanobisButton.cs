using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Corridor Designer Tools-group ribbon button that opens the Land Facet
    /// Mahalanobis geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox. It is the third step of the Land Facet analysis, after
    /// clustering and density: for each facet it builds a Mahalanobis distance
    /// cost surface, with the mean vector and covariance matrix taken from that
    /// facet's cells inside the Wildland Blocks, and writes one single-band
    /// raster per facet for least-cost corridor modelling.
    ///
    /// In the .pyt rather than a window of its own because it needs no
    /// interaction, so the same logic is available in ModelBuilder and the
    /// Geoprocessing pane.
    /// </summary>
    internal class LandFacetMahalanobisButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("LandFacetMahalanobis");
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
                    "Unable to open the Land Facet Mahalanobis tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
