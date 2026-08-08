using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Corridor Designer Tools-group ribbon button that opens the "Describe
    /// Land Facet Background" window (a single shared non-modal instance):
    /// pick a dataset and get a human-readable report of its sources, how they
    /// were analyzed, and where it sits in the Land Facet Corridor analysis
    /// sequence, with staleness warnings when a recorded source has since been
    /// regenerated.
    /// </summary>
    internal sealed class LandFacetDescribeButton : Button
    {
        private static LandFacetDescribeWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null) { _window.Activate(); return; }
                _window = new LandFacetDescribeWindow
                { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Describe Land Facet Background window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "Describe Land Facet Background");
            }
        }
    }
}
