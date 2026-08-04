using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.DataTools
{
    /// <summary>
    /// Data Tools-group ribbon button that opens the Attribute Summary
    /// Reports window: counts and proportions of attribute value classes,
    /// with a user-designed per-line format, ported from the SSI ArcMap
    /// "Summarize Attribute Fields" tool. ONE shared non-modal window.
    /// </summary>
    internal sealed class AttributeSummaryButton : Button
    {
        private static AttributeSummaryWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null) { _window.Activate(); return; }
                _window = new AttributeSummaryWindow
                { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();                     // non-modal
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Attribute Summary Reports window:" +
                    Environment.NewLine + Environment.NewLine + ex.Message,
                    "Attribute Summary Reports");
            }
        }
    }
}
