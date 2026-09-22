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

using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using System.Windows;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Layouts;

namespace WildlifeTools.PolarPlots
{
    /// <summary>
    /// The suite-wide "Add to Layout" flow: charts and formatted statistics
    /// texts belong on LAYOUTS -- placed on a map they slow drawing down and
    /// their fonts and symbols draw at the wrong size. If a layout is the
    /// active view it is used directly; otherwise the user picks one of the
    /// project's layouts (a single layout is used without asking, and a
    /// project with none gets a pointer to Insert ▸ New Layout).
    /// </summary>
    internal static class LayoutPlacement
    {
        /// <summary>Place a chart model (and optionally a tagged statistics
        /// text beside it) on the resolved layout. Call from the UI thread;
        /// returns the status message, or null when the user canceled or no
        /// layout exists (a message is shown).</summary>
        public static async Task<string> AddChart(Window owner,
            PolarPlotModel model, string name, string taggedStatsText = null,
            string statsName = null)
        {
            var (layout, item) = ResolveTarget(owner);
            if (layout == null && item == null) return null;
            return await QueuedTask.Run(() =>
            {
                var target = layout ?? item.GetLayout();
                string msg = PolarPlotCim.PlaceOnLayout(model, target, name);
                if (!string.IsNullOrEmpty(taggedStatsText))
                    msg += " " + PolarPlotCim.PlaceStatsTextOnLayout(
                        target, taggedStatsText,
                        statsName ?? (name + " Statistics"));
                return msg;
            });
        }

        /// <summary>Place a tagged statistics text on the resolved layout.
        /// Call from the UI thread; returns the status message, or null when
        /// the user canceled or no layout exists.</summary>
        public static async Task<string> AddStatsText(Window owner,
            string taggedText, string name = "Statistics")
        {
            var (layout, item) = ResolveTarget(owner);
            if (layout == null && item == null) return null;
            return await QueuedTask.Run(() =>
                PolarPlotCim.PlaceStatsTextOnLayout(
                    layout ?? item.GetLayout(), taggedText, name));
        }

        /// <summary>Active layout, or a chosen project layout: exactly one
        /// of the pair is non-null on success; both null = canceled/none.</summary>
        private static (Layout layout, LayoutProjectItem item)
            ResolveTarget(Window owner)
        {
            var active = LayoutView.Active?.Layout;
            if (active != null) return (active, null);
            var items = Project.Current.GetItems<LayoutProjectItem>()
                .OrderBy(i => i.Name, StringComparer.CurrentCultureIgnoreCase)
                .ToList();
            if (items.Count == 0)
            {
                ArcGIS.Desktop.Framework.Dialogs.MessageBox.Show(
                    "The project has no layouts yet. Create a layout " +
                    "(Insert ▸ New Layout), then add the graphic to it.",
                    "Add to Layout");
                return (null, null);
            }
            if (items.Count == 1) return (null, items[0]);
            return (null, ChooseLayout(owner, items));
        }

        /// <summary>A small modal list of the project's layouts; returns
        /// the chosen item or null on cancel.</summary>
        private static LayoutProjectItem ChooseLayout(Window owner,
            List<LayoutProjectItem> items)
        {
            var list = new System.Windows.Controls.ListBox
            {
                ItemsSource = items.Select(i => i.Name).ToList(),
                SelectedIndex = 0,
                Margin = new Thickness(10, 10, 10, 6),
                MinHeight = 140,
            };
            var ok = new System.Windows.Controls.Button
            {
                Content = "Add to Layout",
                Padding = new Thickness(12, 3, 12, 3),
                Margin = new Thickness(0, 0, 8, 0),
                IsDefault = true,
            };
            var cancel = new System.Windows.Controls.Button
            {
                Content = "Cancel",
                Padding = new Thickness(12, 3, 12, 3),
                IsCancel = true,
            };
            var buttons = new System.Windows.Controls.StackPanel
            {
                Orientation = System.Windows.Controls.Orientation.Horizontal,
                HorizontalAlignment = HorizontalAlignment.Right,
                Margin = new Thickness(10, 0, 10, 10),
            };
            buttons.Children.Add(ok);
            buttons.Children.Add(cancel);
            var panel = new System.Windows.Controls.DockPanel();
            System.Windows.Controls.DockPanel.SetDock(buttons,
                System.Windows.Controls.Dock.Bottom);
            panel.Children.Add(buttons);
            panel.Children.Add(list);
            var dlg = new ArcGIS.Desktop.Framework.Controls.ProWindow
            {
                Title = "Add to which layout?",
                Owner = owner,
                Width = 360,
                Height = 300,
                WindowStartupLocation = WindowStartupLocation.CenterOwner,
                ShowInTaskbar = false,
                Content = panel,
            };
            ok.Click += (_, __) => dlg.DialogResult = true;
            list.MouseDoubleClick += (_, __) => dlg.DialogResult = true;
            if (dlg.ShowDialog() == true && list.SelectedIndex >= 0)
                return items[list.SelectedIndex];
            return null;
        }
    }
}
