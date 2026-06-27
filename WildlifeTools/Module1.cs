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

        // Thought for the Day: keys of recently shown quotes (oldest first), persisted
        // with the project, so a quote is not repeated until MaxRecentQuotes others have
        // been shown.
        private const string RecentQuoteKeysKey = "ThoughtForTheDay_RecentQuoteKeys";
        public const int MaxRecentQuotes = 100;

        /// <summary>Keys of the most recently shown "Thought for the Day" quotes.</summary>
        public static List<string> RecentQuoteKeys { get; } = new List<string>();

        /// <summary>Records a shown quote's key, moving it to the most-recent end and
        /// dropping the oldest beyond the cap.</summary>
        public static void RecordRecentQuote(string key)
        {
            if (string.IsNullOrEmpty(key))
            {
                return;
            }

            RecentQuoteKeys.Remove(key);
            RecentQuoteKeys.Add(key);
            while (RecentQuoteKeys.Count > MaxRecentQuotes)
            {
                RecentQuoteKeys.RemoveAt(0);
            }
        }

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

        /// <summary>Restores the recent-quote history persisted with the project.</summary>
        protected override Task OnReadSettingsAsync(ModuleSettingsReader settings)
        {
            RecentQuoteKeys.Clear();
            if (settings?.Get(RecentQuoteKeysKey) is string recentQuotes && !string.IsNullOrEmpty(recentQuotes))
            {
                RecentQuoteKeys.AddRange(recentQuotes.Split('|', StringSplitOptions.RemoveEmptyEntries));
            }
            return Task.FromResult(0);
        }

        /// <summary>Persists the recent-quote history with the project.</summary>
        protected override Task OnWriteSettingsAsync(ModuleSettingsWriter settings)
        {
            settings.Add(RecentQuoteKeysKey, string.Join("|", RecentQuoteKeys));
            return Task.FromResult(0);
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
