using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Corridor Designer Tools-group ribbon button that opens the Land Facet
    /// Density geoprocessing tool from the bundled Jenness Enterprises Python
    /// toolbox. It follows the Land Facet Clustering wizard in the analysis: for
    /// every land facet it measures the proportion of the surrounding
    /// neighborhood composed of that facet, as one multi-band raster with a band
    /// per facet. That surface both places the corridor termini and supplies a
    /// variable to the Mahalanobis cost surface.
    ///
    /// It lives in the .pyt rather than in a window of its own because it needs
    /// no interaction -- raster in, raster out -- so the same logic is available
    /// in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class LandFacetDensityButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("LandFacetDensity");
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
                    "Unable to open the Land Facet Density tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
