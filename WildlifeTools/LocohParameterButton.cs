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

using ArcGIS.Desktop.Framework.Contracts;

namespace WildlifeTools
{
    /// <summary>
    /// Opens the LoCoH Parameter Explorer window (singleton -- clicking
    /// again brings the existing window forward, so a computed sweep is
    /// never lost to an accidental second click).
    /// </summary>
    internal class LocohParameterButton : Button
    {
        private static LocohParameterWindow _window;

        protected override void OnClick()
        {
            if (_window != null)
            {
                _window.Activate();
                return;
            }
            _window = new LocohParameterWindow
            {
                Owner = ArcGIS.Desktop.Framework.FrameworkApplication
                    .Current.MainWindow,
            };
            _window.Closed += (_, __) => _window = null;
            _window.Show();
        }
    }
}
