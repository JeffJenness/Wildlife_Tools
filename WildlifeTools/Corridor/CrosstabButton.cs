using ArcGIS.Desktop.Framework.Contracts;

namespace WildlifeTools.Corridor
{
    /// <summary>Opens the Cross-Tabulation window (singleton).</summary>
    internal class CrosstabButton : Button
    {
        private static CrosstabWindow _window;

        protected override void OnClick()
        {
            if (_window != null)
            {
                _window.Activate();
                return;
            }
            _window = new CrosstabWindow
            {
                Owner = ArcGIS.Desktop.Framework.FrameworkApplication
                    .Current.MainWindow,
            };
            _window.Closed += (_, __) => _window = null;
            _window.Show();
        }
    }
}
