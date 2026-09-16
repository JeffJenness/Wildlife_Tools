using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Random Sample Array
    /// Generator geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox: randomly placed arrays of sampling points (grid,
    /// triangular, circular, or one-dimensional along a polyline) built
    /// around random focal locations, following the Random Point
    /// Generator's stratified sampling design, with footprint spacing
    /// and containment constraints and a reproducibility seed.
    /// </summary>
    internal class RandomArraysButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath =
                    ToolboxPaths.ToolPath("RandomSampleArrayGenerator");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife and Forestry Tools");
                    return;
                }

                Geoprocessing.OpenToolDialog(
                    toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Random Sample Array Generator tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
