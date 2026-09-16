using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Aspect Tools-menu ribbon button that opens the Extract Aspect Values to
    /// Points geoprocessing tool dialog from the bundled Jenness Enterprises
    /// Python toolbox. The tool copies the input points and adds a field with
    /// the aspect at each point -- the containing cell's value, or a bilinear
    /// weighted-mean-direction (circular) interpolation -- with no Spatial
    /// Analyst. It lives in the .pyt so the same logic is available in
    /// ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class ExtractAspectValuesToPointsButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("ExtractAspectValuesToPoints");
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
                    "Unable to open the Extract Aspect Values to Points tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
