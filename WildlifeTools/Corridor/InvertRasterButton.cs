using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Corridor Designer Tools-group ribbon button that opens the Invert Raster
    /// geoprocessing tool from the bundled Jenness Enterprises Python toolbox.
    /// It turns a high-is-good surface (a land facet density raster, a
    /// diversity index) into a least-cost surface via the reciprocal
    /// 1/(x + c); a multi-band density raster yields one single-band cost
    /// raster per facet.
    /// </summary>
    internal class InvertRasterButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("InvertRaster");
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
                    "Unable to open the Invert Raster tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
