using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using ArcGIS.Core.Data;
using ArcGIS.Core.Data.Raster;
using ArcGIS.Core.Geometry;
using ArcGIS.Desktop.Mapping;
using WildlifeTools.PolarPlots;

namespace WildlifeTools.Corridor
{
    /// <summary>
    /// Data ingestion for the Cross-Tabulation tool: turns the five input
    /// scenarios of the original (single table / feature class; polygon x
    /// polygon; raster x raster; polygon x raster sampled per cell or per
    /// polygon with a zonal statistic) into paired (v1, v2, weight)
    /// observations. MCT only.
    ///
    /// Weights are geodesic (meters / square meters) or raster cells with
    /// a known cell area, so the display units convert exactly -- no
    /// "pseudo-degrees". The optional area-of-interest polygon clips
    /// features BEFORE their size is measured (manual p. 69) and masks
    /// raster cells on the analysis grid.
    ///
    /// Raster-based scenarios stream the template grid in strips
    /// (RasterStack.ReadStrip) and compress the pairs onto a 1024 x 1024
    /// MEAN-PRESERVING micro-grid: each occupied micro-cell keeps its
    /// weighted mean v1 and v2, so integer-valued rasters stay exact and
    /// continuous ones are accurate to range/1024 -- far finer than any
    /// sensible class breaks.
    /// </summary>
    internal static class CrosstabDataReader
    {
        private const int StripRows = 512;
        private const int Micro = 1024;
        private const double AuthalicR = 6371007.181;

        public enum Mode { SingleSource, TwoDatasets }
        public enum WeightMode { None, BySize, ByField }

        public enum RasterStatKind
        {
            CellValues,      // sample at each raster cell
            Min, Mean, Max, Range, StdDev, Sum,   // per-polygon zonal
        }

        public sealed class Request
        {
            public Mode Mode;
            // single source
            public PolarPlotData.Source Source;
            public string Field1, Field2;
            public WeightMode Weight = WeightMode.BySize;
            public string WeightField;
            public bool SelectedOnly;
            // two datasets (A = variable 1 / horizontal)
            public PolarPlotData.Source SourceA, SourceB;
            public string FieldA, FieldB;             // polygon sources
            public RasterStatKind RasterStat = RasterStatKind.CellValues;
            // common
            public Polygon Aoi;                       // any SR; null = none
            public string AoiName;
        }

        public static CrosstabData Read(Request req, Action<double> progress)
        {
            CrosstabData d;
            if (req.Mode == Mode.SingleSource)
                d = ReadSingle(req, progress);
            else if (req.SourceA.IsRaster && req.SourceB.IsRaster)
                d = ReadTwoRasters(req, progress);
            else if (!req.SourceA.IsRaster && !req.SourceB.IsRaster)
                d = ReadTwoPolygons(req, progress);
            else
                d = ReadPolyRaster(req, progress);
            if (d.V1.Length == 0)
                throw new InvalidOperationException(
                    "No paired observations were found" +
                    (req.Aoi != null ? " inside the analysis polygon." : "."));
            if (req.Aoi != null)
                d.SourceLines.Add("Option to restrict analysis to polygon "
                    + "boundary (" + (req.AoiName ?? "polygon") + ")");
            return d;
        }

