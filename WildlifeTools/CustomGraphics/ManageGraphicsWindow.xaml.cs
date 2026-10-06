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
using ArcGIS.Desktop.Framework.Controls;

namespace WildlifeTools.CustomGraphics
{
    /// <summary>
    /// The Manage Custom Graphics and North Arrows window: a ProWindow shell
    /// around <see cref="ManageGraphicsPanel"/>, which is all of its content.
    /// Opened by "Manage..." at the foot of the Add Graphics and North Arrows
    /// galleries. Non-modal, one shared instance, so an element can be
    /// selected on the map or layout while it is open (Apply to Selected
    /// Element).
    /// </summary>
    public partial class ManageGraphicsWindow : ProWindow
    {
        private static ManageGraphicsWindow _instance;

        public ManageGraphicsWindow()
        {
            InitializeComponent();
            panel.CloseRequested += (_, _) => Close();
        }

        /// <summary>Shows the window on the tab of the given gallery. Must be
        /// called on the UI thread.</summary>
        internal static void ShowWindow(GraphicKind kind)
        {
            if (_instance == null)
            {
                _instance = new ManageGraphicsWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                _instance.Closed += (_, _) => _instance = null;
            }
            _instance.panel.ShowKind(kind, null);
            _instance.Show();
            _instance.Activate();
        }
    }
}
