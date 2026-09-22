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
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Sudoku
{
    /// <summary>
    /// Ribbon button for the Sudoku toy ("Take a Break" group). Opens ONE
    /// shared, NON-MODAL window (like the Thought for the Day card), so the
    /// user can keep working in Pro while the puzzle floats alongside;
    /// clicking the button again just brings the existing game forward.
    /// </summary>
    internal sealed class SudokuButton : Button
    {
        private static SudokuWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null)
                {
                    _window.Activate();
                    return;
                }
                _window = new SudokuWindow { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();                     // non-modal
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    $"Could not open the Sudoku window:{Environment.NewLine}{Environment.NewLine}{ex.Message}",
                    "Sudoku");
            }
        }
    }
}
