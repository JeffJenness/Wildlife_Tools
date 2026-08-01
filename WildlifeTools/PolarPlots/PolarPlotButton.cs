using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// Ribbon button that opens the interactive Polar Plots window. ONE shared
    /// non-modal window (like the Sudoku / Thought for the Day cards), so the
    /// map keeps working while the plot is designed; clicking again brings the
    /// existing window forward.
    /// </summary>
    internal sealed class PolarPlotButton : Button
    {
        private static PolarPlotWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null) { _window.Activate(); return; }
                _window = new PolarPlotWindow
                { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();                     // non-modal
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Polar Plots window:" + Environment.NewLine +
                    Environment.NewLine + ex.Message, "Polar Plots");
            }
        }
    }
}
