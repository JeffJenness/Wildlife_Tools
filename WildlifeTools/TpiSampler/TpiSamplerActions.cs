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
using System.Linq;
using System.Threading.Tasks;
using ArcGIS.Core.Data.Raster;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Framework.Threading.Tasks;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.Corridor;

namespace WildlifeTools.TpiSampler
{
    /// <summary>
    /// Everything the TPI Neighborhood Sampler needs from ArcGIS Pro: the
    /// raster layers of the active map, the snapshot of a DEM inside the
    /// current view, and the help page. Kept apart from the panel so the
    /// panel can be built and drawn outside Pro.
    /// </summary>
    internal static class TpiSamplerActions
    {
        /// <summary>A raster layer of the active map, as the DEM list shows it.</summary>
        internal sealed class DemLayer
        {
            public string Name;
            public RasterLayer Layer;
            /// <summary>True when the layer is selected in the Contents pane,
            /// so the window can start on it.</summary>
            public bool Selected;
            public override string ToString() => Name;
        }

        /// <summary>The largest native window read cell by cell and averaged
        /// into blocks; a larger view is sampled every Factor-th cell instead.</summary>
        private const long MaxBlockMeanCells = 36_000_000;
        private const int StripRows = 256;

        public static Task<List<DemLayer>> ListDemLayersAsync()
        {
            return QueuedTask.Run(() =>
            {
                var list = new List<DemLayer>();
                MapView mv = MapView.Active;
                Map map = mv?.Map;
                if (map == null) return list;
                IReadOnlyList<Layer> selected = null;
                try { selected = mv.GetSelectedLayers(); } catch { }
                foreach (RasterLayer rl in map.GetLayersAsFlattenedList().OfType<RasterLayer>())
                {
                    list.Add(new DemLayer
                    {
                        Name = rl.Name,
                        Layer = rl,
                        Selected = selected != null && selected.Contains(rl),
                    });
                }
                return list;
            });
        }

        public static Task<SamplerGrid> SnapshotAsync(DemLayer dem, int targetCells)
        {
            return QueuedTask.Run(() => Snapshot(dem, targetCells));
        }

