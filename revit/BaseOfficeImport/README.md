# BASE Office Import - Revit 2022 add-in

Imports the **project number, name, client, address, levels and straight grids** from a BASE Office
"Export to Revit 2022" file (`BASE-2026-001-revit2022.json`) into an open Revit 2022 project, with a preview first.
Fees, payments, quotations and internal notes are never in that file.

## Why a new add-in instead of `revit-2022-bridge` v49
Your bridge (`revit-rebar-tools\revit-2022-bridge`) can already create levels (`level`, `set_level`) and grids
(`grid`), and it uses the same conventions (millimetres, Revit internal origin). But it matches nothing by ID, so running
the same batch twice creates duplicate levels and grids, it does not write project information, and it is driven from
PowerShell rather than a preview window. This add-in keeps those conventions, builds the same way (`csc.exe`, no
Visual Studio) and runs alongside the bridge without changing it.

## Install (once)
1. Copy this `BaseOfficeImport` folder somewhere permanent, e.g. `C:\Users\jalal\revit-rebar-tools\base-office-import`.
2. Close Revit. In PowerShell inside the folder:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\build.ps1
   ```
   It compiles `BaseOfficeImport.dll` against `C:\Program Files\Autodesk\Revit 2022\RevitAPI.dll` and writes
   `%APPDATA%\Autodesk\Revit\Addins\2022\BaseOfficeImport.addin`.
3. Start Revit 2022 and choose **Always Load** for "BASE Office Import". A **BASE Design** tab appears with
   **Import Project Data**.

## Units and origin
* Lengths in the file are millimetres.
* Level elevation = FFL (and "<level> - Slab Top" when a slab-top offset was entered) relative to project ±0.000,
  placed at the Revit **internal origin** Z = 0. Set the internal origin elevation to the site datum if needed.
* Grid coordinates are plan millimetres from the internal origin: +X = project east, +Y = project north.

## How matching works
Each level/grid created or linked by the importer carries a hidden BASE stable ID (Revit extensible storage), and the
model remembers which BASE project it belongs to. On the next import:

| Situation | Result |
|---|---|
| Same ID, same values | Skip |
| Same ID, new name / elevation / grid moved in a straight line | Update (no duplicate) |
| Grid direction or length changed | Conflict, or Update by recreating if you tick that option |
| No ID yet, a level/grid with the same name exists | Conflict, or linked if you tick "Link existing ... with the same name" |
| Provisional elevation | Skip, unless "Include provisional levels" is ticked |
| Model linked to another BASE project | Everything blocked unless you allow re-linking |
| In the model but removed from the export | Skip (never deleted automatically) |

## Exact checks to run on your PC (sample villa)
Use `..\samples\villa-revit2022.json` (BASE-2026-001: 10 levels = 6 FFL + 4 slab-top, 4 grids A, B, 1, 2).

1. New project from the default metric template (it has *Level 1* and *Level 2*). Import the sample.
   Preview should read **Create 14, Update 1, Skip 0, Conflict 0** (10 levels + 4 grids + project info).
   Apply. The result box should say **12 levels (10 from BASE Office), 4 grids (4 from BASE Office)** and
   "Repeat-import check: OK".
2. Import the **same file again**. Preview: **Create 0, Update 0, Skip 15, Conflict 0**; *Apply* is disabled.
   Counts stay 12 levels / 4 grids.
3. Elevation view: First Floor = **+4.600**, Ground Floor = **+0.600**, Basement = **-3.400**, Roof = **+12.600**.
   Manage > Project Information: Number **BASE-2026-001**, client name from the website.
4. Rename *Level 1* to *Ground Floor* in a fresh project and import: that row shows **Conflict** until you tick
   "Link existing levels and grids with the same name"; then it shows **Update** and no second Ground Floor appears.

Each import appends a log next to the JSON file: `villa-revit2022.json.import-log.txt`. Send it back if anything differs.

## Tested here (without Revit)
* `revit/CoreTests`: the matching/preview logic runs on the sample against a simulated model: two runs, a revised export,
  same-name conflicts, linked-project block, provisional levels, a name swap and a rotated grid (23 checks pass).
* `revit/CompileCheck`: all add-in source compiles as C# 5 / .NET Framework 4.8 (what `csc.exe` accepts) against a
  signatures-only Revit API stub.
* **Not yet run inside Revit 2022.** The live Revit calls (Level.Create, Grid.Create, extensible storage, project info,
  plan views) need the checks above on your machine.
