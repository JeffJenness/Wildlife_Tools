// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Award notice, marking of modified versions, use of names,
// and an additional permission to link with ArcGIS Pro).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

using System.Windows;
using System.Windows.Documents;
using System.Windows.Media;

namespace WildlifeTools.OnlineData
{
    /// <summary>
    /// Keeps a link in a table row readable when the row is selected. A
    /// selected row is drawn dark blue with white text, and a link's own
    /// blue cannot be read on it. Bind <c>Around</c> on the Hyperlink to
    /// the Foreground of the TextBlock that holds it: while that text
    /// color is a light one (the row is selected), the link is drawn light
    /// blue; otherwise the link keeps the color its style gives it.
    /// <code>
    /// &lt;Hyperlink od:LinkContrast.Around="{Binding Foreground, RelativeSource={RelativeSource AncestorType=TextBlock}}" ...
    /// </code>
    /// <c>Warn</c> draws a link in red: for a row whose link can be
    /// followed but whose data cannot be had (a Movebank study the account
    /// may not download). It is a light red where the text around it is
    /// light, for the same reason.
    /// </summary>
    public static class LinkContrast
    {
        private static readonly Brush OnDark = MakeBrush(0xC4, 0xE4, 0xFF);
        private static readonly Brush WarnBrush = MakeBrush(0xC6, 0x28, 0x28);
        private static readonly Brush WarnOnDark = MakeBrush(0xFF, 0xB4, 0xB4);

        private static Brush MakeBrush(byte r, byte g, byte b)
        {
            var brush = new SolidColorBrush(Color.FromRgb(r, g, b));
            brush.Freeze();
            return brush;
        }

        public static readonly DependencyProperty WarnProperty = DependencyProperty.RegisterAttached(
            "Warn", typeof(bool), typeof(LinkContrast), new PropertyMetadata(false, OnAroundChanged));

        public static bool GetWarn(DependencyObject d) => (bool)d.GetValue(WarnProperty);

        public static void SetWarn(DependencyObject d, bool value) => d.SetValue(WarnProperty, value);

        public static readonly DependencyProperty AroundProperty = DependencyProperty.RegisterAttached(
            "Around", typeof(Brush), typeof(LinkContrast), new PropertyMetadata(null, OnAroundChanged));

        public static Brush GetAround(DependencyObject d) => (Brush)d.GetValue(AroundProperty);

        public static void SetAround(DependencyObject d, Brush value) => d.SetValue(AroundProperty, value);

        private static void OnAroundChanged(DependencyObject d, DependencyPropertyChangedEventArgs e)
        {
            if (!(d is Hyperlink link))
            {
                return;
            }
            bool light = false;
            if (GetAround(link) is SolidColorBrush s)
            {
                Color c = s.Color;
                light = c.A > 128 && (0.299 * c.R + 0.587 * c.G + 0.114 * c.B) > 170.0;
            }
            if (GetWarn(link))
            {
                link.Foreground = light ? WarnOnDark : WarnBrush;
            }
            else if (light)
            {
                link.Foreground = OnDark;
            }
            else
            {
                link.ClearValue(TextElement.ForegroundProperty);
            }
        }
    }
}
