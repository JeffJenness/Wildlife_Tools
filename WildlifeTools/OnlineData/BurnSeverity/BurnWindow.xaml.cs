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
using System.Collections.Generic;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Controls;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.OnlineData.BurnSeverity
{
    /// <summary>
    /// A Burn Severity window: a ProWindow shell around
    /// <see cref="BurnPanel"/>, for one program (MTBS, RAVG or BAER).
    /// Non-modal, one shared instance for each program. It tells the panel
    /// when the map changes, so the study-area list keeps up.
    /// </summary>
    public partial class BurnWindow : ProWindow
    {
        private static readonly Dictionary<string, BurnWindow> Instances = new Dictionary<string, BurnWindow>();
        private readonly List<Action> _unhook = new List<Action>();

        public BurnWindow() : this("MTBS")
        {
        }

        public BurnWindow(string program)
        {
            InitializeComponent();
            panel.SetProgram(program);
            Title = BurnActions.Title(panel.Program);
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            OnlineWindowFit.Apply(this);     // never larger than the screen
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

        internal static void ShowWindow(string program)
        {
            if (!Instances.TryGetValue(program, out BurnWindow w) || w == null)
            {
                w = new BurnWindow(program)
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                Instances[program] = w;
                w.Closed += (_, _) => Instances.Remove(program);
            }
            w.Show();
            w.Activate();
        }

        internal static void Open(string program)
        {
            try
            {
                ShowWindow(program);
            }
            catch (Exception ex)
            {
                MessageBox.Show("Could not open the " + BurnActions.Title(program) + " window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message, BurnActions.Title(program));
            }
        }
    }

    /// <summary>Data Tools group, Online Data: opens the MTBS Burn Severity window.</summary>
    internal sealed class MtbsBurnButton : Button
    {
        protected override void OnClick() => BurnWindow.Open("MTBS");
    }

    /// <summary>Data Tools group, Online Data: opens the RAVG Burn Severity window.</summary>
    internal sealed class RavgBurnButton : Button
    {
        protected override void OnClick() => BurnWindow.Open("RAVG");
    }

    /// <summary>Data Tools group, Online Data: opens the BAER Burn Severity window.</summary>
    internal sealed class BaerBurnButton : Button
    {
        protected override void OnClick() => BurnWindow.Open("BAER");
    }

    /// <summary>Data Tools group, Online Data: opens the Load Burn Severity
    /// Bundle geoprocessing tool's dialog.</summary>
    internal sealed class BurnBundleButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = global::WildlifeTools.ToolboxPaths.ToolPath("LoadBurnSeverityBundle");
                if (toolPath == null)
                {
                    MessageBox.Show("Could not find JennessEnterprisesTools.pyt next to the add-in.",
                        "Wildlife and Forestry Tools");
                    return;
                }
                Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray());
            }
            catch (Exception ex)
            {
                MessageBox.Show("Unable to open the Load Burn Severity Bundle tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
