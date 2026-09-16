using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using System;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// Graphics-group ribbon button: draws an (optionally rounded) rectangle graphic
    /// behind the currently selected element(s) — graphics or layout map elements, NOT
    /// dataset features — and groups it with the selection, rectangle underneath.
    /// Rectangle sibling of the Circle Background tool; opens
    /// <see cref="AddRectangleWindow"/> for the parameters, work done by
    /// <see cref="AddRectangleOperation"/>.
    /// </summary>
    internal sealed class RectangleBackgroundButton : Button
    {
        protected override async void OnClick()
        {
            try
            {
                // Snapshot the element selection on the MCT (shared with Circle Background).
                AddCircleContext ctx = await QueuedTask.Run(AddCircleOperation.Inspect);

                if (ctx != null && ctx.MultiContainer)
                {
                    MessageBox.Show(
                        "The selected elements are in more than one graphics layer. " +
                        "Select elements within a single graphics layer (or a single layout) and try again.",
                        "Rectangle Background");
                    return;
                }
                if (ctx == null || ctx.Selected.Count == 0 || ctx.Extent == null)
                {
                    MessageBox.Show(
                        "Select one or more graphic or layout elements first. " +
                        "This tool works on selected elements, not on features in a dataset.",
                        "Rectangle Background");
                    return;
                }

                // Collect parameters (UI thread, modal). Start from the symbol the user
                // last ran this tool with (persisted with the project), else the default.
                var startSymbol = AddCircleOperation.SavedSymbolOrNull(Module1.RectangleSymbolJson)
                                  ?? ctx.DefaultSymbol;
                var win = new AddRectangleWindow(startSymbol, ctx.IsLayout, ctx.MapUnitName,
                    ctx.MetersPerPageUnit, Module1.RectanglePrefs)
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                if (win.ShowDialog() != true)
                    return;

                // Serialize the chosen symbol BEFORE running: Run applies the
                // transparencies to the same symbol instance in place, and the stored
                // design should stay as the user built it.
                string symbolJson = win.Result.SymbolReference?.ToJson();

                await QueuedTask.Run(() => AddRectangleOperation.Run(ctx, win.Result));
                Module1.SaveBackgroundSymbol(Module1.BackgroundTool.Rectangle, symbolJson);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not add the rectangle background:" + Environment.NewLine + ex.Message,
                    "Rectangle Background");
            }
        }
    }
}
