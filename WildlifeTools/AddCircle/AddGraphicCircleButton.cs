using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using System;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// Graphics-group ribbon button: draws a circle graphic behind the currently
    /// selected element(s) — graphics or layout map elements, NOT dataset features —
    /// and groups the circle with the selection into a new group element, circle
    /// underneath. Opens <see cref="AddCircleWindow"/> for the parameters; the work is
    /// done by <see cref="AddCircleOperation"/>.
    /// </summary>
    internal sealed class AddGraphicCircleButton : Button
    {
        protected override async void OnClick()
        {
            try
            {
                // Snapshot the element selection on the MCT (extent, groups, focal point).
                AddCircleContext ctx = await QueuedTask.Run(AddCircleOperation.Inspect);

                if (ctx != null && ctx.MultiContainer)
                {
                    MessageBox.Show(
                        "The selected elements are in more than one graphics layer. " +
                        "Select elements within a single graphics layer (or a single layout) and try again.",
                        "Circle Background");
                    return;
                }
                if (ctx == null || ctx.Selected.Count == 0 || ctx.Extent == null)
                {
                    MessageBox.Show(
                        "Select one or more graphic or layout elements first. " +
                        "This tool works on selected elements, not on features in a dataset.",
                        "Circle Background");
                    return;
                }

                // Collect parameters (UI thread, modal).
                var win = new AddCircleWindow(ctx.SingleHasFocal, ctx.DefaultSymbol)
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                if (win.ShowDialog() != true)
                    return;

                await QueuedTask.Run(() => AddCircleOperation.Run(ctx, win.Result));
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not add the circle background:" + Environment.NewLine + ex.Message,
                    "Circle Background");
            }
        }
    }
}
