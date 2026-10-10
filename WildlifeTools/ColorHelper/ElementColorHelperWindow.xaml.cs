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

namespace WildlifeTools.ColorHelper
{
    /// <summary>
    /// The Element Color Helper window: a ProWindow shell around
    /// <see cref="ElementColorHelperPanel"/>. Non-modal, one shared instance,
    /// so elements can be selected on the layout or map while it is open;
    /// the panel follows the selection.
    /// </summary>
    public partial class ElementColorHelperWindow : ProWindow
    {
        private static ElementColorHelperWindow _instance;

        public ElementColorHelperWindow()
        {
            InitializeComponent();
            global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            panel.CloseRequested += (_, _) => Close();
        }

        internal static void ShowWindow()
        {
            if (_instance == null)
            {
                _instance = new ElementColorHelperWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                _instance.Closed += (_, _) => _instance = null;
            }
            _instance.Show();
            _instance.Activate();
        }
    }

    /// <summary>Cartography group, Cartographic Helpers row: opens the
    /// Element Color Helper window.</summary>
    internal sealed class ElementColorHelperButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                ElementColorHelperWindow.ShowWindow();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the Element Color Helper window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "Element Color Helper");
            }
        }
    }
}
