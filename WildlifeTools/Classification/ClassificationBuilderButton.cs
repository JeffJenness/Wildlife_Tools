using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Classification
{
    /// <summary>
    /// Ribbon button that opens the Classification System Builder: create,
    /// edit, duplicate, and delete the reusable classification systems (class
    /// rules, names, and colors) that the Slope Position / Landform / General
    /// Grid Classification geoprocessing tools apply. Systems are stored as
    /// JSON in a per-user location that survives add-in reinstalls.
    /// </summary>
    internal class ClassificationBuilderButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                var win = new ClassificationBuilderWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow
                };
                win.ShowDialog();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Classification System Builder:" +
                    Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
