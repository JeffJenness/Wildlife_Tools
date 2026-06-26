using System.IO;
using System.Reflection;

namespace WildlifeTools
{
    /// <summary>
    /// Locates the Jenness Enterprises Python toolbox that is deployed alongside the
    /// add-in assembly (extracted into a "Toolboxes" subfolder of the add-in install
    /// folder at run time).
    /// </summary>
    internal static class ToolboxPaths
    {
        /// <summary>Full path to the deployed JennessEnterprisesTools.pyt.</summary>
        public static string Pyt
        {
            get
            {
                string dir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
                return Path.Combine(dir, "Toolboxes", "JennessEnterprisesTools.pyt");
            }
        }

        /// <summary>
        /// Tool reference for a tool in the toolbox, e.g.
        /// "...\JennessEnterprisesTools.pyt\LoCoHStandard", or null if the toolbox
        /// cannot be found next to the assembly.
        /// </summary>
        public static string ToolPath(string toolName)
        {
            string pyt = Pyt;
            return File.Exists(pyt) ? Path.Combine(pyt, toolName) : null;
        }
    }
}
