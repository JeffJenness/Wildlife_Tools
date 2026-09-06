using ArcGIS.Desktop.Framework.Contracts;

namespace WildlifeTools
{
    /// <summary>
    /// Opens the LoCoH Parameter Explorer window (singleton -- clicking
    /// again brings the existing window forward, so a computed sweep is
    /// never lost to an accidental second click).
    /// </summary>
    internal class LocohParameterButton : Button
    {
        private static LocohParameterWindow _window;

        protected override void OnClick()
        {
            if (_window != null)
            {
                _window.Activate();
                return;
            }
            _window = new LocohParameterWindow
            {
                Owner = ArcGIS.Desktop.Framework.FrameworkApplication
                    .Current.MainWindow,
            };
            _window.Closed += (_, __) => _window = null;
            _window.Show();
        }
    }
}
