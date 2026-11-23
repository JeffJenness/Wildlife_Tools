// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, use of names,
// and an additional permission to link with ArcGIS Pro).
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
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Controls;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.OnlineData.INaturalist
{
    /// <summary>
    /// The iNaturalist Observations window: a ProWindow shell around
    /// <see cref="INatPanel"/>. Non-modal, one shared instance. It tells
    /// the panel when the map changes, so the study-area list keeps up.
    /// </summary>
    public partial class INatWindow : ProWindow
    {
        private static INatWindow _instance;
        private readonly List<Action> _unhook = new List<Action>();

        public INatWindow()
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
        }

        internal static void ShowWindow()
        {
            if (_instance == null)
            {
                _instance = new INatWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                _instance.Closed += (_, _) => _instance = null;
            }
            _instance.Show();
            _instance.Activate();
        }
    }

    /// <summary>Data Tools group, Online Data: opens the iNaturalist Observations window.</summary>
    internal sealed class INatButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                INatWindow.ShowWindow();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the iNaturalist Observations window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "iNaturalist Observations");
            }
        }
    }
}
