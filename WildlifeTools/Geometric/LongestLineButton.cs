using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Longest Line in
    /// Polygon (Fetch) geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox: the longest straight line fully
    /// inside each polygon (the fetch on water bodies), or directional
    /// fetch rays from station points.
    /// </summary>
    internal class LongestLineButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath =
                    ToolboxPaths.ToolPath("LongestLineFetch");
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
                    "Unable to open the Longest Line in Polygon (Fetch) tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