        // =================================================================
        // single table / feature class
        // =================================================================
        private static CrosstabData ReadSingle(Request req,
                                               Action<double> progress)
        {
            var src = req.Source;
            var d = new CrosstabData();
            Table table = PolarPlotData.GetTable(src);
            if (table == null)
                throw new InvalidOperationException(
                    "The dataset's table could not be opened.");
            try
            {
                var def = table.GetDefinition();
                int f1 = def.FindField(req.Field1 ?? "");
                int f2 = def.FindField(req.Field2 ?? "");
                if (f1 < 0 || f2 < 0)
                    throw new InvalidOperationException("Field not found.");
                int fw = -1;
                if (req.Weight == WeightMode.ByField)
                {
                    fw = def.FindField(req.WeightField ?? "");
                    if (fw < 0)
                        throw new InvalidOperationException(
                            "Weight field not found: " + req.WeightField);
                }
                var fc = table as FeatureClass;
                GeometryType shp = fc != null
                    ? fc.GetDefinition().GetShapeType() : GeometryType.Unknown;
                bool bySize = req.Weight == WeightMode.BySize && fc != null
                    && (shp == GeometryType.Polygon
                        || shp == GeometryType.Polyline
                        || shp == GeometryType.Multipoint);

                Polygon aoi = ProjectAoi(req.Aoi, fc?.GetDefinition()
                    .GetSpatialReference());

                var qf = new QueryFilter();
                if (req.SelectedOnly && !src.IsRaster)
                {
                    var ids = PolarPlotData.GetSelectedIds(src);
                    if (ids != null && ids.Count > 0)
                        qf.ObjectIDs = ids.ToList();
                }

                var v1 = new List<double>();
                var v2 = new List<double>();
                var w = new List<double>();
                long done = 0;
                using (var cur = table.Search(qf, true))
                    while (cur.MoveNext())
                        using (var row = cur.Current)
                        {
                            if (((++done) & 511) == 0) progress?.Invoke(-1);
                            object a = row[f1], b = row[f2];
                            if (a == null || a is DBNull || b == null ||
                                b is DBNull) continue;
                            double wt = 1.0;
                            if (fc != null)
                            {
                                Geometry g = null;
                                try { g = (row as Feature)?.GetShape(); }
                                catch { }
                                if (g == null || g.IsEmpty) continue;
                                if (aoi != null)
                                {
                                    g = ClipToAoi(g, aoi, shp);
                                    if (g == null) continue;
                                }
                                if (bySize)
                                {
                                    wt = SizeOf(g, shp);
                                    if (wt <= 0) continue;
                                }
                            }
                            if (fw >= 0)
                            {
                                object wo = row[fw];
                                if (wo == null || wo is DBNull) continue;
                                wt = Convert.ToDouble(wo,
                                    CultureInfo.InvariantCulture);
                                if (wt <= 0) continue;
                            }
                            v1.Add(Convert.ToDouble(a,
                                CultureInfo.InvariantCulture));
                            v2.Add(Convert.ToDouble(b,
                                CultureInfo.InvariantCulture));
                            w.Add(wt);
                        }
                d.V1 = v1.ToArray();
                d.V2 = v2.ToArray();
                d.W = w.ToArray();
                d.Name1 = req.Field1;
                d.Name2 = req.Field2;
                if (bySize)
                {
                    d.Basis = shp == GeometryType.Polygon ? WeightBasis.AreaM2
                        : shp == GeometryType.Polyline ? WeightBasis.LengthM
                        : WeightBasis.Records;
                    d.WeightDesc = shp == GeometryType.Polygon
                        ? "Geodesic area"
                        : shp == GeometryType.Polyline
                            ? "Geodesic length" : "Point count";
                }
                else if (fw >= 0)
                {
                    d.WeightDesc = "Sum of [" + req.WeightField + "]";
                }
                else
                {
                    d.WeightDesc = fc != null ? "Feature count" : "Row count";
                }
                d.SourceLines.Add((fc != null
                        ? "Feature Class:  " : "Table:  ") + src.Name);
                d.SourceLines.Add("First Selected Attribute Field:  "
                                  + req.Field1);
                d.SourceLines.Add("Second Selected Attribute Field:  "
                                  + req.Field2);
                if (req.SelectedOnly)
                    d.SourceLines.Add("Selected records only");
                return d;
            }
            finally
            {
                if (!src.IsRaster) table.Dispose();
            }
        }

