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
using ArcGIS.Desktop.Mapping;

namespace WildlifeTools.OnlineData.Prism
{
    /// <summary>Gives a layer of a PRISM Climate Data file the metadata the
    /// tool wrote beside the file.
    ///
    /// Why (found on 22 November 2026, ArcGIS Pro 3.7). The tool writes the
    /// file's metadata beside it (prism.nc.xml): what a value of each
    /// variable is, the dates, the grids less than six months old, how to
    /// cite PRISM and the caution about change over time. A layer of a
    /// netCDF variable is set to show the metadata of its source, and for
    /// such a layer ArcGIS Pro finds none: its title and description come
    /// back empty, so View Metadata on the layer shows nothing of what the
    /// tool wrote. A Python tool cannot reach the layers the map makes when
    /// a file is added by hand, so it is done here, for the layers the tool
    /// adds and for the ones a user adds later.
    ///
    /// What is done, with the public functions of MapMember only: the
    /// layer is switched to metadata of its own and given the text of the
    /// file's metadata.
    ///
    /// The guards, all of which must hold:
    ///   1. The layer is a layer of a netCDF file that the tool wrote (the
    ///      caller has found the tool's marker in the file).
    ///   2. A metadata file lies beside the netCDF file and is an ArcGIS
    ///      metadata document.
    ///   3. The layer still shows the metadata of its source, as every
    ///      layer does when it is made. A layer that has metadata of its
    ///      own, which a user may have written, is never touched.
    /// Nothing is said to the user and nothing here can fail a tool: every
    /// error is swallowed, and the layer is then as ArcGIS Pro made
    /// it.</summary>
    internal static class PrismLayerMetadata
    {
        /// <summary>On the main CIM thread. Returns what was done, in a few
        /// words, for the log. Never throws.</summary>
        public static string Give(MapMember layer, string ncPath)
        {
            try
            {
                if (layer == null) return "metadata: no layer";
                string xml = PrismLayerFacts.ReadFileMetadata(ncPath, out string why);
                if (xml == null) return "metadata: " + why + ", so the layer is left as it is";
                if (!layer.SupportsMetadata) return "metadata: the layer takes none";
                if (!layer.GetUseSourceMetadata())
                    return "metadata: the layer has metadata of its own, which is left alone";
                layer.SetUseSourceMetadata(false);
                if (!layer.GetCanEditMetadata())
                {
                    layer.SetUseSourceMetadata(true);
                    return "metadata: the layer's cannot be changed, so it is left as it was";
                }
                layer.SetMetadata(xml);
                return "metadata: the layer was given the metadata of the file";
            }
            catch (Exception ex)
            {
                return "metadata: stopped by " + ex.GetType().Name + ": " + ex.Message;
            }
        }
    }
}
