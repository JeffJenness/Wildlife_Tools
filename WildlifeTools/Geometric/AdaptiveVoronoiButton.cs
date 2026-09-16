using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;
using WildlifeTools.Corridor;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Adaptive Voronoi
    /// Polygons geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox: Lloyd's algorithm / centroidal Voronoi
    /// tessellation -- compact, nearly equal-area honeycomb partitions
    /// of a boundary polygon, at every license level.
    /// </summary>
    internal class AdaptiveVoronoiButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath =
                    ToolboxPaths.ToolPath("AdaptiveVoronoiPolygons");
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
                    "Unable to open the Adaptive Voronoi Polygons tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