        // =================================================================
        // two polygon feature classes
        // =================================================================
        private static CrosstabData ReadTwoPolygons(Request req,
                                                    Action<double> progress)
        {
            var d = new CrosstabData();
            var la = req.SourceA.FeatureLayer;
            var lb = req.SourceB.FeatureLayer;
            if (la == null || lb == null ||
                la.ShapeType != ArcGIS.Core.CIM.esriGeometryType
                    .esriGeometryPolygon ||
                lb.ShapeType != ArcGIS.Core.CIM.esriGeometryType
                    .esriGeometryPolygon)
                throw new InvalidOperationException(
                    "Both datasets must be polygon layers.");

            using (var ta = la.GetTable())
            using (var tb = lb.GetTable())
            {
                var defA = ta.GetDefinition();
                var defB = tb.GetDefinition();
                int fa = defA.FindField(req.FieldA ?? "");
                int fb = defB.FindField(req.FieldB ?? "");
                if (fa < 0 || fb < 0)
                    throw new InvalidOperationException("Field not found.");
                var srA = ((FeatureClassDefinition)defA)
                    .GetSpatialReference();
                Polygon aoi = ProjectAoi(req.Aoi, srA);

                var v1 = new List<double>();
                var v2 = new List<double>();
                var w = new List<double>();
                long total = 0;
                try { total = ta.GetCount(); } catch { }
                long done = 0;
                using (var curA = ta.Search(null, true))
                    while (curA.MoveNext())
                        using (var rowA = (Feature)curA.Current)
                        {
                            done++;
                            if (total > 0 && (done & 15) == 0)
                                progress?.Invoke((double)done / total);
                            object a = rowA[fa];
                            if (a == null || a is DBNull) continue;
                            Geometry ga = rowA.GetShape();
                            if (ga == null || ga.IsEmpty) continue;
                            if (aoi != null)
                            {
                                ga = ClipToAoi(ga, aoi, GeometryType.Polygon);
                                if (ga == null) continue;
                            }
                            double va = Convert.ToDouble(a,
                                CultureInfo.InvariantCulture);
                            // spatial index does the candidate filtering
                            var sf = new SpatialQueryFilter
                            {
                                FilterGeometry = ga,
                                SpatialRelationship =
                                    SpatialRelationship.Intersects,
                            };
                            using (var curB = tb.Search(sf, true))
                                while (curB.MoveNext())
                                    using (var rowB = (Feature)curB.Current)
                                    {
                                        object b = rowB[fb];
                                        if (b == null || b is DBNull)
                                            continue;
                                        Geometry gb = rowB.GetShape();
                                        if (gb == null || gb.IsEmpty)
                                            continue;
                                        Geometry inter;
                                        try
                                        {
                                            if (gb.SpatialReference != null
                                                && srA != null)
                                                gb = GeometryEngine.Instance
                                                    .Project(gb, srA);
                                            inter = GeometryEngine.Instance
                                                .Intersection(ga, gb,
                                                    GeometryDimensionType
                                                    .EsriGeometry2Dimension);
                                        }
                                        catch { continue; }
                                        if (inter == null || inter.IsEmpty)
                                            continue;
                                        double area = SizeOf(inter,
                                            GeometryType.Polygon);
                                        if (area <= 0) continue;
                                        v1.Add(va);
                                        v2.Add(Convert.ToDouble(b,
                                            CultureInfo.InvariantCulture));
                                        w.Add(area);
                                    }
                        }
                d.V1 = v1.ToArray();
                d.V2 = v2.ToArray();
                d.W = w.ToArray();
            }
            // dataset name + field: two sources often share field names
            // (e.g. GRIDCODE in two patch maps), so the field alone is
            // ambiguous
            d.Name1 = req.SourceA.Name + " : " + req.FieldA;
            d.Name2 = req.SourceB.Name + " : " + req.FieldB;
            d.Basis = WeightBasis.AreaM2;
            d.WeightDesc = "Geodesic area of intersection";
            d.SourceLines.Add("First Data Source:  Polygon Feature Class");
            d.SourceLines.Add("      Name:  " + req.SourceA.Name);
            d.SourceLines.Add("      Attribute Field:  " + req.FieldA);
            d.SourceLines.Add("Second Data Source:  Polygon Feature Class");
            d.SourceLines.Add("      Name:  " + req.SourceB.Name);
            d.SourceLines.Add("      Attribute Field:  " + req.FieldB);
            return d;
        }

        // =================================================================
        // two rasters
        // =================================================================
        private static CrosstabData ReadTwoRasters(Request req,
                                                   Action<double> progress)
        {
            var ra = req.SourceA.RasterLayer.GetRaster();
            var rb = req.SourceB.RasterLayer.GetRaster();
            if (ra == null || rb == null)
                throw new InvalidOperationException(
                    "A raster could not be opened.");
            // template = first PROJECTED raster; both unprojected -> the
            // second (the original's rule, manual p. 72)
            bool aProj = !(ra.GetSpatialReference()?.IsGeographic ?? true);
            bool bProj = !(rb.GetSpatialReference()?.IsGeographic ?? true);
            Raster template = aProj ? ra : bProj ? rb : rb;
            Raster other = ReferenceEquals(template, ra) ? rb : ra;
            bool aIsTemplate = ReferenceEquals(template, ra);

            var d = StreamPairs(template, other, aIsTemplate, req.Aoi,
                                null, progress);
            d.Name1 = req.SourceA.Name;
            d.Name2 = req.SourceB.Name;
            d.SourceLines.Add("First Data Source:  Raster");
            d.SourceLines.Add("      Name:  " + req.SourceA.Name);
            d.SourceLines.Add("Second Data Source:  Raster");
            d.SourceLines.Add("      Name:  " + req.SourceB.Name);
            d.SourceLines.Add("Data Values:  Cell Values (grid of '"
                + (aIsTemplate ? req.SourceA.Name : req.SourceB.Name) + "')");
            return d;
        }

