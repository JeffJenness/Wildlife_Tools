using ArcGIS.Desktop.Framework.Contracts;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Opens the Histograms and Habitat Suitability Statistics window
    /// (singleton -- clicking again brings the existing window forward, so
    /// a composed comparison is never lost to an accidental second click).
    /// </summary>
    internal class HistogramButton : Button
    {
        private static HistogramWindow _window;

        protected override void OnClick()
        {
            if (_window != null)
            {
                _window.Activate();
                return;
            }
            _window = new HistogramWindow
            {
                Owner = ArcGIS.Desktop.Framework.FrameworkApplication
                    .Current.MainWindow,
            };
            _window.Closed += (_, __) => _window = null;
            _window.Show();
        }
    }
}
