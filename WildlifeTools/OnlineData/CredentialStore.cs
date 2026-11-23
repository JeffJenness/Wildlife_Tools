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

using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace WildlifeTools.OnlineData
{
    /// <summary>
    /// Keeps the user's sign-in for an online data service (GBIF, ...) in
    /// Windows Credential Manager, under the user's own Windows login. The
    /// password is never written to a settings file. Each service has one
    /// entry, named "JennessWildlifeTools.&lt;service&gt;", which the user
    /// can also see and delete in Windows' own Credential Manager.
    ///
    /// No ArcGIS types here: the class is compiled into the headless tests.
    /// </summary>
    internal static class CredentialStore
    {
        // No spaces or colons in the name: Windows' own cmdkey command refuses them,
        // and the user may make or remove the entry by hand.
        private const string Prefix = "JennessWildlifeTools.";
        private const int CredTypeGeneric = 1;
        private const int PersistLocalMachine = 2;
        private const int ErrorNotFound = 1168;

        internal static string TargetName(string service) => Prefix + service;

        /// <summary>Saves (or replaces) the sign-in for a service.</summary>
        internal static void Save(string service, string userName, string password)
        {
            if (string.IsNullOrWhiteSpace(service)) throw new ArgumentException("service");
            if (string.IsNullOrEmpty(userName)) throw new ArgumentException("userName");
            byte[] blob = Encoding.Unicode.GetBytes(password ?? "");
            IntPtr blobPtr = Marshal.AllocCoTaskMem(Math.Max(blob.Length, 1));
            try
            {
                Marshal.Copy(blob, 0, blobPtr, blob.Length);
                var cred = new CREDENTIAL
                {
                    Type = CredTypeGeneric,
                    TargetName = TargetName(service),
                    UserName = userName,
                    CredentialBlob = blobPtr,
                    CredentialBlobSize = blob.Length,
                    Persist = PersistLocalMachine,
                    Comment = "Sign-in used by the Wildlife and Forestry Tools add-in for ArcGIS Pro.",
                };
                if (!CredWrite(ref cred, 0))
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error());
                }
            }
            finally
            {
                Marshal.FreeCoTaskMem(blobPtr);
                Array.Clear(blob, 0, blob.Length);
            }
        }

        /// <summary>Reads the sign-in for a service. False when none is stored.</summary>
        internal static bool TryRead(string service, out string userName, out string password)
        {
            userName = null;
            password = null;
            if (!CredRead(TargetName(service), CredTypeGeneric, 0, out IntPtr ptr))
            {
                return false;
            }
            try
            {
                var cred = Marshal.PtrToStructure<CREDENTIAL>(ptr);
                userName = cred.UserName;
                password = cred.CredentialBlobSize > 0 && cred.CredentialBlob != IntPtr.Zero
                    ? Marshal.PtrToStringUni(cred.CredentialBlob, cred.CredentialBlobSize / 2)
                    : "";
                return !string.IsNullOrEmpty(userName);
            }
            finally
            {
                CredFree(ptr);
            }
        }

        /// <summary>The stored user name alone (null when none), for display.</summary>
        internal static string UserName(string service) =>
            TryRead(service, out string user, out _) ? user : null;

        /// <summary>Forgets the sign-in. True when an entry was removed.</summary>
        internal static bool Delete(string service)
        {
            if (CredDelete(TargetName(service), CredTypeGeneric, 0))
            {
                return true;
            }
            int err = Marshal.GetLastWin32Error();
            if (err == ErrorNotFound)
            {
                return false;
            }
            throw new Win32Exception(err);
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
        private struct CREDENTIAL
        {
            public int Flags;
            public int Type;
            public string TargetName;
            public string Comment;
            public System.Runtime.InteropServices.ComTypes.FILETIME LastWritten;
            public int CredentialBlobSize;
            public IntPtr CredentialBlob;
            public int Persist;
            public int AttributeCount;
            public IntPtr Attributes;
            public string TargetAlias;
            public string UserName;
        }

        [DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CredWrite(ref CREDENTIAL credential, int flags);

        [DllImport("advapi32.dll", EntryPoint = "CredReadW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CredRead(string target, int type, int flags, out IntPtr credential);

        [DllImport("advapi32.dll", EntryPoint = "CredDeleteW", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern bool CredDelete(string target, int type, int flags);

        [DllImport("advapi32.dll", EntryPoint = "CredFree")]
        private static extern void CredFree(IntPtr buffer);
    }
}
