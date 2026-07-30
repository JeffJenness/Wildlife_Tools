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
