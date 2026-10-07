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

namespace WildlifeTools.TpiSampler
{
    /// <summary>
    /// The TPI Neighborhood Sampler window: a ProWindow shell around
    /// <see cref="TpiSamplerPanel"/>. Non-modal, one shared instance, so the
    /// map can be panned and zoomed while it is open and a new snapshot
    /// taken.
    /// </summary>
    public partial class TpiSamplerWindow : ProWindow
    {
        private static TpiSamplerWindow _instance;

        public TpiSamplerWindow()
        {
            InitializeComponent();
            panel.CloseRequested += (_, _) => Close();
        }

        internal static void ShowWindow()
        {
            if (_instance == null)
            {
                _instance = new TpiSamplerWindow
                {
                    Owner = FrameworkApplication.Current.MainWindow,
                };
                _instance.Closed += (_, _) => _instance = null;
            }
            _instance.Show();
            _instance.Activate();
        }
    }

    /// <summary>Topographic Analysis group, TPI Tools row: opens the
    /// TPI Neighborhood Sampler window.</summary>
    internal sealed class TpiSamplerButton : Button
    {
        protected override void OnClick()
        {
            try
            {
                TpiSamplerWindow.ShowWindow();
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not open the TPI Neighborhood Sampler window:"
                    + Environment.NewLine + Environment.NewLine + ex.Message,
                    "TPI Neighborhood Sampler");
            }
        }
    }
}
