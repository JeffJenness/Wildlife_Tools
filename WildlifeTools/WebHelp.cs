using System;

namespace WildlifeTools
{
    /// <summary>
    /// Opens the Jenness Enterprises online help pages. Every custom
    /// window's Help button routes through here, so pointing the whole
    /// suite at the live site is a one-line change (BaseUrl).
    /// </summary>
    internal static class WebHelp
    {
        // Root of the online help site. Leave empty until the site is
        // hosted; the Help buttons then explain where the pages will
        // live instead of opening a dead link.
        internal const string BaseUrl = "";

        internal static void Open(string relativePage)
        {
            if (string.IsNullOrWhiteSpace(BaseUrl))
            {
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(
                    "The online help pages are being prepared and will "
                    + "be available at www.jennessent.com.",
                    "Wildlife and Forestry Tools help");
                return;
            }
            try
            {
                System.Diagnostics.Process.Start(
                    new System.Diagnostics.ProcessStartInfo(
                        BaseUrl.TrimEnd('/') + "/" + relativePage)
                    { UseShellExecute = true });
            }
            catch (Exception)
            {
                // A browser hiccup should never crash the window.
            }
        }
    }
}