        // =================================================================
        // one polygon feature class + one raster
        // =================================================================
        private static CrosstabData ReadPolyRaster(Request req,
                                                   Action<double> progress)
        {
            // normalize: P = polygon source, R = raster source; v1 remains
            // Variable #1 = SourceA
            bool aIsPoly = !req.SourceA.IsRaster;
            var polySrc = aIsPoly ? req.SourceA : req.SourceB;
            var rastSrc = aIsPoly ? req.SourceB : req.SourceA;
            string polyField = aIsPoly ? req.FieldA : req.FieldB;
            var raster = rastSrc.RasterLayer.GetRaster();
            var lyr = polySrc.FeatureLayer;
            if (raster == null || lyr == null ||
                lyr.ShapeType != ArcGIS.Core.CIM.esriGeometryType
                    .esriGeometryPolygon)
                throw new InvalidOperationException(
                    "This scenario needs one polygon layer and one raster.");

            CrosstabData d = req.RasterStat == RasterStatKind.CellValues
                ? PolyRasterPerCell(lyr, polyField, raster, aIsPoly, req.Aoi,
                                    progress)
                : PolyRasterPerPolygon(lyr, polyField, raster, aIsPoly,
                                       req.RasterStat, req.Aoi, progress);
            string polyLabel = polySrc.Name + " : " + polyField;
            d.Name1 = aIsPoly ? polyLabel : req.SourceA.Name;
            d.Name2 = aIsPoly ? req.SourceB.Name : polyLabel;
            d.SourceLines.Add("First Data Source:  " + (aIsPoly
                ? "Polygon Feature Class" : "Raster"));
            d.SourceLines.Add("      Name:  " + req.SourceA.Name);
            if (aIsPoly)
                d.SourceLines.Add("      Attribute Field:  " + req.FieldA);
            d.SourceLines.Add("Second Data Source:  " + (aIsPoly
                ? "Raster" : "Polygon Feature Class"));
            d.SourceLines.Add("      Name:  " + req.SourceB.Name);
            if (!aIsPoly)
                d.SourceLines.Add("      Attribute Field:  " + req.FieldB);
            d.SourceLines.Add("Data Values:  " + StatWord(req.RasterStat));
            return d;
        }

        public static string StatWord(RasterStatKind k)
        {
            switch (k)
            {
                case RasterStatKind.Min: return "Polygon Minimum";
                case RasterStatKind.Mean: return "Polygon Mean";
                case RasterStatKind.Max: return "Polygon Maximum";
                case RasterStatKind.Range: return "Polygon Range";
                case RasterStatKind.StdDev: return "Polygon Std. Deviation";
                case RasterStatKind.Sum: return "Polygon Sum";
                default: return "Cell Values";
            }
        }

        /// <summary>Sample-at-cell: paint each polygon's attribute onto
        /// the raster grid (windowed scanline per polygon), then stream
        /// paired (cell value, polygon attribute) observations weighted by
        /// cell area. Cells with no polygon become "Other" via NaN.</summary>
        private static CrosstabData PolyRasterPerCell(FeatureLayer lyr,
            string field, Raster raster, bool polyIsV1, Polygon aoi,
            Action<double> progress)
        {
            var grid = GridInfo.Of(raster);
            var attr = new double[(long)grid.W * grid.H];
            for (long i = 0; i < attr.LongLength; i++) attr[i] = double.NaN;
            PaintPolygons(lyr, field, grid, attr, p =>
                progress?.Invoke(p * 0.35));

            bool[] aoiMask = aoi != null
                ? RasterStack.BuildClipMask(aoi, grid.Sr, grid.X0, grid.Cx,
                                            grid.Y0, grid.Cy, grid.W, grid.H)
                : null;

            var acc = new PairAccumulator();
            object noData = raster.GetNoDataValue();
            for (int y0 = 0; y0 < grid.H; y0 += StripRows)
            {
                int h = Math.Min(StripRows, grid.H - y0);
                RasterStack.ReadStrip(raster, grid.W, h, y0, out var vals,
                                      out var valid, noData, false);
                for (int r = 0; r < h; r++)
                {
                    long baseIdx = (long)(y0 + r) * grid.W;
                    double cw = grid.CellWeight(y0 + r);
                    for (int c = 0; c < grid.W; c++)
                    {
                        if (!valid[r, c]) continue;
                        if (aoiMask != null && !aoiMask[baseIdx + c])
                            continue;
                        double pv = attr[baseIdx + c];   // NaN = no polygon
                        double rv = vals[r, c];
                        acc.Add(polyIsV1 ? pv : rv, polyIsV1 ? rv : pv, cw);
                    }
                }
                progress?.Invoke(0.35 + 0.65 * Math.Min(y0 + h, grid.H)
                                 / grid.H);
            }
            var d = acc.ToData();
            grid.ApplyBasis(d);
            return d;
        }

