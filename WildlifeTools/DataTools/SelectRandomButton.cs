using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.DataTools
{
    /// <summary>
    /// Data Tools ribbon button that opens the Select Random Records
    /// geoprocessing tool from the bundled Jenness Enterprises Python
    /// toolbox: random and systematic selection of features or table
    /// rows -- fixed counts, percentages, statistically derived sample
    /// sizes, probability-proportional-to-size, and every-Xth/odd/even
    /// designs, with the standard four selection methods.
    /// </summary>
    internal class SelectRandomButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath =
                    ToolboxPaths.ToolPath("SelectRandomRecords");
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
                    "Unable to open the Select Random Records tool:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
