using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Corridor Designer Tools-group ribbon button that opens the Identify
    /// Termini Polygons geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox: the concentrations of a land facet (or of
    /// facet diversity) inside each Wildland Block, as the polygons the
    /// least-cost corridors will run between. One run of a multi-band Land
    /// Facet Density raster yields every facet's termini in a single feature
    /// class with a Facet field.
    /// </summary>
    internal class IdentifyTerminiButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("IdentifyTerminiPolygons");
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
                    "Unable to open the Identify Termini Polygons tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