        /// <summary>Sample-at-polygon: paint polygon INDICES onto the
        /// grid, stream once accumulating each polygon's zonal statistic,
        /// then one observation per polygon weighted by its (AOI-clipped)
        /// geodesic area. Polygons with no cells are ignored (manual
        /// p. 76).</summary>
        private static CrosstabData PolyRasterPerPolygon(FeatureLayer lyr,
            string field, Raster raster, bool polyIsV1, RasterStatKind stat,
            Polygon aoi, Action<double> progress)
        {
            var grid = GridInfo.Of(raster);
            var index = new int[(long)grid.W * grid.H];
            for (long i = 0; i < index.LongLength; i++) index[i] = -1;

            var polyVals = new List<double>();
            var polyAreas = new List<double>();
            Polygon aoiP = ProjectAoi(aoi, lyr.GetSpatialReference());
            PaintPolygons(lyr, field, grid, null, p =>
                progress?.Invoke(p * 0.35), index, polyVals, polyAreas, aoiP);

            bool[] aoiMask = aoi != null
                ? RasterStack.BuildClipMask(aoi, grid.Sr, grid.X0, grid.Cx,
                                            grid.Y0, grid.Cy, grid.W, grid.H)
                : null;

            int n = polyVals.Count;
            var cnt = new long[n];
            var sum = new double[n];
            var sumsq = new double[n];
            var mn = new double[n];
            var mx = new double[n];
            for (int i = 0; i < n; i++)
            {
                mn[i] = double.PositiveInfinity;
                mx[i] = double.NegativeInfinity;
            }
            object noData = raster.GetNoDataValue();
            for (int y0 = 0; y0 < grid.H; y0 += StripRows)
            {
                int h = Math.Min(StripRows, grid.H - y0);
                RasterStack.ReadStrip(raster, grid.W, h, y0, out var vals,
                                      out var valid, noData, false);
                for (int r = 0; r < h; r++)
                {
                    long baseIdx = (long)(y0 + r) * grid.W;
                    for (int c = 0; c < grid.W; c++)
                    {
                        if (!valid[r, c]) continue;
                        if (aoiMask != null && !aoiMask[baseIdx + c])
                            continue;
                        int k = index[baseIdx + c];
                        if (k < 0) continue;
                        double v = vals[r, c];
                        cnt[k]++;
                        sum[k] += v;
                        sumsq[k] += v * v;
                        if (v < mn[k]) mn[k] = v;
                        if (v > mx[k]) mx[k] = v;
                    }
                }
                progress?.Invoke(0.35 + 0.65 * Math.Min(y0 + h, grid.H)
                                 / grid.H);
            }

            var v1 = new List<double>();
            var v2 = new List<double>();
            var w = new List<double>();
            for (int k = 0; k < n; k++)
            {
                if (cnt[k] == 0) continue;           // no cells: ignored
                double s;
                switch (stat)
                {
                    case RasterStatKind.Min: s = mn[k]; break;
                    case RasterStatKind.Max: s = mx[k]; break;
                    case RasterStatKind.Range: s = mx[k] - mn[k]; break;
                    case RasterStatKind.Sum: s = sum[k]; break;
                    case RasterStatKind.StdDev:
                        s = cnt[k] > 1
                            ? Math.Sqrt(Math.Max(0,
                                (sumsq[k] - sum[k] * sum[k] / cnt[k])
                                / (cnt[k] - 1)))
                            : 0;
                        break;
                    default: s = sum[k] / cnt[k]; break;   // mean
                }
                double pv = polyVals[k];
                v1.Add(polyIsV1 ? pv : s);
                v2.Add(polyIsV1 ? s : pv);
                w.Add(polyAreas[k]);
            }
            var d = new CrosstabData
            {
                V1 = v1.ToArray(),
                V2 = v2.ToArray(),
                W = w.ToArray(),
                Basis = WeightBasis.AreaM2,
                WeightDesc = "Geodesic polygon area",
            };
            return d;
        }

