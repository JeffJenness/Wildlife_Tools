using System;
using ArcGIS.Desktop.Framework.Contracts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// "Take a Break"-group ribbon button that opens the <see cref="EasterEggWindow"/>:
    /// a little tribute to the classic ArcMap "jerry" easter egg. Runs entirely on
    /// the UI thread; one shared non-modal window.
    /// </summary>
    internal sealed class EasterEggButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                EasterEggWindow.ShowEgg();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    $"Could not open the ArcMap Easter Egg window:{Environment.NewLine}{Environment.NewLine}{ex.Message}",
                    "ArcMap Easter Egg");
            }
        }
    }
}
