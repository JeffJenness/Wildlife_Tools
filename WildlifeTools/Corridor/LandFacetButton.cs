using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Corridor Designer Tools-group ribbon button that opens the Land Facet
    /// Clustering wizard: an all-in-Pro fuzzy c-means workflow that replaces the
    /// Land Facet Corridor Designer's export-to-R / import-from-R round trip. ONE
    /// shared non-modal window.
    /// </summary>
    internal sealed class LandFacetButton : Button
    {
        private static LandFacetWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null) { _window.Activate(); return; }
                _window = new LandFacetWindow
                { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Land Facet Clustering window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "Land Facet Clustering");
            }
        }
    }
}