        /// <summary>Rasterize every polygon of the layer onto the template
        /// grid, windowed to each polygon's row range. Fills EITHER
        /// ``attr`` (the polygon's field value per cell) OR ``index`` (the
        /// polygon's ordinal, with its value and AOI-clipped geodesic area
        /// recorded in the lists).</summary>
        private static void PaintPolygons(FeatureLayer lyr, string field,
            GridInfo grid, double[] attr, Action<double> progress,
            int[] index = null, List<double> polyVals = null,
            List<double> polyAreas = null, Polygon aoiForAreas = null)
        {
            using (var t = lyr.GetTable())
            {
                var def = (FeatureClassDefinition)t.GetDefinition();
                int fi = def.FindField(field ?? "");
                if (fi < 0)
                    throw new InvalidOperationException(
                        "Field not found: " + field);
                long total = 0;
                try { total = t.GetCount(); } catch { }
                long done = 0;
                using (var cur = t.Search(null, true))
                    while (cur.MoveNext())
                        using (var f = (Feature)cur.Current)
                        {
                            done++;
                            if (total > 0 && (done & 15) == 0)
                                progress?.Invoke((double)done / total);
                            object o = f[fi];
                            if (o == null || o is DBNull) continue;
                            var g = f.GetShape() as Polygon;
                            if (g == null || g.IsEmpty) continue;
                            double val = Convert.ToDouble(o,
                                CultureInfo.InvariantCulture);
                            Polygon gp = g;
                            try
                            {
                                if (grid.Sr != null &&
                                    g.SpatialReference != null)
                                    gp = (Polygon)GeometryEngine.Instance
                                        .Project(g, grid.Sr);
                            }
                            catch { }
                            int id = -1;
                            if (index != null)
                            {
                                Geometry forArea = g;
                                if (aoiForAreas != null)
                                {
                                    forArea = ClipToAoi(g, aoiForAreas,
                                        GeometryType.Polygon);
                                    if (forArea == null) continue;
                                }
                                id = polyVals.Count;
                                polyVals.Add(val);
                                polyAreas.Add(SizeOf(forArea,
                                    GeometryType.Polygon));
                            }
                            StampPolygon(gp, grid, attr, index, val, id);
                        }
            }
        }

        /// <summary>Scanline one polygon into the grid, restricted to its
        /// own row window (cheap even for many polygons).</summary>
        private static void StampPolygon(Polygon poly, GridInfo grid,
            double[] attr, int[] index, double val, int id)
        {
            Polygon p = poly;
            if (p.HasCurves)
            {
                try
                {
                    double step = Math.Min(grid.Cx, grid.Cy) * 0.5;
                    if (step > 0)
                        p = (Polygon)GeometryEngine.Instance
                            .DensifyByLength(p, step);
                }
                catch { }
            }
            var env = p.Extent;
            int r0 = (int)Math.Floor((grid.Y0 - env.YMax) / grid.Cy) - 1;
            int r1 = (int)Math.Ceiling((grid.Y0 - env.YMin) / grid.Cy) + 1;
            r0 = Math.Max(r0, 0);
            r1 = Math.Min(r1, grid.H - 1);
            if (r1 < r0) return;
            int hh = r1 - r0 + 1;
            var rings = new List<double[][]>();
            foreach (var part in p.Parts)
            {
                var pts = new List<double[]>();
                foreach (var seg in part)
                    pts.Add(new[] { seg.StartPoint.X, seg.StartPoint.Y });
                if (pts.Count >= 3) rings.Add(pts.ToArray());
            }
            if (rings.Count == 0) return;
            var mask = PolygonRasterizer.Rasterize(rings, grid.X0, grid.Cx,
                grid.Y0 - r0 * grid.Cy, grid.Cy, grid.W, hh);
            for (int r = 0; r < hh; r++)
            {
                long baseIdx = (long)(r0 + r) * grid.W;
                int rowOff = r * grid.W;
                for (int c = 0; c < grid.W; c++)
                    if (mask[rowOff + c])
                    {
                        if (attr != null) attr[baseIdx + c] = val;
                        if (index != null) index[baseIdx + c] = id;
                    }
            }
        }

