# Classification System Schema (TPI / Slope Position / Landform)

**Status:** design contract (v1). This is the shared specification that BOTH
the C# "Classification System Builder" (writer) and the Python geoprocessing
"apply" tools (reader) implement against. Keep the two sides in lock-step; a
drift between writer and reader is the main failure mode. Any change to the
on-disk shape requires bumping `schemaVersion` and updating both sides.

This modernizes Jeff Jenness's original ArcView 3 "TPI v.1.3a" *Saved Criteria
Sets* (which stored criteria in portable dBASE tables: `tpi_slope_criteria`,
`tpi_landform_criteria`, `tpi_class_criteria`). Here the container is JSON,
the store is a fixed per-user location that survives add-in reinstalls, and
the classification is applied cell-by-cell in vectorized numpy (`np.select`),
**not** with Esri `Con` / Spatial Analyst.

---

## 1. What a "classification system" is

A **classification system** is a named, reusable set of rules that turns one
or more input rasters into a single categorical raster (each cell gets one
class value). Each class is a boolean AND of per-input range conditions; on
overlap, the lowest class **value** wins (precedence). This is exactly the
Weiss (2001) slope-position / landform model and its relatives.

Three **families**:

| family | inputs | preloaded defaults |
|---|---|---|
| `slope_position` | one TPI (+ optional Slope) | Weiss 6-class; Dickson & Beier 4-class |
| `landform` | small-neighborhood TPI + large-neighborhood TPI (+ optional Slope) | Weiss 10-class |
| `general` | one arbitrary raster (+ optional additional rasters) | basic 4-class; basic 7-class |

---

## 2. Storage & location

