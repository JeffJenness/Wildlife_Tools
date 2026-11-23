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
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Interop;
using System.Windows.Media;

namespace WildlifeTools
{
    /// <summary>
    /// Keeps a window of the add-in inside the screen it opens on.
    ///
    /// THE RULE (Jeff Jenness, 23 November 2026): every window the add-in
    /// makes, in XAML or in code, must fit a laptop screen. Each one calls
    /// <c>WindowFit.Apply</c>: a XAML window from its constructor, after
    /// InitializeComponent; a window built in code just before it is shown.
    /// claude_tests/window_fit_test.py fails when a window does not.
    ///
    /// Why. A window that asks for 1040 by 780 is taller than the screen of
    /// a 1366 by 768 laptop, or of a 1920 by 1080 laptop at 150 percent (a
    /// work area of about 1280 by 672): its bottom edge, with the OK and
    /// Close buttons, opens off the screen.
    ///
    /// What Apply does, for the monitor ArcGIS Pro is on:
    ///   - A window with a size of its own opens no larger than the work
    ///     area less a small margin, and its minimum size is lowered to
    ///     that if need be. It stays resizable.
    ///   - A window that sizes itself to its content is given the same
    ///     limit as its greatest size. When its layout cannot give way (no
    ///     row that takes "the rest"), its content is put inside a scroll
    ///     box, so that the buttons at the bottom can always be reached. On
    ///     a screen the content fits, nothing looks different.
    ///   - When the window has loaded it is moved back inside the work
    ///     area, if centering it on Pro's window pushed part of it out.
    /// Nothing here can stop a window from opening: every error is
    /// swallowed and the window is then as its XAML made it.
    /// </summary>
    internal static class WindowFit
    {
        /// <summary>Room left around the window, in device-independent units.</summary>
        internal const double Margin = 16.0;

        [StructLayout(LayoutKind.Sequential)]
        private struct NativeRect { public int Left, Top, Right, Bottom; }

        [StructLayout(LayoutKind.Sequential)]
        private struct MonitorInfo
        {
            public int Size;
            public NativeRect Monitor;
            public NativeRect Work;
            public uint Flags;
        }

        [DllImport("user32.dll")]
        private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint flags);

        [DllImport("user32.dll")]
        private static extern bool GetMonitorInfo(IntPtr monitor, ref MonitorInfo info);

        /// <summary>The work area (the screen less the taskbar) of the monitor
        /// that holds <paramref name="owner"/>, in WPF units; the primary
        /// monitor's when the owner cannot be asked.</summary>
        internal static Rect WorkAreaOf(Window owner)
        {
            Rect area = SystemParameters.WorkArea;
            try
            {
                if (owner == null)
                {
                    return area;
                }
                IntPtr handle = new WindowInteropHelper(owner).Handle;
                if (handle == IntPtr.Zero)
                {
                    return area;
                }
                var info = new MonitorInfo { Size = Marshal.SizeOf(typeof(MonitorInfo)) };
                if (!GetMonitorInfo(MonitorFromWindow(handle, 2 /* the nearest monitor */), ref info))
                {
                    return area;
                }
                DpiScale dpi = VisualTreeHelper.GetDpi(owner);
                double sx = dpi.DpiScaleX > 0 ? dpi.DpiScaleX : 1.0, sy = dpi.DpiScaleY > 0 ? dpi.DpiScaleY : 1.0;
                var found = new Rect(info.Work.Left / sx, info.Work.Top / sy,
                                     Math.Max(0, info.Work.Right - info.Work.Left) / sx,
                                     Math.Max(0, info.Work.Bottom - info.Work.Top) / sy);
                return found.Width > 200 && found.Height > 200 ? found : area;
            }
            catch (Exception)
            {
                return area;
            }
        }

        /// <summary>The largest size a window may have inside
        /// <paramref name="area"/>: the area less the margin, and never so
        /// small that a window could not be used at all.</summary>
        internal static Size Limit(Rect area)
        {
            return new Size(Math.Max(320.0, area.Width - Margin), Math.Max(240.0, area.Height - Margin));
        }

        /// <summary>The size a window may open at inside <paramref name="area"/>:
        /// its own, or the limit when that is smaller. A size that is not
        /// set (NaN) stays unset.</summary>
        internal static Size Fitted(double width, double height, Rect area)
        {
            Size limit = Limit(area);
            return new Size(double.IsNaN(width) ? width : Math.Min(width, limit.Width),
                            double.IsNaN(height) ? height : Math.Min(height, limit.Height));
        }

        /// <summary>Call from the window's constructor, after
        /// InitializeComponent; for a window built in code, when its content
        /// and owner are set and before it is shown.
        /// <paramref name="scrollContent"/> says whether the content of a
        /// window that sizes itself to its content is put in a scroll box:
        /// null (the usual choice) lets <see cref="NeedsScrollBox"/> decide;
        /// false is for a window that scrolls its own content (the
        /// cards).</summary>
        internal static void Apply(Window window, bool? scrollContent = null)
        {
            if (window == null)
            {
                return;
            }
            try
            {
                Window owner = window.Owner ?? Application.Current?.MainWindow;
                Shape(window, WorkAreaOf(ReferenceEquals(owner, window) ? null : owner), scrollContent);
                window.Loaded += (s, e) => KeepInside(window);
            }
            catch (Exception)
            {
                // the size the XAML asks for stands
            }
        }