        /// <summary>Stream the template + the other raster aligned onto
        /// its grid, compressing pairs onto the micro-grid.</summary>
        private static CrosstabData StreamPairs(Raster template,
            Raster other, bool templateIsV1, Polygon aoi, string unused,
            Action<double> progress)
        {
            var grid = GridInfo.Of(template);
            object ndT = template.GetNoDataValue();
            object ndO = other.GetNoDataValue();
            // align the other raster onto the template grid (nearest)
            other.SetSpatialReference(grid.Sr);
            other.SetExtent(template.GetExtent());
            other.SetWidth(grid.W);
            other.SetHeight(grid.H);
            other.SetResamplingType(
                ArcGIS.Core.CIM.RasterResamplingType.NearestNeighbor);
            other.Refresh();

            bool[] aoiMask = aoi != null
                ? RasterStack.BuildClipMask(aoi, grid.Sr, grid.X0, grid.Cx,
                                            grid.Y0, grid.Cy, grid.W, grid.H)
                : null;

            var acc = new PairAccumulator();
            for (int y0 = 0; y0 < grid.H; y0 += StripRows)
            {
                int h = Math.Min(StripRows, grid.H - y0);
                RasterStack.ReadStrip(template, grid.W, h, y0, out var tv,
                                      out var tvalid, ndT, false);
                RasterStack.ReadStrip(other, grid.W, h, y0, out var ov,
                                      out var ovalid, ndO, false);
                for (int r = 0; r < h; r++)
                {
                    long baseIdx = (long)(y0 + r) * grid.W;
                    double cw = grid.CellWeight(y0 + r);
                    for (int c = 0; c < grid.W; c++)
                    {
                        if (!tvalid[r, c] || !ovalid[r, c]) continue;
                        if (aoiMask != null && !aoiMask[baseIdx + c])
                            continue;
                        double a = templateIsV1 ? tv[r, c] : ov[r, c];
                        double b = templateIsV1 ? ov[r, c] : tv[r, c];
                        acc.Add(a, b, cw);
                    }
                }
                progress?.Invoke((double)Math.Min(y0 + h, grid.H) / grid.H);
            }
            var d = acc.ToData();
            grid.ApplyBasis(d);
            return d;
        }

        // =================================================================
        // helpers
        // =================================================================
        private sealed class GridInfo
        {
            public int W, H;
            public double X0, Y0, Cx, Cy;
            public SpatialReference Sr;
            public bool Geographic;
            public double CellAreaM2;      // projected only

            public static GridInfo Of(Raster r)
            {
                var g = new GridInfo
                {
                    W = r.GetWidth(),
                    H = r.GetHeight(),
                    Sr = r.GetSpatialReference(),
                };
                var ext = r.GetExtent();
                var cell = r.GetMeanCellSize();
                g.X0 = ext.XMin;
                g.Y0 = ext.YMax;
                g.Cx = cell.Item1;
                g.Cy = cell.Item2;
                g.Geographic = g.Sr?.IsGeographic ?? false;
                if (!g.Geographic && g.Sr != null)
                {
                    double f = (g.Sr.Unit as LinearUnit)?.ConversionFactor
                               ?? 1.0;
                    g.CellAreaM2 = Math.Abs(g.Cx * g.Cy) * f * f;
                }
                return g;
            }

            /// <summary>One cell's weight: 1 (projected; cell area known)
            /// or its per-row spherical area in m2 (geographic).</summary>
            public double CellWeight(int row)
            {
                if (!Geographic) return 1.0;
                double lat = Y0 - (row + 0.5) * Cy;
                double dx = Math.Abs(Cx) * Math.PI / 180.0;
                double dy = Math.Abs(Cy) * Math.PI / 180.0;
                return AuthalicR * AuthalicR * dx * dy
                       * Math.Cos(lat * Math.PI / 180.0);
            }

            public void ApplyBasis(CrosstabData d)
            {
                if (Geographic)
                {
                    d.Basis = WeightBasis.AreaM2;
                    d.WeightDesc = "Spherical cell area";
                }
                else
                {
                    d.Basis = WeightBasis.Cells;
                    d.CellAreaM2 = CellAreaM2;
                    d.WeightDesc = "Raster cells";
                }
            }
        }

        /// <summary>2-D mean-preserving compressor: pairs land in a
        /// Micro x Micro grid over the observed ranges; each occupied
        /// micro-cell keeps sum(w), sum(v1 w), sum(v2 w). NaN values (the
        /// per-cell "no polygon" marker) keep a dedicated band so they
        /// survive to the Other class.</summary>
        private sealed class PairAccumulator
        {
            private readonly List<double> _v1 = new List<double>();
            private readonly List<double> _v2 = new List<double>();
            private readonly List<double> _w = new List<double>();
            private Dictionary<long, (double w, double s1, double s2)> _bins;
            private double _min1, _max1, _min2, _max2;
            private double _nanW1, _nanW2;        // NaN on one side: track
            private readonly Dictionary<double, double> _nan1 =
                new Dictionary<double, double>();  // v2 value -> w (v1 NaN)
            private readonly Dictionary<double, double> _nan2 =
                new Dictionary<double, double>();  // v1 value -> w (v2 NaN)
            private const int RawCap = 200000;

