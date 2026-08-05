using System;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using MessageBox = ArcGIS.Desktop.Framework.Dialogs.MessageBox;

namespace WildlifeTools.Puzzle
{
    /// <summary>
    /// Ribbon button for the sliding-tile puzzle toy ("Take a Break" group).
    /// Opens ONE shared, NON-MODAL window (like Sudoku and the Thought-for-the-Day
    /// card), so the user can keep working in Pro while the puzzle floats
    /// alongside; clicking the button again just brings the existing game forward.
    /// The puzzle image is a snapshot of the active map or layout view, captured
    /// by the window itself once it loads.
    /// </summary>
    internal sealed class SlidingPuzzleButton : Button
    {
        private static SlidingPuzzleWindow _window;

        protected override void OnClick()
        {
            try
            {
                if (_window != null)
                {
                    _window.Activate();
                    return;
                }
                _window = new SlidingPuzzleWindow { Owner = FrameworkApplication.Current.MainWindow };
                _window.Closed += (_, __) => _window = null;
                _window.Show();                     // non-modal
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    $"Could not open the Sliding Puzzle window:{Environment.NewLine}{Environment.NewLine}{ex.Message}",
                    "Sliding Puzzle");
            }
        }
    }
}
