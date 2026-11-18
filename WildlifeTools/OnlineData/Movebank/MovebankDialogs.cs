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
using System.Windows;
using System.Windows.Controls;

namespace WildlifeTools.OnlineData.Movebank
{
    /// <summary>Asks for a Movebank user name and password. Plain WPF, built
    /// in code. The password leaves this window only through
    /// <see cref="Password"/>, to be checked with Movebank and stored in
    /// Windows Credential Manager.</summary>
    internal sealed class MovebankSignInWindow : Window
    {
        private readonly TextBox _user = new TextBox { Height = 24, VerticalContentAlignment = VerticalAlignment.Center };
        private readonly PasswordBox _password = new PasswordBox { Height = 24, VerticalContentAlignment = VerticalAlignment.Center };

        internal string UserName => _user.Text.Trim();
        internal string Password => _password.Password;

        internal MovebankSignInWindow(string userName)
        {
            Title = "Sign in to Movebank";
            Width = 460;
            SizeToContent = SizeToContent.Height;
            ResizeMode = ResizeMode.NoResize;
            WindowStartupLocation = WindowStartupLocation.CenterOwner;
            ShowInTaskbar = false;
            try
            {
                global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            }
            catch (Exception)
            {
            }
            var stack = new StackPanel { Margin = new Thickness(14) };
            stack.Children.Add(new TextBlock
            {
                TextWrapping = TextWrapping.Wrap,
                Margin = new Thickness(0, 0, 0, 10),
                Text = "Give the user name and password of your own Movebank account (movebank.org). The user name is not "
                    + "your email address. A new account works here only after Movebank has activated it.",
            });
            stack.Children.Add(new TextBlock { Text = "Movebank user name", FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 0, 0, 3) });
            _user.Text = userName ?? "";
            stack.Children.Add(_user);
            stack.Children.Add(new TextBlock { Text = "Password", FontWeight = FontWeights.SemiBold, Margin = new Thickness(0, 8, 0, 3) });
            stack.Children.Add(_password);
            stack.Children.Add(new TextBlock
            {
                TextWrapping = TextWrapping.Wrap,
                Opacity = 0.8,
                Margin = new Thickness(0, 10, 0, 0),
                Text = "The sign-in is kept on this computer, in Windows Credential Manager, under the name "
                    + CredentialStore.TargetName(MovebankAccount.Service) + ". It is sent only to www.movebank.org, over an "
                    + "encrypted (https) connection, and is never written to a report, a file or the metadata. Sign out removes it.",
            });
            var buttons = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 14, 0, 0) };
            var ok = new Button { Content = "Sign in", Width = 90, Height = 28, IsDefault = true };
            ok.Click += (_, _) =>
            {
                if (UserName.Length == 0 || Password.Length == 0)
                {
                    MessageBox.Show(this, "Give both the user name and the password.", Title);
                    return;
                }
                DialogResult = true;
            };
            var cancel = new Button { Content = "Cancel", Width = 84, Height = 28, Margin = new Thickness(8, 0, 0, 0), IsCancel = true };
            buttons.Children.Add(ok);
            buttons.Children.Add(cancel);
            stack.Children.Add(buttons);
            Content = stack;
            Loaded += (_, _) => (_user.Text.Length == 0 ? (Control)_user : _password).Focus();
        }
    }

    /// <summary>Shows a study's license terms, as Movebank sent them.</summary>
    internal sealed class MovebankTermsWindow : Window
    {
        internal MovebankTermsWindow(string studyName, string terms)
        {
            Title = "License terms of the Movebank study";
            Width = 720;
            Height = 620;
            MinWidth = 420;
            MinHeight = 300;
            WindowStartupLocation = WindowStartupLocation.CenterOwner;
            ShowInTaskbar = false;
            try
            {
                global::WildlifeTools.Corridor.JennessIcon.Apply(this);
            }
            catch (Exception)
            {
            }
            var dock = new DockPanel { Margin = new Thickness(12) };
            var head = new TextBlock
            {
                TextWrapping = TextWrapping.Wrap,
                FontWeight = FontWeights.SemiBold,
                Margin = new Thickness(0, 0, 0, 8),
                Text = studyName,
            };
            DockPanel.SetDock(head, Dock.Top);
            dock.Children.Add(head);
            var note = new TextBlock
            {
                TextWrapping = TextWrapping.Wrap,
                Opacity = 0.8,
                Margin = new Thickness(0, 0, 0, 8),
                Text = "These are the terms as Movebank sent them for this study. Reading them here accepts nothing. If you "
                    + "accept them, close this window and check the box under the button: the terms are then accepted in the "
                    + "name of your Movebank account when you click Get the data.",
            };
            DockPanel.SetDock(note, Dock.Top);
            dock.Children.Add(note);
            var close = new Button { Content = "Close", Width = 84, Height = 28, IsCancel = true, IsDefault = true, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 10, 0, 0) };
            close.Click += (_, _) => Close();
            DockPanel.SetDock(close, Dock.Bottom);
            dock.Children.Add(close);
            dock.Children.Add(new TextBox
            {
                Text = terms,
                IsReadOnly = true,
                TextWrapping = TextWrapping.Wrap,
                AcceptsReturn = true,
                VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
                Padding = new Thickness(6),
            });
            Content = dock;
        }
    }
}
