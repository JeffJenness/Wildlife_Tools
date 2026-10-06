# Wildlife and Forestry Tools for ArcGIS Pro

A free add-in for ArcGIS Pro with over a hundred analytical, cartographic and
data-management tools for wildlife research, forestry, landscape analysis and
general GIS work, written by Jeff Jenness of
[Jenness Enterprises](https://www.jennessent.com), Flagstaff, Arizona.

Most of these tools began life as ArcView 3.x and ArcMap extensions over the
last three decades. They have been ported to ArcGIS Pro, rebuilt on modern
methods, and gathered on one ribbon tab. Every tool has a help page, and the
geoprocessing tools also carry their full help inside ArcGIS Pro (hover any
parameter, or open the tool's help panel).

The add-in is dedicated to the memory of Lois Engelman. If these tools are
useful to you, please consider a donation to the **Lois Engelman Memorial
Scholarship Fund**, which helps educate and train the next generation of land
managers and ecologists. See [Support](#support-the-lois-engelman-memorial-scholarship-fund)
below.

## Requirements and installation

- ArcGIS Pro 3.7 or newer. Every tool runs at the Basic license level with no
  extension licenses required.
- Download the current `.esriAddinX` package from the
  [Jenness Enterprises website](https://www.jennessent.com) and double-click
  it. ArcGIS Pro installs it, and the **Wildlife and Forestry** tab appears on the
  ribbon.
- The geoprocessing tools live in one Python toolbox, *Jenness Enterprises
  Tools*, which the ribbon buttons open. The toolbox can also be used directly
  from ModelBuilder or Python (`arcpy.ImportToolbox`); each tool's help page
  shows the call.

No other software is needed. The Python tools use only the arcpy, NumPy,
SciPy and GDAL that ship with ArcGIS Pro.

## The tools

The ribbon is organized into the groups below. Tools marked *(window)* open an
interactive window of their own; the rest are geoprocessing tools.

### Home Range

**Local Convex Hulls (LoCoH)**

- **LoCoH (Standard)** – the *k*-LoCoH home-range estimator of Getz and Wilmers.
- **LoCoH (Adaptive)** – the *a*-LoCoH estimator of Getz et al. (2007).
- **LoCoH (Fixed Sphere)** – the *r*-LoCoH (fixed sphere of influence) estimator.
- **LoCoH Parameter Explorer** *(window)* – charts the 50, 75 and 95 percent
  isopleth areas across a sweep of *k*, *a* or *r* values, the leveling-off
  analysis used to choose a parameter.

**Kernel Density**

- **Kernel Density Enhanced** – a kernel-density surface from points with a
  choice of kernel shape and bandwidth method.
- **KD to Proportion Surface** – converts a kernel-density raster into a
  proportion-under-the-surface (volume) raster.
- **KD Probability Contours** – home-range probability isopleths (5 to 100
  percent) as polylines and polygons.

**Mahalanobis**

- **Mahalanobis Distance Raster** – a raster of Mahalanobis distances from a set
  of landscape variables and reference points.
- **Mahalanobis Distance at Points** – the Mahalanobis value at each input point.
- **Calculate Statistical Matrices** – mean vector, covariance, inverse
  covariance and correlation tables from reference points.
- **Mahalanobis Chi-Square Transform** – rescales a Mahalanobis squared-distance
  raster to a 0–1 chi-square probability surface.

### Topographic Analysis

**Topographic Roughness**

- **Surface Area and Ratio** – the three-dimensional surface area of the
  terrain and its ratio to planimetric area (Jenness 2004).
- **Vector Ruggedness Measure (VRM)** – the three-dimensional dispersion of
  surface-normal vectors (Sappington et al. 2007).
- **Arc-Chord Ratio (ACR)** – a rugosity index decoupled from slope (Du Preez 2015).
- **Terrain Ruggedness Index (TRI)** – neighborhood terrain ruggedness, with
  four selectable methods.
- **Fractal Dimension** – the local fractal dimension of the terrain surface,
  by triangular prism, variogram, madogram or differential box counting.
- **LSRI on DEM** and **LSRI on Contours** – the Land Surface Ruggedness Index
  of Beasom, Wiggers and Giardino (1983), from a DEM or from contour lines.
- **Topography along Lines** – adds fields describing the topography along
  each polyline: lengths, slopes, elevation gain and loss, aspect statistics,
  sinuosity and more.
- **Split Lines into Topography Segments** – converts polylines into
  PolylineZ features with one segment per DEM cell crossing.

**Wetness, Slope and Extreme Values**

- **Topographic Wetness Index** – TWI, also called the Compound Topographic
  Index, from priority-flood flow routing.
- **Slope Zonal Statistics as Table** – slope statistics per zone, computed
  correctly in angle space.
- **Find Raster Extreme Values** – regional peaks and lows of any raster
  surface, with prominence and depth.

**TPI Tools**

- **Topographic Position Index** – each cell's elevation minus the mean of its
  neighborhood (Weiss 2001).
- **Slope Position Classification**, **Landform Classification** and
  **General Raster Classification** – classify a DEM or any raster with a saved
  classification system.
- **Classification System Builder** *(window)* – create, edit and share the
  reusable classification systems those tools apply.

**Aspect Tools** (all computed with circular statistics)

- **Aspect Focal Statistics** – neighborhood statistics for an aspect raster.
- **Extract Aspect Values to Points** – the aspect at each point.
- **Aspect Transformation** – one or more transformations of an aspect raster
  at once.
- **Aspect Zonal Statistics as Table** – circular statistics per zone.

### Corridor Designer Tools

The complete CorridorDesigner workflow, by Jenness, Majka and Beier, with the
Land Facet corridor tools by Jenness, Brost and Beier, rebuilt to run entirely
inside ArcGIS Pro.

**Habitat Suitability Modeling**

- **Create HSM Remap Table** *(window)* – an interactive remap-table builder.
- **Create Habitat Suitability Model** – a habitat suitability model (HSM) on
  the 0–100 scale from habitat factor rasters.
- **Combine Habitat Factors** – combines factor rasters already scored 0–100.
- **Normalize Existing HSM** – linearly stretches an HSM built elsewhere to
  the 0–100 scale.
- **Reclassify Features in HSM** – assigns a fixed score to every cell under a
  feature class.
- **Critical Habitat Feature** – rescales an HSM by distance from a critical
  habitat feature.
- **Create Habitat Patches** – turns an HSM into habitat patch polygons
  classified by size.

**Land Facet Corridor Modeling**

- **Land Facet Clustering** *(window)* – fuzzy c-means clustering of terrain
  variables into land facets, replacing the export-to-R step of the original.
- **Land Facet Density** – the proportion of the surrounding landscape in each
  facet.
- **Land Facet Mahalanobis** – one Mahalanobis cost surface per facet.

**Build and Modify Corridors**

- **Identify Termini Polygons** – the corridor termini within each wildland block.
- **Least-Cost Corridor** – least-cost corridor polygons between every pair of
  termini.
- **Fill Corridor Holes** – closes small interior holes in corridor polygons.
- **Union Corridors** – combines corridor feature classes into one seamless shape.

**Evaluate Corridors**

- **Patch Analysis** *(window)* – the route through a corridor that minimizes
  the patch-to-patch gaps an animal must cross.
- **Bottleneck Analysis** *(window)* – the route with the widest bottleneck,
  found on the corridor's medial axis.
- **Weighted Summary Statistics** – record statistics and geometry-weighted
  statistics of polyline or polygon fields, side by side.
- **Histograms and Statistics** *(window)* – side-by-side histograms of any
  rasters, feature layers or tables.

**Ancillary Corridor Tools**

- **Diversity Indices** – neighborhood diversity of a categorical raster (Hill
  numbers, Shannon, Simpson).
- **Invert Raster** – inverts a raster so that high values become low.
- **Clip Data to Analysis Area** *(window)* – clips any mix of rasters and
  feature classes to an analysis area in one run.
- **Cross-Tab Statistics** *(window)* – a contingency table of two variables
  across the landscape.
- **Cumulative Surface** *(window)* – how many input layers occur on each cell.
- **Corridor Analysis Data Report** *(window)* – a plain-language report of
  where a dataset stands in the Land Facet analysis chain.

### Ecological Analysis

**Kappa Tools**

- **Classification Accuracy (Kappa)** – the error matrix, Cohen's Kappa with
  variance, Z test and confidence interval, and the modern estimators.
- **Compare Accuracy Analyses** – Z tests between the Kappas of two or more
  analyses.
- **Estimate Sample Size** – the reference sample size for a defensible
  accuracy assessment, by four methods.

### Geometric Tools

**Tessellations**

- **Voronoi (Thiessen) Polygons** – Voronoi polygons clipped to a boundary,
  computed exactly on the sphere for geographic data.
- **Adaptive Voronoi Polygons** – subdivides a polygon into compact, nearly
  equal-area cells.
- **Repeating Shapes** – hexagons, squares, triangles or brick-course
  rectangles across an area, or arrays of sampling points and circles.

**Random Samples**

- **Random Point Generator** – uniform random points in polygons, along lines
  or within raster cells, with stratification and minimum-spacing options.
- **Random Sample Array Generator** – randomly placed arrays of sampling
  stations.
- **Spatially Balanced Sample (GRTS)** – a generalized random-tessellation
  stratified sample from a point, line or polygon frame.

**Geometry on Geometry**

- **Centerline and Width** – collapses polygons to centerlines carrying the
  local width.
- **Longest Line in Polygon (Fetch)** – the longest straight line inside each
  polygon, and fetch radials at points.
- **Radiating Lines and Points** – fans of evenly spaced lines from each point,
  optionally blocked by barriers.
- **Maximum Inscribed Geometry** – the largest circle, rectangle, convex
  polygon or ellipse inside each polygon.
- **Concave Hull** – the shape of a cloud of points, by three methods.
- **Estimated Shortest Path through Points** – orders points into a short
  survey route.

### Data Tools

- **Load Favorite Datasets** *(window)* – keep often-used datasets in named
  groups and add any combination to the map.
- **Attribute Summary Reports** *(window)* – counts and proportions of value
  classes in attribute fields, with geometry summaries.
- **Select Random Records** – random or systematic selection of features or
  rows.

### Terrain Graphs

- **Polar Plots** *(window)* – circular plots of directional data, nine plot
  types.
- **Slope Graphic** *(window)* – a visual summary of the slope in an area: a
  line at the mean slope, a wedge for the spread, and the confidence interval.

### Cartography

**Hillshade Variations**

- **Enhanced Hillshade** – a hillshade that is accurate on geographic
  (latitude-longitude) DEMs.
- **MDOW Hillshade** – the multi-directional oblique-weighted hillshade.
- **Multi-Sun Hillshade** – a weighted average of hillshades from two to eight
  sun positions.
- **Blurred Hillshade** – the softened hillshade of the Swiss style.
- **Simple Hypsometric Shading** – drapes a DEM over a hillshade as a group
  layer.

**Graphic Backgrounds** – draw a **Circle**, **Rectangle**, **Polygon** or
**Minimum Convex Polygon** behind selected map or layout elements and group
them.

**Cartographic Helpers**

- **Remove SLC** – moves the Service Layer Credits text out of the map frame.
- **Create Shadow Polygons** – concentric ring buffers with graded
  transparency, a soft shadow behind a feature class.
- **Add Graphics** and **North Arrows** – galleries of compass-rose graphics
  and north arrows for maps and layouts, which also hold your own registered
  images.
- **Manage Custom Graphics and North Arrows** *(window)* – registers your
  own images (a logo, a seal, a north arrow) in those galleries and sets each
  one's focal point.

### Take a Break

- **Thought for the Day** – a random quotation.
- **Sudoku** and **Sliding Puzzle** *(windows)* – the sliding puzzle is cut
  from a snapshot of your own map.
- **ArcMap Easter Egg** – a small tribute to a classic.
- **Lois Engelman Scholarship** – the Donate to the Lois Engelman Memorial
  Scholarship Fund tool: what the scholarship is, and where to give (see
  below).

## Help and tutorials

Every tool has an illustrated help page on the Jenness Enterprises website,
along with background pages on the methods (kernel density, LoCoH, Kappa,
topographic roughness, corridor design) and tutorials with sample data. The
help pages give the recommended citation for each tool and the literature it
implements.

## Support the Lois Engelman Memorial Scholarship Fund

The author's wife, best friend and partner of 36 years, Lois Engelman, died in
July 2026. The **Donate to the Lois Engelman Memorial Scholarship Fund** tool
on the ribbon opens a page about the fund, which supports forestry students
who want to focus on spatial ecology and wildlife. Any funds Jenness
Enterprises receives in connection with this add-in go to that scholarship.
Please keep that tool in place if you redistribute or modify the add-in.

## License

This program is free software under the
[GNU General Public License, version 3](LICENSE) or (at your option) any
later version, with the
[additional terms](LICENSE-ADDITIONAL-TERMS.md) permitted by section 7 of
that license: the scholarship-fund notice must be preserved, modified versions
must be marked as modified, and the Jenness Enterprises name may not be used
to promote derived works. The bundled fonts are under the SIL Open Font
License; see `WildlifeTools/Fonts/Licenses`.

## Author

Jeff Jenness, Jenness Enterprises, Flagstaff, Arizona.
jeffj@jennessent.com · https://www.jennessent.com