        /// <summary>The part of <see cref="Apply"/> that needs no screen:
        /// sizes, limits and the scroll box, for a work area that is
        /// given. (Tested outside ArcGIS Pro: claude_tests/WindowFitTest.)</summary>
        internal static void Shape(Window window, Rect area, bool? scrollContent = null)
        {
            Size limit = Limit(area);
            bool toWidth = window.SizeToContent == SizeToContent.Width
                           || window.SizeToContent == SizeToContent.WidthAndHeight || double.IsNaN(window.Width);
            bool toHeight = window.SizeToContent == SizeToContent.Height
                            || window.SizeToContent == SizeToContent.WidthAndHeight || double.IsNaN(window.Height);

            if (window.MinWidth > limit.Width)
            {
                window.MinWidth = limit.Width;
            }
            if (window.MinHeight > limit.Height)
            {
                window.MinHeight = limit.Height;
            }
            if (toWidth)
            {
                if (!(window.MaxWidth <= limit.Width))       // (also true of "no limit", which is infinity)
                {
                    window.MaxWidth = limit.Width;
                }
            }
            else if (window.Width > limit.Width)
            {
                window.Width = limit.Width;
            }
            if (toHeight)
            {
                if (!(window.MaxHeight <= limit.Height))
                {
                    window.MaxHeight = limit.Height;
                }
            }
            else if (window.Height > limit.Height)
            {
                window.Height = limit.Height;
            }

            // a scroll box only where the window really takes its size from its content: a window with no
            // size given and no SizeToContent is sized by Windows, and its content must keep filling it
            bool wide = window.SizeToContent == SizeToContent.Width || window.SizeToContent == SizeToContent.WidthAndHeight;
            bool tall = window.SizeToContent == SizeToContent.Height || window.SizeToContent == SizeToContent.WidthAndHeight;
            if ((wide || tall) && (scrollContent ?? NeedsScrollBox(window.Content, wide, tall)))
            {
                PutInScrollBox(window, wide, tall);
            }
        }

        /// <summary>Whether the content of a window that sizes itself to its
        /// content must be put in a scroll box to stay reachable on a small
        /// screen. Not when it scrolls already, and not when its layout
        /// gives way by itself: a Grid with a row (a column, for width) that
        /// takes "the rest" shrinks that row and keeps its buttons in view,
        /// and a DockPanel does the same with its last child.</summary>
        internal static bool NeedsScrollBox(object content, bool toWidth, bool toHeight)
        {
            if (!(content is UIElement) || content is ScrollViewer)
            {
                return false;
            }
            if (content is DockPanel dock && dock.LastChildFill)
            {
                return false;
            }
            if (content is Grid grid)
            {
                bool rowGives = false, columnGives = false;
                foreach (RowDefinition r in grid.RowDefinitions)
                {
                    rowGives |= r.Height.IsStar;
                }
                foreach (ColumnDefinition c in grid.ColumnDefinitions)
                {
                    columnGives |= c.Width.IsStar;
                }
                // a Grid with no rows at all is one row that takes everything
                rowGives |= grid.RowDefinitions.Count == 0;
                columnGives |= grid.ColumnDefinitions.Count == 0;
                return (toHeight && !rowGives) || (toWidth && !columnGives);
            }
            return true;
        }

        private static void PutInScrollBox(Window window, bool toWidth, bool toHeight)
        {
            if (!(window.Content is UIElement content) || content is ScrollViewer)
            {
                return;
            }
            var box = new ScrollViewer
            {
                VerticalScrollBarVisibility = toHeight ? ScrollBarVisibility.Auto : ScrollBarVisibility.Disabled,
                HorizontalScrollBarVisibility = toWidth ? ScrollBarVisibility.Auto : ScrollBarVisibility.Disabled,
                // a scroll box that can take the focus puts a stop in the Tab order and a dotted frame on screen
                Focusable = false,
            };
            window.Content = null;
            box.Content = content;
            window.Content = box;
        }

        /// <summary>Moves a loaded window back inside the work area of the
        /// monitor it is on. Never throws.</summary>
        internal static void KeepInside(Window window)
        {
            try
            {
                Rect now = WorkAreaOf(window);
                Point at = Inside(new Rect(window.Left, window.Top, window.ActualWidth, window.ActualHeight), now);
                if (at.X != window.Left)
                {
                    window.Left = at.X;
                }
                if (at.Y != window.Top)
                {
                    window.Top = at.Y;
                }
            }
            catch (Exception)
            {
                // a window that could not be moved is still a window
            }
        }

        /// <summary>Where the top left corner of <paramref name="window"/>
        /// goes so that the window lies inside <paramref name="area"/>; a
        /// window larger than the area keeps its top left corner in
        /// view.</summary>
        internal static Point Inside(Rect window, Rect area)
        {
            double left = window.Left, top = window.Top;
            if (double.IsNaN(left) || double.IsNaN(top))
            {
                return new Point(left, top);
            }
            if (left + window.Width > area.Right)
            {
                left = area.Right - window.Width;
            }
            if (top + window.Height > area.Bottom)
            {
                top = area.Bottom - window.Height;
            }
            if (left < area.Left)
            {
                left = area.Left;
            }
            if (top < area.Top)
            {
                top = area.Top;
            }
            return new Point(left, top);
        }
    }
}
