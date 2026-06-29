using System;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools
{
    /// <summary>
    /// Ribbon button that opens the Mahalanobis Distance at Points geoprocessing tool
    /// dialog from the bundled Jenness Enterprises Python toolbox.
    /// </summary>
    internal class MahalanobisPointsButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("MahalanobisPoints");
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
                    "Unable to open the Mahalanobis Distance (Points) tool:" + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
