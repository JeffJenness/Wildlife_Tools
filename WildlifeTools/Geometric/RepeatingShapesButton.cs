using System;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;

namespace WildlifeTools.Geometric
{
    /// <summary>
    /// Geometric Tools ribbon button that opens the Repeating Shapes
    /// window -- the friendly image-gallery front end (only reachable
    /// from this button) that gathers the parameters and runs the real
    /// RepeatingShapes geoprocessing tool through the GP framework, so
    /// History and environments behave exactly as a pane run. The GP
    /// pane and ModelBuilder continue to use the standard tool dialog.
    /// </summary>
    internal class RepeatingShapesButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                RepeatingShapesWindow.ShowSingleton();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Unable to open the Repeating Shapes window:"
                    + Environment.NewLine + ex.Message,
                    "Wildlife Tools");
            }
        }
    }
}
