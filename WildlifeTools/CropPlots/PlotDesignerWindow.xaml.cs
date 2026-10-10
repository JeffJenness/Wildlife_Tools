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
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Controls;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// The Plot Designer window: a ProWindow shell around
    /// <see cref="PlotDesignerPanel"/>. Non-modal, one shared instance, so
    /// the map stays usable for clicking the anchor and drawing the
    /// direction while it is open.
    /// </summary>
    public partial class PlotDesignerWindow : ProWindow
    {
        private static PlotDesignerWindow _instance;

        public PlotDesignerWindow()
        {
            InitializeComponent();
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            panel.CloseRequested += (_, _) => Close();
            Closed += (_, _) => panel.Shutdown();
        }

        internal static void ShowWindow()
        {
            if (_instance == null)
            {
                _instance = new PlotDesignerWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                _instance.Closed += (_, _) => _instance = null;
            }
            _instance.Show();
            _instance.Activate();
        }
    }

    /// <summary>Geometric Tools gallery, Crop Plot Design Tools row: opens
    /// the Plot Designer window.</summary>
    internal sealed class PlotDesignerButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                PlotDesignerWindow.ShowWindow();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Plot Designer window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "Plot Designer");
            }
        }
    }
}
