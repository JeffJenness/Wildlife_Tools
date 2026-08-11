using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Opens the non-modal "Create Reclass Tables for the Create Habitat
    /// Suitability Model Tool" window: an interactive remap builder showing
    /// the chosen raster's histogram and statistics, translucent coverage
    /// washes as remap rows are entered, and a save step that writes an
    /// ASCII remap file next to a file raster or a geodatabase remap table
    /// next to a geodatabase raster -- both accepted directly by the Create
    /// Habitat Suitability Model geoprocessing tool.
    /// </summary>
    internal class HsmRemapButton : Button
    {
        private HsmRemapWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null)
                {
                    _window.Activate();
                    return;
                }
                _window = new HsmRemapWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                _window.Closed += (s, e) => _window = null;
                _window.Show();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the remap table builder:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
