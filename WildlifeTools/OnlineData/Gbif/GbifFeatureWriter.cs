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
using System.Collections.Generic;

namespace WildlifeTools.OnlineData.Gbif
{
    /// <summary>
    /// What the GBIF Species Occurrences tool wrote, as the window reads it
    /// back from the output feature class for Save the source list (see
    /// GbifActions.SummarizeAsync).
    ///
    /// The records themselves are written by the geoprocessing tool, in
    /// Toolboxes\gbif_occurrences.py: its fields, its reading of GBIF's
    /// download files and its fallback for outlines of more than 10,000
    /// vertices live there and nowhere else. This file once held a second,
    /// C# writer of the same records, which only tests called. It was
    /// removed so that there is one writer to keep right.
    /// </summary>
    internal static class GbifFeatureWriter
    {
        /// <summary>What is in a written feature class: how many records,
        /// and how many from each source.</summary>
        internal sealed class Result
        {
            public long Written;
            public long WrittenWithoutLocation;     // rows with an empty shape (counted in Written too)
            public Dictionary<string, long> ByDataset = new Dictionary<string, long>(StringComparer.Ordinal);
            /// <summary>The name written for each dataset key (GBIF's title for the dataset,
            /// or the name the records themselves carry when no title was supplied).</summary>
            public Dictionary<string, string> DatasetNames = new Dictionary<string, string>(StringComparer.Ordinal);
        }
    }
}
