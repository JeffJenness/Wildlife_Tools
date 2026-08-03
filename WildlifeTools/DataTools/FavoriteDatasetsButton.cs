using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.DataTools
{
    /// <summary>
    /// Data Tools-group ribbon button that opens the Load Favorite Datasets
    /// window: four user-defined groups of favorite datasets (local or web),
    /// multi-checked loading into the active map, availability shown in red.
    /// ONE shared non-modal window (same pattern as Polar Plots / Sudoku).
    /// </summary>
    internal sealed class FavoriteDatasetsButton : Button
    {
        private static FavoriteDatasetsWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null) { _window.Activate(); return; }
                _window = new FavoriteDatasetsWindow
                { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();                     // non-modal
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Load Favorite Datasets window:" +
                    Environment.NewLine + Environment.NewLine + ex.Message,
                    "Load Favorite Datasets");
            }
        }
    }
}
