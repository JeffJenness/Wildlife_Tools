using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;
using WildlifeTools.Corridor;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Voronoi (Thiessen)
    /// Polygons within Boundary geoprocessing tool from the bundled
    /// Jenness Enterprises Python toolbox: Thiessen polygons clipped to a
    /// boundary polygon, exact-on-the-sphere for geographic data, at
    /// every license level.
    /// </summary>
    internal class VoronoiButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("VoronoiPolygons");
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
                    "Unable to open the Voronoi (Thiessen) Polygons tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
