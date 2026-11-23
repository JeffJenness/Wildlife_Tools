// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, and
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
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Controls;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.OnlineData.Gbif
{
    /// <summary>
    /// The GBIF Species Occurrences window: a ProWindow shell around
    /// <see cref="GbifPanel"/>. Non-modal, one shared instance.
    /// </summary>
    public partial class GbifWindow : ProWindow
    {
        private static GbifWindow _instance;

        public GbifWindow()
        {
            InitializeComponent();
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            WindowFit.Apply(this);     // never larger than the screen
            panel.CloseRequested += (_, _) => Close();
            HookMapEvents();
            Closed += (_, _) =>
            {
                foreach (Action undo in _unhook)
                {
                    try { undo(); } catch (Exception) { }
                }
                _unhook.Clear();
            };
        }

        private readonly System.Collections.Generic.List<Action> _unhook = new System.Collections.Generic.List<Action>();

        /// <summary>The study-area list follows the map: layers added or
        /// removed, another map activated, the selection changed.</summary>
        private void HookMapEvents()
        {
            try
            {
                var t1 = ArcGIS.Desktop.Mapping.Events.LayersAddedEvent.Subscribe(_ => panel.MapChanged());
                _unhook.Add(() => ArcGIS.Desktop.Mapping.Events.LayersAddedEvent.Unsubscribe(t1));
                var t2 = ArcGIS.Desktop.Mapping.Events.LayersRemovedEvent.Subscribe(_ => panel.MapChanged());
                _unhook.Add(() => ArcGIS.Desktop.Mapping.Events.LayersRemovedEvent.Unsubscribe(t2));
                var t3 = ArcGIS.Desktop.Mapping.Events.ActiveMapViewChangedEvent.Subscribe(_ => panel.MapChanged());
                _unhook.Add(() => ArcGIS.Desktop.Mapping.Events.ActiveMapViewChangedEvent.Unsubscribe(t3));
                var t4 = ArcGIS.Desktop.Mapping.Events.MapSelectionChangedEvent.Subscribe(_ => panel.MapChanged());
                _unhook.Add(() => ArcGIS.Desktop.Mapping.Events.MapSelectionChangedEvent.Unsubscribe(t4));
            }
            catch (Exception)
            {
                // without the events the Refresh button beside the list still works
            }
            // The panel keeps the polygons of a study-area layer for a short while. An edit, or a change of a
            // layer's properties (its definition query, its source), can change them without changing the
            // list of layers: the panel is told, and reads them again when it next needs them.
            try
            {
                var t5 = ArcGIS.Desktop.Editing.Events.EditCompletedEvent.Subscribe(_ =>
                {
                    panel.AreaMayHaveChanged();
                    return System.Threading.Tasks.Task.CompletedTask;
                });
                _unhook.Add(() => ArcGIS.Desktop.Editing.Events.EditCompletedEvent.Unsubscribe(t5));
                var t6 = ArcGIS.Desktop.Mapping.Events.MapMemberPropertiesChangedEvent.Subscribe(_ => panel.AreaMayHaveChanged());
                _unhook.Add(() => ArcGIS.Desktop.Mapping.Events.MapMemberPropertiesChangedEvent.Unsubscribe(t6));
            }
            catch (Exception)
            {
                // the polygons are read again after a minute in any case
            }
        }

        internal static void ShowWindow()
        {
            if (_instance == null)
            {
                _instance = new GbifWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                _instance.Closed += (_, _) => _instance = null;
            }
            _instance.Show();
            _instance.Activate();
        }
    }

    /// <summary>Data Tools group: opens the GBIF Species Occurrences window.</summary>
    internal sealed class GbifButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                GbifWindow.ShowWindow();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the GBIF Species Occurrences window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "GBIF Species Occurrences");
            }
        }
    }
}
