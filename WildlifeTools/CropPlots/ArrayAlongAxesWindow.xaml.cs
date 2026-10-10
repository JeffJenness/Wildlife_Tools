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
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Controls;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.CropPlots
{
    /// <summary>
    /// The Create Array along Axes window: a ProWindow shell around
    /// <see cref="ArrayAlongAxesPanel"/>. Non-modal, one shared instance, so
    /// graphics can be selected and axes drawn on the map while it is open.
    /// </summary>
    public partial class ArrayAlongAxesWindow : ProWindow
    {
        private static ArrayAlongAxesWindow _instance;

        public ArrayAlongAxesWindow()
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
                _instance = new ArrayAlongAxesWindow
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
    /// the Create Array along Axes window.</summary>
    internal sealed class ArrayAlongAxesButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                ArrayAlongAxesWindow.ShowWindow();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Create Array along Axes window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "Create Array along Axes");
            }
        }
    }

    /// <summary>Geometric Tools gallery, Crop Plot Design Tools row: opens
    /// the Fuse Plots and Field geoprocessing tool.</summary>
    internal sealed class FusePlotsButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("FusePlotsAndField");
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
                MessageBox.Show("Unable to open the Fuse Plots and Field tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }

    /// <summary>Data Tools group: opens the Add Random Numbers
    /// geoprocessing tool.</summary>
    internal sealed class AddRandomNumbersButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                string toolPath = ToolboxPaths.ToolPath("AddRandomNumbers");
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
                MessageBox.Show("Unable to open the Add Random Numbers tool:" + Environment.NewLine + ex.Message,
                    "Wildlife and Forestry Tools");
            }
        }
    }
}
