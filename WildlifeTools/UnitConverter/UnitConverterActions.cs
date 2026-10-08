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

using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using System.Threading.Tasks;
using ArcGIS.Core.Data;
using ArcGIS.Desktop.Core.Geoprocessing;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.UnitConverter
{
    /// <summary>
    /// Everything the Unit Converter window needs from ArcGIS Pro: the
    /// layers and tables of the active map with their numeric fields,
    /// running and opening the Convert Field Units tool, and the help page.
    /// Kept apart from the panel so the panel can be built and drawn
    /// outside Pro.
    /// </summary>
    internal static class UnitConverterActions
    {
        /// <summary>A layer or standalone table with numeric fields.</summary>
        internal sealed class TableChoice
        {
            public string Name { get; set; }
            public MapMember Member { get; set; }
            public List<string> Fields { get; set; } = new List<string>();
            public bool IsDbf { get; set; }
            public bool IsSelectedInContents { get; set; }
            public string RecordNote { get; set; } = "";
            public override string ToString() => Name;
        }

        private static readonly FieldType[] Numeric =
        {
            FieldType.SmallInteger, FieldType.Integer, FieldType.BigInteger, FieldType.Single, FieldType.Double,
        };

        public static Task<List<TableChoice>> ListTablesAsync()
        {
            return QueuedTask.Run(() =>
            {
                var list = new List<TableChoice>();
                MapView mv = MapView.Active;
                Map map = mv?.Map;
                if (map == null)
                {
                    return list;
                }
                IReadOnlyList<Layer> selected = null;
                try { selected = mv.GetSelectedLayers(); } catch { }
                IReadOnlyList<StandaloneTable> selTables = null;
                try { selTables = mv.GetSelectedStandaloneTables(); } catch { }
                var members = new List<MapMember>();
                members.AddRange(map.GetLayersAsFlattenedList().OfType<BasicFeatureLayer>());
                members.AddRange(map.GetStandaloneTablesAsFlattenedList());
                foreach (MapMember m in members)
                {
                    if (!(m is IDisplayTable dt))
                    {
                        continue;
                    }
                    var choice = new TableChoice { Name = m.Name, Member = m };
                    try
                    {
                        choice.Fields = dt.GetFieldDescriptions()
                            .Where(f => Numeric.Contains(f.Type))
                            .Select(f => f.Name).ToList();
                    }
                    catch
                    {
                        continue;
                    }
                    if (choice.Fields.Count == 0)
                    {
                        continue;
                    }
                    choice.IsSelectedInContents = (m is Layer l && selected != null && selected.Contains(l))
                                                  || (m is StandaloneTable st && selTables != null && selTables.Contains(st));
                    try
                    {
                        string path = (m as FeatureLayer)?.GetPath()?.LocalPath ?? (m as StandaloneTable)?.GetPath()?.LocalPath ?? "";
                        string p = path.ToLowerInvariant();
                        choice.IsDbf = p.EndsWith(".shp", StringComparison.Ordinal) || p.EndsWith(".dbf", StringComparison.Ordinal);
                    }
                    catch { }
                    long sel = 0;
                    try { sel = dt.GetSelection()?.GetCount() ?? 0; } catch { }
                    string defq = "";
                    try { defq = (m as BasicFeatureLayer)?.DefinitionQuery ?? (m as StandaloneTable)?.DefinitionQuery ?? ""; } catch { }
                    string what = m is StandaloneTable ? "table" : "layer";
                    choice.RecordNote = sel > 0
                        ? sel.ToString("N0", CultureInfo.CurrentCulture) + (sel == 1 ? " record is" : " records are")
                          + " selected in this " + what + ", and only " + (sel == 1 ? "it" : "those") + " will be converted."
                        : "No records are selected, so every record the " + what + " shows will be converted"
                          + (string.IsNullOrWhiteSpace(defq) ? "." : " (its definition query is honored).");
                    list.Add(choice);
                }
                return list;
            });
        }

        /// <summary>Runs the Convert Field Units tool, recorded in History,
        /// and returns a one-line report.</summary>
        public static async Task<string> RunConvertAsync(TableChoice t, string field, string from, string to, string outField)
        {
            string toolPath = ToolboxPaths.ToolPath("ConvertFieldUnits")
                ?? throw new InvalidOperationException("Could not find JennessEnterprisesTools.pyt next to the add-in.");
            IReadOnlyList<string> args = Geoprocessing.MakeValueArray(t.Member, field, "", from, to, outField);
            IGPResult result;
            try
            {
                result = await Geoprocessing.ExecuteToolAsync(toolPath, args, null,
                    (System.Threading.CancellationToken?)null, null,
                    GPExecuteToolFlags.Default | GPExecuteToolFlags.AddToHistory);
            }
            catch (NullReferenceException)
            {
                // the flags overloads have misfired before (see the Land Facet window)
                result = await Geoprocessing.ExecuteToolAsync(toolPath, args);
            }
            if (result.IsFailed)
            {
                string why = string.Join(" ", result.ErrorMessages.Select(m => m.Text).Take(3));
                throw new InvalidOperationException(string.IsNullOrWhiteSpace(why) ? "the tool failed." : why);
            }
            string done = result.Messages.Select(m => m.Text).LastOrDefault(s => s.StartsWith("Converted ", StringComparison.Ordinal))
                          ?? "Done.";
            string warn = string.Join(" ", result.Messages.Where(m => m.Type == GPMessageType.Warning).Select(m => m.Text));
            return done + " Into the field '" + outField + "' of " + t.Name + "." + (warn.Length > 0 ? " " + warn : "");
        }

        /// <summary>Opens the Convert Field Units tool with the window's settings.</summary>
        public static void OpenTool(TableChoice t, string field, string cls, string from, string to, string outField)
        {
            string toolPath = ToolboxPaths.ToolPath("ConvertFieldUnits")
                ?? throw new InvalidOperationException("Could not find JennessEnterprisesTools.pyt next to the add-in.");
            string c = cls == UnitCatalog.AllClasses ? "" : cls ?? "";
            Geoprocessing.OpenToolDialog(toolPath, Geoprocessing.MakeValueArray(
                (object)t?.Member ?? "", field ?? "", c, from ?? "", to ?? "", outField ?? ""));
        }

        public static void OpenHelp() => WebHelp.Open("data-tools/unit-converter.html");
    }
}