            public void Add(double a, double b, double w)
            {
                if (w <= 0) return;
                bool na = double.IsNaN(a), nb = double.IsNaN(b);
                if (na && nb) return;
                if (na)
                {
                    double key = Quant(b);
                    _nan1[key] = _nan1.TryGetValue(key, out var x)
                        ? x + w : w;
                    _nanW1 += w;
                    return;
                }
                if (nb)
                {
                    double key = Quant(a);
                    _nan2[key] = _nan2.TryGetValue(key, out var x)
                        ? x + w : w;
                    _nanW2 += w;
                    return;
                }
                if (_bins == null)
                {
                    _v1.Add(a);
                    _v2.Add(b);
                    _w.Add(w);
                    if (_v1.Count >= RawCap) Compress();
                    return;
                }
                AddBinned(a, b, w);
            }

            private static double Quant(double v) => Math.Round(v, 9);

            private void Compress()
            {
                _min1 = _v1.Min(); _max1 = Math.Max(_v1.Max(), _min1 + 1e-9);
                _min2 = _v2.Min(); _max2 = Math.Max(_v2.Max(), _min2 + 1e-9);
                _bins = new Dictionary<long, (double, double, double)>();
                for (int i = 0; i < _v1.Count; i++)
                    AddBinned(_v1[i], _v2[i], _w[i]);
                _v1.Clear(); _v2.Clear(); _w.Clear();
            }

            private void AddBinned(double a, double b, double w)
            {
                int i1 = MicroIdx(a, _min1, _max1);
                int i2 = MicroIdx(b, _min2, _max2);
                long key = (long)i1 * (Micro + 2) + i2;
                _bins[key] = _bins.TryGetValue(key, out var x)
                    ? (x.Item1 + w, x.Item2 + a * w, x.Item3 + b * w)
                    : (w, a * w, b * w);
            }

            private static int MicroIdx(double v, double mn, double mx)
            {
                if (v <= mn) return 0;
                if (v >= mx) return Micro + 1;
                return 1 + (int)((v - mn) / (mx - mn) * Micro);
            }

            public CrosstabData ToData()
            {
                var v1 = new List<double>();
                var v2 = new List<double>();
                var w = new List<double>();
                if (_bins == null)
                {
                    v1.AddRange(_v1);
                    v2.AddRange(_v2);
                    w.AddRange(_w);
                }
                else
                {
                    foreach (var kv in _bins.Values)
                    {
                        v1.Add(kv.s1 / kv.w);
                        v2.Add(kv.s2 / kv.w);
                        w.Add(kv.w);
                    }
                }
                foreach (var kv in _nan1)
                {
                    v1.Add(double.NaN);
                    v2.Add(kv.Key);
                    w.Add(kv.Value);
                }
                foreach (var kv in _nan2)
                {
                    v1.Add(kv.Key);
                    v2.Add(double.NaN);
                    w.Add(kv.Value);
                }
                return new CrosstabData
                {
                    V1 = v1.ToArray(),
                    V2 = v2.ToArray(),
                    W = w.ToArray(),
                };
            }
        }

        private static Polygon ProjectAoi(Polygon aoi, SpatialReference sr)
        {
            if (aoi == null) return null;
            try
            {
                if (sr != null && aoi.SpatialReference != null)
                    return (Polygon)GeometryEngine.Instance.Project(aoi, sr);
            }
            catch { }
            return aoi;
        }

        /// <summary>Clip a shape to the AOI (BEFORE its size is measured,
        /// manual p. 69); null = disjoint.</summary>
        private static Geometry ClipToAoi(Geometry g, Polygon aoi,
                                          GeometryType shp)
        {
            try
            {
                if (GeometryEngine.Instance.Disjoint(g, aoi)) return null;
                if (shp == GeometryType.Polygon || shp == GeometryType.Polyline)
                {
                    var inter = GeometryEngine.Instance.Intersection(g, aoi,
                        shp == GeometryType.Polygon
                            ? GeometryDimensionType.EsriGeometry2Dimension
                            : GeometryDimensionType.EsriGeometry1Dimension);
                    return inter == null || inter.IsEmpty ? null : inter;
                }
                return g;
            }
            catch { return null; }
        }

        private static double SizeOf(Geometry g, GeometryType shp)
        {
            try
            {
                if (shp == GeometryType.Polygon)
                    return Math.Abs(GeometryEngine.Instance.GeodesicArea(g));
                if (shp == GeometryType.Polyline)
                    return Math.Abs(GeometryEngine.Instance.GeodesicLength(g));
                if (g is Multipoint mp) return mp.PointCount;
                return 1.0;
            }
            catch
            {
                try
                {
                    if (shp == GeometryType.Polygon)
                        return Math.Abs((g as Polygon)?.Area ?? 0);
                    if (shp == GeometryType.Polyline)
                        return Math.Abs((g as Polyline)?.Length ?? 0);
                }
                catch { }
                return 0;
            }
        }
    }
}
