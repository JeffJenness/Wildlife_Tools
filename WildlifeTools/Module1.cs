// Wildlife and Forestry Tools for ArcGIS Pro
// Copyright (C) 2000-2026 Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version, together with the additional terms
// in LICENSE-ADDITIONAL-TERMS.md (preservation of the Lois Engelman
// Memorial Scholarship Fund notice, marking of modified versions, and
// use of names).
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <https://www.gnu.org/licenses/>.

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
    /// <summary>
    /// Last-used dialog parameters of one Background tool (Circle / Rectangle /
    /// Polygon), persisted with the project alongside the tool's symbol. The graphic
    /// NAME is deliberately not persisted. Only the fields a given tool uses are
    /// meaningful for it; the rest keep their defaults.
    /// </summary>
    public sealed class BackgroundToolPrefs
    {
        public double FillTransparency { get; set; }             // all tools
        public double OutlineTransparency { get; set; }          // all tools
        public double RadiusPercent { get; set; } = 100;         // circle (raw value; % or margin)
        public bool UseFocalPoint { get; set; } = true;          // circle
        public double HorizontalExtentPct { get; set; } = 100;   // rectangle (raw value; % or margin)
        public double VerticalExtentPct { get; set; } = 100;     // rectangle (raw value; % or margin)
        public int SizeUnitIndex { get; set; }                   // circle + rectangle: 0 = % of extent,
                                                                 // 1.. = BackgroundUnits linear units
        public double RoundingPct { get; set; }                  // rectangle
        public bool UseSymbolizedShape { get; set; }             // polygon
        public double BufferValue { get; set; }                  // polygon (raw value)
        public int LayoutUnitIndex { get; set; } = 2;            // polygon (Inches)
        public bool RemoveHoles { get; set; }                    // polygon
        // Drop shadow (rectangle first; raw dialog values -- Distance and Size
        // in points on a layout, map units on a map)
        public bool ShadowEnabled { get; set; }
        public double ShadowR { get; set; }
        public double ShadowG { get; set; }
        public double ShadowB { get; set; }
        public double ShadowOpacity { get; set; } = 60;
        public double ShadowAngle { get; set; } = 135;
        public double ShadowDistance { get; set; } = 5;
        public double ShadowSpread { get; set; }
        public double ShadowSize { get; set; } = 5;
        public int ShadowLayers { get; set; } = 10;
    }

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

        // Background tools: the last-used fill symbol of each tool (CIMSymbolReference
        // as JSON), persisted with the project and reloaded as the dialog's starting
        // symbol on the next run. Saved BEFORE the fill/outline transparencies are
        // applied, so the stored symbol is the design as the user built it.
        private const string CircleSymbolKey = "CircleBackground_SymbolJson";
        private const string RectangleSymbolKey = "RectangleBackground_SymbolJson";
        private const string PolygonSymbolKey = "PolygonBackground_SymbolJson";
        private const string CirclePrefsKey = "CircleBackground_Prefs";
        private const string RectanglePrefsKey = "RectangleBackground_Prefs";
        private const string PolygonPrefsKey = "PolygonBackground_Prefs";
        private const string McpSymbolKey = "McpBackground_SymbolJson";
        private const string McpPrefsKey = "McpBackground_Prefs";
        private const string LastImageFolderKey = "ImageSave_LastFolder";

        /// <summary>Folder of the last graph/table image saved from any of
        /// the suite's windows, persisted with the project (see
        /// ImageSaveFolder). Null until an image is saved.</summary>
        public static string LastImageFolder { get; set; }

        /// <summary>Circle Background's last-used dialog parameters (never null).</summary>
        public static BackgroundToolPrefs CirclePrefs { get; set; } = new();

        /// <summary>Rectangle Background's last-used dialog parameters (never null).</summary>
        public static BackgroundToolPrefs RectanglePrefs { get; set; } = new();

        /// <summary>Polygon Background's last-used dialog parameters (never null).</summary>
        public static BackgroundToolPrefs PolygonPrefs { get; set; } = new();

        private static BackgroundToolPrefs PrefsFromJson(string json)
        {
            if (string.IsNullOrEmpty(json))
                return new BackgroundToolPrefs();
            try
            {
                return System.Text.Json.JsonSerializer.Deserialize<BackgroundToolPrefs>(json)
                       ?? new BackgroundToolPrefs();
            }
            catch
            {
                return new BackgroundToolPrefs();
            }
        }

        /// <summary>Which background tool a persisted symbol belongs to.</summary>
        public enum BackgroundTool { Circle, Rectangle, Polygon, Mcp }

        /// <summary>Circle Background's last-used fill symbol (JSON), or null.</summary>
        public static string CircleSymbolJson { get; set; }

        /// <summary>Rectangle Background's last-used fill symbol (JSON), or null.</summary>
        public static string RectangleSymbolJson { get; set; }

        /// <summary>Polygon Background's last-used fill symbol (JSON), or null.</summary>
        public static string PolygonSymbolJson { get; set; }

        /// <summary>Minimum Convex Polygon's last-used fill symbol (JSON), or null.</summary>
        public static string McpSymbolJson { get; set; }

        /// <summary>Minimum Convex Polygon's last-used dialog parameters (never null).</summary>
        public static BackgroundToolPrefs McpPrefs { get; set; } = new();

        /// <summary>Stores a background tool's last-used symbol and marks the project
        /// dirty so the setting is persisted on the next project save.</summary>
        public static void SaveBackgroundSymbol(BackgroundTool tool, string symbolJson)
        {
            if (string.IsNullOrEmpty(symbolJson))
                return;
            switch (tool)
            {
                case BackgroundTool.Circle: CircleSymbolJson = symbolJson; break;
                case BackgroundTool.Rectangle: RectangleSymbolJson = symbolJson; break;
                case BackgroundTool.Polygon: PolygonSymbolJson = symbolJson; break;
                case BackgroundTool.Mcp: McpSymbolJson = symbolJson; break;
            }
            try
            {
                Project.Current?.SetDirty(true);
            }
            catch
            {
                // Non-fatal: the symbol still persists with the next ordinary save.
            }
        }

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

        /// <summary>Restores the recent-quote history and the background tools'
        /// last-used symbols persisted with the project.</summary>
        protected override Task OnReadSettingsAsync(ModuleSettingsReader settings)
        {
            RecentQuoteKeys.Clear();
            if (settings?.Get(RecentQuoteKeysKey) is string recentQuotes && !string.IsNullOrEmpty(recentQuotes))
            {
                RecentQuoteKeys.AddRange(recentQuotes.Split('|', StringSplitOptions.RemoveEmptyEntries));
            }
            CircleSymbolJson = settings?.Get(CircleSymbolKey) as string;
            RectangleSymbolJson = settings?.Get(RectangleSymbolKey) as string;
            PolygonSymbolJson = settings?.Get(PolygonSymbolKey) as string;
            McpSymbolJson = settings?.Get(McpSymbolKey) as string;
            CirclePrefs = PrefsFromJson(settings?.Get(CirclePrefsKey) as string);
            RectanglePrefs = PrefsFromJson(settings?.Get(RectanglePrefsKey) as string);
            PolygonPrefs = PrefsFromJson(settings?.Get(PolygonPrefsKey) as string);
            McpPrefs = PrefsFromJson(settings?.Get(McpPrefsKey) as string);
            LastImageFolder = settings?.Get(LastImageFolderKey) as string;
            return Task.FromResult(0);
        }

        /// <summary>Persists the recent-quote history and the background tools'
        /// last-used symbols with the project.</summary>
        protected override Task OnWriteSettingsAsync(ModuleSettingsWriter settings)
        {
            settings.Add(RecentQuoteKeysKey, string.Join("|", RecentQuoteKeys));
            if (!string.IsNullOrEmpty(CircleSymbolJson))
                settings.Add(CircleSymbolKey, CircleSymbolJson);
            if (!string.IsNullOrEmpty(RectangleSymbolJson))
                settings.Add(RectangleSymbolKey, RectangleSymbolJson);
            if (!string.IsNullOrEmpty(PolygonSymbolJson))
                settings.Add(PolygonSymbolKey, PolygonSymbolJson);
            if (!string.IsNullOrEmpty(McpSymbolJson))
                settings.Add(McpSymbolKey, McpSymbolJson);
            settings.Add(CirclePrefsKey, System.Text.Json.JsonSerializer.Serialize(CirclePrefs));
            settings.Add(RectanglePrefsKey, System.Text.Json.JsonSerializer.Serialize(RectanglePrefs));
            settings.Add(PolygonPrefsKey, System.Text.Json.JsonSerializer.Serialize(PolygonPrefs));
            settings.Add(McpPrefsKey, System.Text.Json.JsonSerializer.Serialize(McpPrefs));
            if (!string.IsNullOrEmpty(LastImageFolder))
                settings.Add(LastImageFolderKey, LastImageFolder);
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
