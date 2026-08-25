using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Radiating Lines
    /// and Points geoprocessing tool from the bundled Jenness
    /// Enterprises Python toolbox: evenly spaced lines radiating from
    /// every input point, optionally blocked by barrier polylines and
    /// polygons, with lines and/or origin-and-endpoint outputs.
    /// </summary>
    internal class RadiatingLinesButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath =
                    ToolboxPaths.ToolPath("RadiatingLines");
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
                    "Unable to open the Radiating Lines and Points tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
