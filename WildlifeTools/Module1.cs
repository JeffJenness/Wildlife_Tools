using ArcGIS.Core.CIM;
using ArcGIS.Core.Data;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Catalog;
using ArcGIS.Desktop.Core;
using ArcGIS.Desktop.Core.Events;
using ArcGIS.Desktop.GeoProcessing;
using ArcGIS.Desktop.Editing;
using ArcGIS.Desktop.Extensions;
using ArcGIS.Desktop.Framework;
using ArcGIS.Desktop.Framework.Contracts;
using ArcGIS.Desktop.Framework.Dialogs;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.KnowledgeGraph;
using ArcGIS.Desktop.Layouts;
using ArcGIS.Desktop.Mapping;
using System;
using System.IO;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Input;

namespace WildlifeTools
{
    internal class Module1 : Module
    {
        private static Module1 _this = null;

        /// <summary>
        /// Retrieve the singleton instance to this module here
        /// </summary>
        public static Module1 Current => _this ??= (Module1)FrameworkApplication.FindModule("WildlifeTools_Module");

        #region Overrides
        /// <summary>
        /// Called by Framework when the module is first loaded. Subscribes to project
        /// events so the bundled toolbox is added to the project's Catalog/ModelBuilder.
        /// </summary>
        protected override bool Initialize()
        {
            ProjectOpenedEvent.Subscribe(OnProjectOpened);
            // A project may already be open when the module first loads.
            EnsureToolboxInProject();
            return base.Initialize();
        }

        /// <summary>
        /// Called by Framework when ArcGIS Pro is closing
        /// </summary>
        /// <returns>False to prevent Pro from closing, otherwise True</returns>
        protected override bool CanUnload()
        {
            //TODO - add your business logic
            //return false to ~cancel~ Application close
            return true;
        }

        #endregion Overrides

        private void OnProjectOpened(ProjectEventArgs args) => EnsureToolboxInProject();

        /// <summary>
        /// Adds the bundled Jenness Enterprises Python toolbox to the current project
        /// (so it appears in the Catalog and is usable in ModelBuilder) if it is not
        /// already present. Non-fatal on failure — the user can add it manually.
        /// </summary>
        private static void EnsureToolboxInProject()
        {
            string pyt = ToolboxPaths.Pyt;
            if (Project.Current == null || !File.Exists(pyt))
                return;

            QueuedTask.Run(() =>
            {
                try
                {
                    bool present = Project.Current.GetItems<GeoprocessingProjectItem>()
                        .Any(i => string.Equals(i.Path, pyt, StringComparison.OrdinalIgnoreCase));
                    if (present)
                        return;

                    var item = ItemFactory.Instance.Create(pyt, ItemFactory.ItemType.PathItem);
                    if (item is IProjectItem projectItem)
                        Project.Current.AddItem(projectItem);
                }
                catch
                {
                    // Non-fatal; toolbox can still be added manually via Add Toolbox.
                }
            });
        }

    }
}
