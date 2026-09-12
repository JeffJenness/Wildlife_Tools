using System;
using System.IO;
using ArcGIS.Desktop.Core;

namespace WildlifeTools
{
    /// <summary>
    /// The folder the suite's windows offer when saving a graph or table
    /// image. Remembered per PROJECT (persisted through Module1's project
    /// settings) from the last image actually saved, so a project whose
    /// figures live in one folder keeps offering that folder; before any
    /// save it is the project's home folder, and outside a project the
    /// user's Documents.
    /// </summary>
    internal static class ImageSaveFolder
    {
        public static string Initial()
        {
            try
            {
                string last = Module1.LastImageFolder;
                if (!string.IsNullOrEmpty(last) && Directory.Exists(last))
                    return last;
                string home = Project.Current?.HomeFolderPath;
                if (!string.IsNullOrEmpty(home) && Directory.Exists(home))
                    return home;
            }
            catch { }
            return Environment.GetFolderPath(
                Environment.SpecialFolder.MyDocuments);
        }

        /// <summary>Remember the folder of a just-saved image and mark the
        /// project dirty so the choice is written with its next save.</summary>
        public static void Remember(string savedFile)
        {
            try
            {
                string dir = Path.GetDirectoryName(savedFile);
                if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) return;
                if (string.Equals(dir, Module1.LastImageFolder,
                        StringComparison.OrdinalIgnoreCase)) return;
                Module1.LastImageFolder = dir;
                Project.Current?.SetDirty(true);
            }
            catch { }
        }
    }
}
