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
using System.Diagnostics;

namespace WildlifeTools
{
    /// <summary>
    /// The one place the add-in hands an address to Windows to open. Windows
    /// opens whatever it is given: a web address goes to the browser, but a
    /// file path or a <c>file:</c> address would open that file, and the
    /// path of a program would run it. Some of the addresses the add-in
    /// opens come from an online service (a dataset's page in a
    /// repository's own record, for one), so nothing is opened here unless
    /// it is a complete <c>http</c> or <c>https</c> address, or a
    /// <c>mailto</c> address where the caller allows one. Anything else is
    /// ignored.
    ///
    /// No ArcGIS types here, so that the headless tests can compile it.
    /// </summary>
    internal static class SafeLink
    {
        /// <summary>The address as Windows will be given it, or null when it
        /// is not one this add-in opens.</summary>
        internal static string Checked(string url, bool allowMail = false)
        {
            if (string.IsNullOrWhiteSpace(url))
            {
                return null;
            }
            if (!Uri.TryCreate(url.Trim(), UriKind.Absolute, out Uri uri))
            {
                return null;
            }
            bool web = uri.Scheme == Uri.UriSchemeHttps || uri.Scheme == Uri.UriSchemeHttp;
            bool mail = allowMail && uri.Scheme == Uri.UriSchemeMailto;
            if (!web && !mail)
            {
                return null;
            }
            if (web && string.IsNullOrEmpty(uri.Host))
            {
                return null;
            }
            return uri.AbsoluteUri;
        }

        /// <summary>Opens a web address in the default browser (or, where
        /// allowed, a mailto address in the mail program). True when it was
        /// handed to Windows. A missing or blocked browser is ignored: a
        /// link must never crash a window.</summary>
        internal static bool Open(string url, bool allowMail = false)
        {
            string address = Checked(url, allowMail);
            if (address == null)
            {
                return false;
            }
            try
            {
                Process.Start(new ProcessStartInfo(address) { UseShellExecute = true });
                return true;
            }
            catch (Exception)
            {
                return false;
            }
        }
    }
}
