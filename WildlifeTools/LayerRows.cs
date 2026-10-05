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

using System.Collections.Generic;
using ArcGIS.Core.Data;
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools
{
    /// <summary>
    /// Reads the rows of a map layer or standalone table AS THE MAP SHOWS THEM.
    ///
    /// STANDING RULE (2026-10-05; Jeff found it in Attribute Summary Reports,
    /// where a layer filtered to 101 of its 203 points was reported on as
    /// 203): NEVER read a layer through <c>layer.GetTable().Search(...)</c>
    /// or <c>layer.GetFeatureClass().Search(...)</c>. Those search the
    /// underlying table, and the layer's DEFINITION QUERY is not part of it.
    /// ArcGIS Pro merges the definition query into the filter only inside the
    /// layer's own Search / SearchEx (BasicFeatureLayer.SearchEx calls
    /// GetTable() and then DisplayTable.SearchImpl, which asks the map
    /// service to add the layer's definition filter -- spatial definition
    /// queries included -- before searching that same table). Because the
    /// rows come from the very table GetTable() returns, field indexes taken
    /// from <c>GetTable().GetDefinition()</c> stay valid for them, and joins
    /// are included either way.
    ///
    /// A selection is a different thing and is still the caller's business
    /// (put the selected ObjectIDs in the QueryFilter); the selected rows are
    /// always a subset of what the definition query lets through.
    ///
    /// All members must be called on the MCT (QueuedTask.Run).
    /// </summary>
    internal static class LayerRows
    {
        /// <summary>The layer's rows, definition query applied, optionally
        /// narrowed by <paramref name="filter"/>. <paramref name="recycling"/>
        /// false keeps every Row object distinct (needed when rows or their
        /// shapes are held past the next MoveNext).</summary>
        public static RowCursor Search(BasicFeatureLayer layer,
            QueryFilter filter = null, bool recycling = false)
        {
            if (layer == null) return null;
            return layer.SearchEx(filter, null, null, null, recycling);
        }

        /// <summary>The standalone table's rows, definition query applied.</summary>
        public static RowCursor Search(StandaloneTable table,
            QueryFilter filter = null, bool recycling = false)
        {
            if (table == null) return null;
            return table.SearchEx(filter, null, null, null, recycling);
        }

        public static bool HasDefinitionQuery(BasicFeatureLayer layer)
        {
            try { return layer?.ActiveDefinitionQuery != null; }
            catch { return false; }
        }

        public static bool HasDefinitionQuery(StandaloneTable table)
        {
            try { return table?.ActiveDefinitionQuery != null; }
            catch { return false; }
        }

        /// <summary>How many rows the layer shows (and, with
        /// <paramref name="whereClause"/>, how many of those also meet it).
        /// With no definition query and no where clause this is the table's
        /// own fast count; otherwise the rows are counted, reading only the
        /// ObjectID column.</summary>
        public static long Count(BasicFeatureLayer layer, string whereClause = null)
        {
            if (layer == null) return 0;
            bool plain = string.IsNullOrWhiteSpace(whereClause);
            using (Table t = layer.GetTable())
            {
                if (t == null) return 0;
                if (plain && !HasDefinitionQuery(layer)) return t.GetCount();
                return CountRows(Search(layer, CountFilter(t, whereClause), true));
            }
        }

        /// <summary>How many rows the standalone table shows.</summary>
        public static long Count(StandaloneTable table, string whereClause = null)
        {
            if (table == null) return 0;
            bool plain = string.IsNullOrWhiteSpace(whereClause);
            using (Table t = table.GetTable())
            {
                if (t == null) return 0;
                if (plain && !HasDefinitionQuery(table)) return t.GetCount();
                return CountRows(Search(table, CountFilter(t, whereClause), true));
            }
        }

        /// <summary>The ObjectIDs of the rows the layer shows, or NULL when
        /// the layer has no definition query (every row shows). For loops
        /// that must keep searching the underlying table directly -- a
        /// spatial query per feature, say -- and filter the hits afterward,
        /// which costs nothing when there is no definition query.</summary>
        public static HashSet<long> DefinitionOids(BasicFeatureLayer layer)
        {
            if (layer == null || !HasDefinitionQuery(layer)) return null;
            var set = new HashSet<long>();
            using (Table t = layer.GetTable())
            using (RowCursor cur = Search(layer, CountFilter(t, null), true))
            {
                if (cur == null) return set;
                while (cur.MoveNext())
                    using (Row row = cur.Current)
                        set.Add(row.GetObjectID());
            }
            return set;
        }

        // a filter that fetches only the ObjectID column: counting must not
        // materialize every geometry (a visible stall on large layers)
        private static QueryFilter CountFilter(Table t, string whereClause)
        {
            var qf = new QueryFilter();
            if (!string.IsNullOrWhiteSpace(whereClause))
                qf.WhereClause = whereClause;
            try
            {
                string oid = t?.GetDefinition()?.GetObjectIDField();
                if (!string.IsNullOrEmpty(oid)) qf.SubFields = oid;
            }
            catch { }
            return qf;
        }

        private static long CountRows(RowCursor cur)
        {
            long n = 0;
            if (cur == null) return n;
            using (cur)
                while (cur.MoveNext())
                    using (cur.Current) n++;
            return n;
        }
    }
}
