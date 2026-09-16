using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Topographic Analysis-group ribbon button that opens the Split Lines into
    /// Topography Segments geoprocessing tool dialog from the bundled Jenness
    /// Enterprises Python toolbox (Topographic Analysis Tools toolset). The tool
    /// converts each polyline into a new PolylineZ feature class with one true
    /// 3-D feature per straight segment, carrying optional per-segment topography
    /// attributes and any chosen source fields. It lives in the .pyt so the same
    /// logic is available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class SplitLinesIntoSegmentsButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("SplitLinesIntoSegments");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife and Forestry Tools");
                    return;
                }

                // Open the geoprocessing tool dialog with no preset parameter values.
                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Split Lines into Topography Segments tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