- **Root:** `%LOCALAPPDATA%\JennessEnterprises\WildlifeTools\`
  - C#: `Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData)`
  - Python: `os.environ["LOCALAPPDATA"]`
  - This is **outside** the add-in install folder, so user-created systems
    **survive add-in reinstalls / upgrades**.
- **Systems folder:** `.../WildlifeTools/classification_systems/`
- **One system per file:** `<sanitized-name>.json`, UTF-8. The authoritative
  display name is the `name` field inside; the filename is only a convenience.
- **Sharing:** copy the `.json` file. That's the whole transport story.
- **Seeding defaults:** on first run (either side), if a default system file is
  absent, write it from a bundled copy. **Never overwrite an existing file** —
  the user may have edited it. Default files carry `"builtin": true` so the UI
  can offer "restore to default" without clobbering user systems.

---

## 3. Top-level object

```json
{
  "schemaVersion": 1,
  "name": "Weiss 10-Class Landform",
  "family": "landform",
  "description": "Landform classification from two TPI scales plus slope.",
  "source": "Weiss, A. 2001. Topographic Position and Landforms Analysis. ESRI Int'l User Conf. poster.",
  "builtin": true,
  "created": "2026-07-23",
  "modified": "2026-07-23",
  "noDataName": "Unclassified",
  "inputs": [ ... ],
  "classes": [ ... ]
}
```

| field | type | notes |
|---|---|---|
| `schemaVersion` | int | **required.** Currently `1`. |
| `name` | string | **required.** Display name; unique within the store. |
| `family` | enum | **required.** `slope_position` \| `landform` \| `general`. |
| `description` | string | optional. |
| `source` | string | optional citation. |
| `builtin` | bool | optional; `true` for seeded defaults. |
| `created` / `modified` | ISO date string | stamped by the builder. |
| `noDataName` | string | optional label for cells matching no class. |
| `inputs` | array | **required.** The roles this system consumes (§4). |
| `classes` | array | **required.** The class definitions (§5). |

---

## 4. `inputs` — the input signature

Declares which rasters the system needs, so the apply tool can prompt for
exactly those (this replaces the old "additional raster referenced by name →
halt and ask" behavior with explicit role→raster mapping).

```json
"inputs": [
  { "role": "tpi_small", "kind": "tpi",   "label": "Small-neighborhood TPI" },
  { "role": "tpi_large", "kind": "tpi",   "label": "Large-neighborhood TPI" },
  { "role": "slope",     "kind": "slope", "label": "Slope (degrees)", "optional": true }
]
```

| field | notes |
|---|---|
| `role` | machine id referenced by each criterion's `input`. |
| `kind` | `tpi` \| `slope` \| `external`. Determines how the apply tool obtains it. |
| `label` | display text. |
| `optional` | if `true`, the role may be unused by some classes. |

**How the apply tool obtains each input** (§7): `tpi` and `slope` inputs are
**computed internally from a DEM** by default, OR the user may supply a
pre-made raster for that role. `external` inputs (e.g. a soils raster) **must**
be supplied as a raster.

Canonical roles per family:
- `slope_position`: `tpi` (required), `slope` (optional).
- `landform`: `tpi_small` (required), `tpi_large` (required), `slope` (optional).
- `general`: `raster` (required), plus any number of extra `external` roles.
  (The role was named `grid` before 2026-07-29; both readers -- Python
  `classification_store._normalize_roles` and C#
  `ClassificationStore.NormalizeRoles` -- migrate `grid` to `raster` in memory
  on read, so old files keep working and rewrite themselves on the next save.)

---

## 5. `classes` — the class definitions

An ordered list. Each class:

```json
{
  "value": 5,
  "name": "Plains",
  "color": [200, 200, 140],
  "criteria": [ ... ]
}
```

| field | type | notes |
|---|---|---|
| `value` | int | **required.** Output cell value AND precedence key. Lowest value wins on overlap. |
| `name` | string | **required.** Class label (written to the output attribute table). |
| `color` | `[r,g,b]` 0–255 | optional. Used to pre-symbolize the output raster on add-to-map. |
| `alpha` | number 0–100 | optional. Percent OPACITY (100 = opaque, the default when omitted; matches CIM and Pro's transparency UI). Carried into the output colormap and unique-value symbology, e.g. to let a hillshade show through selected classes. The builder omits it at 100. |
| `criteria` | array | **required.** A cell is in this class iff **ALL** criteria hold (logical AND across inputs). |

**Precedence / evaluation:** the apply tool evaluates classes in ascending
`value` order and assigns the first class whose criteria all hold
(`np.select`), so the lowest `value` wins any overlap — matching the original
"lowest Classification Value takes precedence" rule. `value`s need not be
contiguous; NoData is written where no class matches (labeled `noDataName`).

### 5.1 `criteria` — per-input conditions

Each criterion tests **one input role**:

```json
{
  "input": "tpi_small",
  "units": "standardized",
  "conditions": [
    { "op": ">", "value": -1 },
    { "join": "and", "op": "<", "value": 1 }
  ]
}
```

| field | notes |
|---|---|
| `input` | a `role` from `inputs`. |
| `units` | how to interpret this input's values (§6). Valid values depend on the input's kind: `tpi` = raw | standardized | sd | pctl; `slope` = degrees | percent (`raw` accepted as a legacy alias from pre-2026-07-29 files); `external` = raw | standardized. |
| `conditions` | 1 or 2 comparison conditions. |

- **`conditions`**: a list of 1 or 2 objects `{ "op", "value" }`. The second
  (if present) carries `"join": "and" \| "or"` describing how it combines with
  the first. One condition = a single threshold; two = a range
  (e.g. `-1 < x < 1` is `{">",-1}` **and** `{"<",1}`).
- **`op`**: `"<"` \| `"<="` \| `">"` \| `">="` \| `"="` \| `"!="`.
- A criterion holds for a cell when its `conditions` (combined by `join`) are
  true for that input's value at that cell. A class holds when **all** its
  criteria hold.

---

## 6. `units` — interpreting an input's values

For a `tpi` input, `units` selects which derived quantity the thresholds apply
to. Exact definitions (from Jenness's corrected ArcMap `TPI_Code.bas`):

| `units` | quantity thresholded | formula | available when |
|---|---|---|---|
| `raw` | TPI itself | `TPI = DEM − FocalMean(DEM, nbhd)` | always |
| `standardized` | whole-raster standardized TPI | `(TPI − meanTPI) / sdTPI` (mean/SD over the entire TPI raster) | always |
| `sd` | standardized *elevation* (local, per neighborhood) | `TPI / FocalSD(DEM, nbhd)` (0 where FocalSD = 0) | DEM-computed only |

- `FocalMean` / `FocalSD` ignore NoData within the neighborhood.
- `sd` requires the neighborhood's SD values, so it is **only available when the
  tool computes TPI from the DEM on the fly** — not when a pre-made TPI raster
  is supplied. The apply tool must reject a system that uses `sd` against a
  supplied TPI raster (matching the original's "invalid criteria set" guard).
- For `slope` and `external` inputs, `units` is `raw` (actual values; slope in
  **degrees**).

**Ecological note (drives help text):** `raw` interprets absolute relief
consistently — a 100 m hill reads the same everywhere — and is usually the
better fit for wildlife questions. `standardized`/`sd`/`var` are relative to
local variability, so a 1 m bump in flat terrain and a 100 m hill in rugged
terrain can fall in the same class. Present `raw` prominently; Weiss's own
defaults use `standardized`.

---

## 7. How the apply tool consumes a system (Python GP tool)

1. **Pick a system** from a dropdown populated by scanning the store folder.
2. **Provide inputs.** For each `tpi`/`slope` role: either give a DEM (default,
   compute internally) or supply a pre-made raster. For `landform`, the two TPI
   roles use a **small** and a **large** neighborhood the user specifies at
   apply time (neighborhood = scale, chosen per the question — it is NOT stored
   in the system, so a system is reusable at any scale). `external` roles must
   be supplied as rasters.
3. **Neighborhood** (apply-time parameter, for DEM-computed TPI): shape
   (`circle` \| `annulus` \| `rectangle` \| `wedge` \| `custom`) and radius in
   `cells` or map units. v1 implements at least **circle** and **annulus**
   (Weiss used annulus). Exact numpy neighborhoods avoid the Spatial-Analyst
   circle/annulus cell-rounding issue noted in the original manual.
4. **Derive** each input's needed quantities (`raw`/`standardized`/`sd`)
   via focal statistics (NoData-ignoring), and slope in degrees.
5. **Classify** with vectorized `np.select`: build each class's boolean mask
   (AND of its criteria), evaluate in ascending `value` order, first match
   wins; NoData elsewhere.
6. **Output** an integer raster with a value/name attribute table and, if
   `color` is present per class, a pre-built colormap so it is symbolized on
   add-to-map. Zero Spatial Analyst throughout.

---

## 8. Worked examples

### 8.1 Weiss 6-class Slope Position (`slope_position`)

Thresholds in standardized units at ±1, ±0.5 SD, with a 5° slope split
between Flat and Middle Slope in the middle band.

```json
{
  "schemaVersion": 1,
  "name": "Weiss 6-Class Slope Position",
  "family": "slope_position",
  "source": "Weiss 2001",
  "builtin": true,
  "inputs": [
    { "role": "tpi",   "kind": "tpi",   "label": "TPI" },
    { "role": "slope", "kind": "slope", "label": "Slope (degrees)" }
  ],
  "classes": [
    { "value": 1, "name": "Valley",       "color": [43,131,186],
      "criteria": [ { "input": "tpi", "units": "standardized", "conditions": [ {"op": "<=", "value": -1} ] } ] },
    { "value": 2, "name": "Lower Slope",  "color": [128,191,171],
      "criteria": [ { "input": "tpi", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<=", "value": -0.5} ] } ] },
    { "value": 3, "name": "Flat Slope",   "color": [255,255,191],
      "criteria": [
        { "input": "tpi",   "units": "standardized", "conditions": [ {"op": ">", "value": -0.5}, {"join": "and", "op": "<", "value": 0.5} ] },
        { "input": "slope", "units": "raw",          "conditions": [ {"op": "<=", "value": 5} ] } ] },
    { "value": 4, "name": "Middle Slope", "color": [253,204,138],
      "criteria": [
        { "input": "tpi",   "units": "standardized", "conditions": [ {"op": ">", "value": -0.5}, {"join": "and", "op": "<", "value": 0.5} ] },
        { "input": "slope", "units": "raw",          "conditions": [ {"op": ">", "value": 5} ] } ] },
    { "value": 5, "name": "Upper Slope",  "color": [240,124,74],
      "criteria": [ { "input": "tpi", "units": "standardized", "conditions": [ {"op": ">=", "value": 0.5}, {"join": "and", "op": "<=", "value": 1} ] } ] },
    { "value": 6, "name": "Ridge",        "color": [215,25,28],
      "criteria": [ { "input": "tpi", "units": "standardized", "conditions": [ {"op": ">", "value": 1} ] } ] }
  ]
}
```

### 8.2 Weiss 10-class Landform (`landform`)

Two TPI scales (SN = small neighborhood, LN = large neighborhood), standardized,
±1 SD, plus a 5° slope split between Plains and Open Slopes.

```json
{
  "schemaVersion": 1,
  "name": "Weiss 10-Class Landform",
  "family": "landform",
  "source": "Weiss 2001 (slightly modified)",
  "builtin": true,
  "inputs": [
    { "role": "tpi_small", "kind": "tpi",   "label": "Small-neighborhood TPI" },
    { "role": "tpi_large", "kind": "tpi",   "label": "Large-neighborhood TPI" },
    { "role": "slope",     "kind": "slope", "label": "Slope (degrees)", "optional": true }
  ],
  "classes": [
    { "value": 1,  "name": "Canyons, Deeply Incised Streams",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": "<=", "value": -1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": "<=", "value": -1} ] } ] },
    { "value": 2,  "name": "Midslope Drainages, Shallow Valleys",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": "<=", "value": -1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<", "value": 1} ] } ] },
    { "value": 3,  "name": "Upland Drainages, Headwaters",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": "<=", "value": -1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": ">=", "value": 1} ] } ] },
    { "value": 4,  "name": "U-shaped Valleys",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<", "value": 1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": "<=", "value": -1} ] } ] },
    { "value": 5,  "name": "Plains",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<", "value": 1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<", "value": 1} ] },
        { "input": "slope",     "units": "raw",          "conditions": [ {"op": "<=", "value": 5} ] } ] },
    { "value": 6,  "name": "Open Slopes",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<", "value": 1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<", "value": 1} ] },
        { "input": "slope",     "units": "raw",          "conditions": [ {"op": ">", "value": 5} ] } ] },
    { "value": 7,  "name": "Upper Slopes, Mesas",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<", "value": 1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": ">=", "value": 1} ] } ] },
    { "value": 8,  "name": "Local Ridges, Hills in Valleys",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": ">=", "value": 1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": "<=", "value": -1} ] } ] },
    { "value": 9,  "name": "Midslope Ridges, Small Hills in Plains",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": ">=", "value": 1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": ">", "value": -1}, {"join": "and", "op": "<", "value": 1} ] } ] },
    { "value": 10, "name": "Mountain Tops, High Ridges",
      "criteria": [
        { "input": "tpi_small", "units": "standardized", "conditions": [ {"op": ">=", "value": 1} ] },
        { "input": "tpi_large", "units": "standardized", "conditions": [ {"op": ">=", "value": 1} ] } ] }
  ]
}
```

> The Weiss 10-class classes are mutually exclusive by construction, so
> precedence never actually fires here; it matters for hand-authored systems
> with overlapping criteria.

---

## 9. Validation rules (both sides enforce)

1. `schemaVersion` present and understood (reader rejects unknown major).
2. `family` is one of the three; `inputs` roles match the family's allowed set
   (`general` may add arbitrary `external` roles).
3. Every criterion's `input` refers to a declared role.
4. Each criterion has 1 or 2 `conditions`; a 2nd condition has a `join`.
5. Class `value`s are unique.
6. A system using `sd`/`var` units cannot be applied against a **supplied** TPI
   raster for that role (DEM-computed only) — surfaced as a clear tool error.
7. `color`, if present, is three ints 0–255; `alpha`, if present, is a number 0–100.

---

## 10. Versioning & forward compatibility

- `schemaVersion` is a single integer. Additive, backward-compatible fields do
  not bump it; any change that an old reader would misinterpret does.
- Readers ignore unknown top-level/optional fields (so the builder can add
  advisory metadata without breaking older apply tools), but MUST reject an
  unknown `schemaVersion`.
- Keep this document and both implementations updated together.

---

## 11. Open items to finalize during build

- Extract the exact **Dickson & Beier 4-class** and the **basic 4/7-class**
  general defaults from `TPI_Code.bas` (`Create_Sample_4_Class_SlopeClass`,
  etc.) and ship them as seed files.
- Confirm default **colors** for each seeded system (the VB6 forms only stored
  names; pick a sensible ramp per family, editable in the builder).
- Decide `wedge`/`custom` neighborhood support timing (v1 = circle + annulus).
- Whether `general` family should allow multiple TPI-derived roles or only
  externally-supplied rasters.
