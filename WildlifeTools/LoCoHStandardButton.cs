using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Ribbon button that opens the Standard LoCoH geoprocessing tool dialog from the
    /// bundled Jenness Enterprises Python toolbox. The tool itself lives in the .pyt so
    /// the same logic is available in ModelBuilder and the Geoprocessing pane.
    /// </summary>
    internal class LoCoHStandardButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("LoCoHStandard");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife Tools");
                    return;
                }

                // Open the geoprocessing tool dialog with no preset parameter values.
                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the LoCoH (Standard) tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
