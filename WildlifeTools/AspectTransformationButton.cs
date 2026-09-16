using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Aspect Tools-menu ribbon button that opens the Aspect Transformation
    /// geoprocessing tool dialog from the bundled Jenness Enterprises Python
    /// toolbox. The tool generates one or more transformations of an aspect
    /// raster at once -- directional reclassifications (with a Class attribute
    /// table) and trigonometric transforms (northness, eastness, deviation from
    /// a bearing) -- with no Spatial Analyst. It lives in the .pyt so the same
    /// logic is available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class AspectTransformationButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("AspectTransformation");
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
                    "Unable to open the Aspect Transformation tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
