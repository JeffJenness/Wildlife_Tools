// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Scholarship Fund notice, marking of modified versions, and
// use of names).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using System;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools
{
    /// <summary>
    /// Graphics-group ribbon button: draws a polygon graphic that fits the actual
    /// shape of the currently selected element(s) — optionally buffered — behind the
    /// selection, and groups it with them, polygon underneath. Shape-fitting sibling
    /// of the Circle and Rectangle Background tools; opens
    /// <see cref="AddPolygonWindow"/> for the parameters, work done by
    /// <see cref="AddPolygonOperation"/>.
    /// </summary>
    internal sealed class PolygonBackgroundButton : Button
    {
        protected override async void OnClick()
        {
            try
            {
                // Snapshot the element selection on the MCT (shared with the other tools).
                AddCircleContext ctx = await QueuedTask.Run(AddCircleOperation.Inspect);

                if (ctx != null && ctx.MultiContainer)
                {
                    MessageBox.Show(
                        "The selected elements are in more than one graphics layer. " +
                        "Select elements within a single graphics layer (or a single layout) and try again.",
                        "Polygon Background");
                    return;
                }
                if (ctx == null || ctx.Selected.Count == 0)
                {
                    MessageBox.Show(
                        "Select one or more graphic or layout elements first. " +
                        "This tool works on selected elements, not on features in a dataset.",
                        "Polygon Background");
                    return;
                }

                // Collect parameters (UI thread, modal). Start from the symbol the user
                // last ran this tool with (persisted with the project), else the default.
                var startSymbol = AddCircleOperation.SavedSymbolOrNull(Module1.PolygonSymbolJson)
                                  ?? ctx.DefaultSymbol;
                var win = new AddPolygonWindow(startSymbol, ctx.IsLayout, ctx.MapUnitName,
                    ctx.MetersPerPageUnit, Module1.PolygonPrefs,
                    Math.Max(ctx.Extent.Width, ctx.Extent.Height) * 0.5)
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                if (win.ShowDialog() != true)
                    return;

                // Serialize the chosen symbol BEFORE running: Run applies the
                // transparencies to the same symbol instance in place, and the stored
                // design should stay as the user built it.
                string symbolJson = win.Result.SymbolReference?.ToJson();

                await QueuedTask.Run(() => AddPolygonOperation.Run(ctx, win.Result));
                Module1.SaveBackgroundSymbol(Module1.BackgroundTool.Polygon, symbolJson);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not add the polygon background:" + Environment.NewLine + ex.Message,
                    "Polygon Background");
            }
        }
    }
}
