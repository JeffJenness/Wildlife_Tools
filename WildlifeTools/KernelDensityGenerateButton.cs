using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Ribbon button that opens the Kernel Density geoprocessing tool dialog from
    /// the bundled Jenness Enterprises Python toolbox.
    /// </summary>
    internal class KernelDensityGenerateButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("KernelDensityGenerate");
                if (toolPath == null)
                {
                    MessageBox.Show(
                        "Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife and Forestry Tools");
                    return;
                }

                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Kernel Density tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
