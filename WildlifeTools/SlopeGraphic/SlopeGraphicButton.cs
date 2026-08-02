using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.SlopeGraphic
{
    /// <summary>
    /// Ribbon button that opens the Slope Graphic window. ONE shared non-modal
    /// window (same pattern as Polar Plots), so the map keeps working while
    /// the graphic is designed; clicking again brings the window forward. The
    /// raster read cache lives on the window and dies with it.
    /// </summary>
    internal sealed class SlopeGraphicButton : Button
    {
        private static SlopeGraphicWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null) { _window.Activate(); return; }
                _window = new SlopeGraphicWindow
                { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();                     // non-modal
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Slope Graphic window:" + Environment.NewLine +
                    Environment.NewLine + ex.Message, "Slope Graphic");
            }
        }
    }
}
