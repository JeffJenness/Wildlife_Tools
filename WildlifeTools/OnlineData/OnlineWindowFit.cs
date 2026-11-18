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
using System.Windows.Interop;
using System.Windows.Media;

namespace WildlifeTools.OnlineData
{
    /// <summary>
    /// Keeps an Online Data window inside the screen it opens on. The windows
    /// ask for about 1040 by 780, which is taller than the screen of a
    /// 1366 by 768 laptop, or of a 1920 by 1080 laptop at 150 percent: the
    /// bottom of the window, with its Close button, then opens off the screen.
    /// <c>Apply</c> shrinks the window's opening size (and its minimum size,
    /// if need be) to the work area of the monitor ArcGIS Pro is on, and when
    /// the window has loaded moves it back inside that area if centering it
    /// on Pro's window pushed part of it out. The window stays resizable, and
    /// each tab scrolls, so nothing is lost on a small screen.
    /// </summary>
    internal static class OnlineWindowFit
    {
        private const double Margin = 16.0;          // room left around the window, in device-independent units

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

        /// <summary>The size a window may open at inside <paramref name="area"/>:
        /// its own, or the area less a margin when that is smaller.</summary>
        internal static Size Fitted(double width, double height, Rect area)
        {
            double maxW = Math.Max(320.0, area.Width - Margin), maxH = Math.Max(240.0, area.Height - Margin);
            return new Size(double.IsNaN(width) ? width : Math.Min(width, maxW),
                            double.IsNaN(height) ? height : Math.Min(height, maxH));
        }

        /// <summary>Call from the window's constructor, after
        /// InitializeComponent.</summary>
        internal static void Apply(Window window)
        {
            if (window == null)
            {
                return;
            }
            try
            {
                Window owner = window.Owner ?? Application.Current?.MainWindow;
                Rect area = WorkAreaOf(ReferenceEquals(owner, window) ? null : owner);
                Size size = Fitted(window.Width, window.Height, area);
                if (window.MinWidth > size.Width)
                {
                    window.MinWidth = size.Width;
                }
                if (window.MinHeight > size.Height)
                {
                    window.MinHeight = size.Height;
                }
                if (!double.IsNaN(size.Width))
                {
                    window.Width = size.Width;
                }
                if (!double.IsNaN(size.Height))
                {
                    window.Height = size.Height;
                }
                window.Loaded += (s, e) =>
                {
                    try
                    {
                        // centered on Pro's window, which may itself hang off the screen
                        Rect now = WorkAreaOf(window);
                        if (window.Left + window.ActualWidth > now.Right)
                        {
                            window.Left = now.Right - window.ActualWidth;
                        }
                        if (window.Top + window.ActualHeight > now.Bottom)
                        {
                            window.Top = now.Bottom - window.ActualHeight;
                        }
                        if (window.Left < now.Left)
                        {
                            window.Left = now.Left;
                        }
                        if (window.Top < now.Top)
                        {
                            window.Top = now.Top;
                        }
                    }
                    catch (Exception)
                    {
                        // a window that could not be moved is still a window
                    }
                };
            }
            catch (Exception)
            {
                // the size the XAML asks for stands
            }
        }
    }
}