        /// <summary>
        /// Reads the DEM inside the active map view into a coarse grid of at
        /// most <paramref name="targetCells"/> cells on its longer side. Each
        /// snapshot cell is a whole number of DEM cells, averaged (or, for a
        /// very large view, sampled), so radii stay true on the ground.
        /// </summary>
        private static SamplerGrid Snapshot(DemLayer dem, int targetCells)
        {
            MapView mv = MapView.Active
                ?? throw new InvalidOperationException("Make a map view active first (not a layout).");
            Envelope view = mv.Extent
                ?? throw new InvalidOperationException("The map view has no extent.");
            Raster raster = dem.Layer.GetRaster()
                ?? throw new InvalidOperationException("The layer's raster could not be opened.");
            SpatialReference sr = raster.GetSpatialReference();
            Envelope rext = raster.GetExtent()
                ?? throw new InvalidOperationException("The raster has no extent.");
            int fullW = raster.GetWidth(), fullH = raster.GetHeight();
            if (fullW <= 0 || fullH <= 0)
                throw new InvalidOperationException("The raster has no cells.");

            Envelope env = view;
            if (sr != null && view.SpatialReference != null && !view.SpatialReference.IsEqual(sr))
            {
                env = GeometryEngine.Instance.Project(view, sr) as Envelope
                      ?? throw new InvalidOperationException("The view extent could not be projected to the DEM's coordinate system.");
            }
            double xmin = Math.Max(env.XMin, rext.XMin), xmax = Math.Min(env.XMax, rext.XMax);
            double ymin = Math.Max(env.YMin, rext.YMin), ymax = Math.Min(env.YMax, rext.YMax);
            if (!(xmin < xmax && ymin < ymax))
                throw new InvalidOperationException("The view does not overlap the DEM. Zoom to the DEM and try again.");

            double cellX = (rext.XMax - rext.XMin) / fullW;
            double cellY = (rext.YMax - rext.YMin) / fullH;
            int col0 = Clamp((int)Math.Floor((xmin - rext.XMin) / cellX), 0, fullW - 1);
            int col1 = Clamp((int)Math.Ceiling((xmax - rext.XMin) / cellX) - 1, 0, fullW - 1);
            int row0 = Clamp((int)Math.Floor((rext.YMax - ymax) / cellY), 0, fullH - 1);
            int row1 = Clamp((int)Math.Ceiling((rext.YMax - ymin) / cellY) - 1, 0, fullH - 1);
            int nw = col1 - col0 + 1, nh = row1 - row0 + 1;
            if (nw < 8 || nh < 8)
                throw new InvalidOperationException("The view covers only " + nw + " × " + nh
                    + " DEM cells. Zoom out so the snapshot has something to work with.");

            int target = Math.Max(50, targetCells);
            int k = Math.Max(1, (int)Math.Ceiling(Math.Max(nw, nh) / (double)target));
            int cw = nw / k, ch = nh / k;
            if (cw < 4 || ch < 4)
                throw new InvalidOperationException("The view is too narrow in one direction for a snapshot.");
            nw = cw * k;
            nh = ch * k;

            object noData = null;
            try { noData = raster.GetNoDataValue(); } catch { }

            SamplerGrid g;
            long cells = (long)nw * nh;
            if (cells <= MaxBlockMeanCells)
            {
                // every DEM cell in the window, averaged into k × k blocks
                var sum = new double[cw * ch];
                var cnt = new int[cw * ch];
                for (int y = 0; y < nh; y += StripRows)
                {
                    int hh = Math.Min(StripRows, nh - y);
                    RasterStack.ReadStrip(raster, nw, hh, row0 + y, out double[,] vals, out bool[,] ok,
                        noData, true, col0);
                    for (int r = 0; r < hh; r++)
                    {
                        int cr = (y + r) / k;
                        for (int c = 0; c < nw; c++)
                        {
                            if (!ok[r, c]) continue;
                            int i = cr * cw + c / k;
                            sum[i] += vals[r, c];
                            cnt[i]++;
                        }
                    }
                }
                var z = new float[cw * ch];
                int need = (k * k + 1) / 2;            // a block less than half NoData keeps its mean
                for (int i = 0; i < z.Length; i++)
                {
                    z[i] = cnt[i] >= Math.Max(1, need) ? (float)(sum[i] / cnt[i]) : float.NaN;
                }
                g = SamplerGrid.FromCoarse(z, cw, ch, k, sampled: false);
            }
            else
            {
                // a very large view: the center cell of each block, one row at a time
                var z = new float[cw * ch];
                for (int cr = 0; cr < ch; cr++)
                {
                    int y = row0 + cr * k + k / 2;
                    RasterStack.ReadStrip(raster, nw, 1, y, out double[,] vals, out bool[,] ok,
                        noData, true, col0);
                    for (int cc = 0; cc < cw; cc++)
                    {
                        int c = cc * k + k / 2;
                        z[cr * cw + cc] = ok[0, c] ? (float)vals[0, c] : float.NaN;
                    }
                }
                g = SamplerGrid.FromCoarse(z, cw, ch, k, sampled: true);
            }

            // the ground size of a snapshot cell
            if (sr != null && sr.IsGeographic)
            {
                Datum datum = sr.Datum;
                double a = datum != null && datum.SpheroidSemiMajorAxis > 0 ? datum.SpheroidSemiMajorAxis : 6378137.0;
                double b = datum != null && datum.SpheroidSemiMinorAxis > 0 ? datum.SpheroidSemiMinorAxis : 6356752.314245;
                double lonCenter = rext.XMin + (col0 + nw / 2.0) * cellX;
                double latTop = rext.YMax - row0 * cellY;
                g.SetGeographicCells(a, b, lonCenter, latTop, k * cellX, k * cellY);
                g.NativeUnitName = "degrees";
            }
            else
            {
                double mpu = 1.0;
                try
                {
                    if (sr?.Unit is LinearUnit lu && lu.ConversionFactor > 0) mpu = lu.ConversionFactor;
                }
                catch { }
                g.SetUniformCells(k * cellX * mpu, k * cellY * mpu);
                g.NativeUnitName = sr?.Unit?.Name ?? "map units";
            }
            g.NativeCellSize = (cellX + cellY) / 2.0;
            g.DemName = dem.Name;
            if (!g.Valid.Any(v => v))
                throw new InvalidOperationException("The DEM has no data inside the view.");
            return g;
        }

        private static int Clamp(int v, int lo, int hi) => v < lo ? lo : (v > hi ? hi : v);

        public static void OpenHelp() => WebHelp.Open("topographic-analysis/tpi-neighborhood-sampler.html");
    }
}
