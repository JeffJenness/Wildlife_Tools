using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Concave Hull
    /// geoprocessing tool from the bundled Jenness Enterprises
    /// Python toolbox: the shape of a cloud of points by chi
    /// (characteristic) shapes, alpha shapes, or the
    /// k-nearest-neighbours hull.
    /// </summary>
    internal class ConcaveHullButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath =
                    ToolboxPaths.ToolPath("ConcaveHull");
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
                    "Unable to open the Concave Hull tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
