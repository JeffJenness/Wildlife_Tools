using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Spatially Balanced
    /// Sample (GRTS) geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox: the generalized random-tessellation
    /// stratified design of Stevens &amp; Olsen (2004) -- hierarchical
    /// randomization maps the frame onto a line preserving spatial
    /// proximity, a systematic pass selects the sites, and reverse
    /// hierarchical ordering writes them so any leading run of SiteIDs
    /// is itself a spatially balanced sample, with replacement sites
    /// consumed in order.
    /// </summary>
    internal class GrtsButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath =
                    ToolboxPaths.ToolPath("SpatiallyBalancedSample");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife Tools");
                    return;
                }

                Geoprocessing.OpenToolDialog(
                    toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Spatially Balanced Sample (GRTS) tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
