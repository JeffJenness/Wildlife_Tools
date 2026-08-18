using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Evaluate Corridors-menu button that opens the interactive
    /// Bottleneck Analysis window (a single shared non-modal instance):
    /// pick the corridor and two wildland blocks, run the medial-axis
    /// maximin analysis, and explore the width profile with a live,
    /// draggable threshold linked to the map.
    /// </summary>
    internal sealed class BottleneckButton : Button
    {
        private static BottleneckWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null) { _window.Activate(); return; }
                _window = new BottleneckWindow
                { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Bottleneck Analysis window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "Bottleneck Analysis");
            }
        }
    }
}
