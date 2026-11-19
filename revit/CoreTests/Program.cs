// Simulates Revit: applies each plan to an in-memory model the way the add-in applies it to a document,
// then checks counts, idempotency, conflicts and stable IDs.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BaseOffice.RevitImport.Core;

class FakeRevit
{
    public ModelSnapshot M = new ModelSnapshot();
    int next = 1000;

    public static FakeRevit BlankTemplate()
    {
        var r = new FakeRevit();
        // Revit 2022 metric architectural template ships with Level 1 / Level 2
        r.M.Levels.Add(new ExistingLevel { ElementId = "311", Name = "Level 1", ElevationMm = 0 });
        r.M.Levels.Add(new ExistingLevel { ElementId = "694", Name = "Level 2", ElevationMm = 4000 });
        r.M.ProjectName = "Project Name"; r.M.ProjectNumber = "Project Number"; r.M.ClientName = "Owner";
        return r;
    }

    public void Apply(ImportFile f, Plan p)
    {
        foreach (var it in p.Items.Where(i => i.Action == PlanAction.Create || i.Action == PlanAction.Update))
        {
            if (it.Category == "Project info")
            {
                M.ProjectName = f.ProjectName; M.ProjectNumber = f.ProjectNumber; M.ClientName = f.ClientName; M.Address = f.Address;
                M.LinkedProjectId = f.ProjectStableId; M.LinkedProjectNumber = f.ProjectNumber;
            }
            else if (it.Category == "Level")
            {
                ExistingLevel e = it.Action == PlanAction.Create ? null : M.Levels.Single(x => x.ElementId == it.TargetElementId);
                if (e == null) { e = new ExistingLevel { ElementId = (next++).ToString() }; M.Levels.Add(e); }
                if (M.Levels.Any(x => x != e && string.Equals(x.Name, it.Level.Name, StringComparison.OrdinalIgnoreCase) && !p.Items.Any(o => o.TargetElementId == x.ElementId && o.Rename)))
                    throw new Exception("Revit would reject duplicate level name " + it.Level.Name);
                e.Name = it.Level.Name; e.ElevationMm = it.Level.ElevationMm;
                e.TagProjectId = f.ProjectStableId; e.TagStableId = it.StableId;
            }
            else if (it.Category == "Grid")
            {
                ExistingGrid e = it.Action == PlanAction.Create ? null : M.Grids.Single(x => x.ElementId == it.TargetElementId);
                if (e != null && it.RecreateGrid) { M.Grids.Remove(e); e = null; }
                if (e == null) { e = new ExistingGrid { ElementId = (next++).ToString() }; M.Grids.Add(e); e.X1 = it.Grid.X1; e.Y1 = it.Grid.Y1; e.X2 = it.Grid.X2; e.Y2 = it.Grid.Y2; }
                if (it.MoveGrid) { e.X1 += it.MoveDx; e.X2 += it.MoveDx; e.Y1 += it.MoveDy; e.Y2 += it.MoveDy; }
                e.Name = it.Grid.Name; e.TagProjectId = f.ProjectStableId; e.TagStableId = it.StableId;
            }
        }
        var names = M.Levels.Select(l => l.Name.ToLowerInvariant()).ToList();
        if (names.Distinct().Count() != names.Count) throw new Exception("duplicate level names after apply");
        var gnames = M.Grids.Select(g => g.Name.ToLowerInvariant()).ToList();
        if (gnames.Distinct().Count() != gnames.Count) throw new Exception("duplicate grid names after apply");
    }
}

static class Program
{
    static int failures = 0;
    static void Check(bool ok, string what)
    {
        Console.WriteLine((ok ? "PASS  " : "FAIL  ") + what);
        if (!ok) failures++;
    }

    static int Main(string[] args)
    {
        string path = args.Length > 0 ? args[0] : Path.Combine("..", "samples", "villa-revit2022.json");
        string json = File.ReadAllText(path);
        var f = ImportFile.Parse(json);
        int nLevels = f.Levels.Count, nGrids = f.Grids.Count;
        Console.WriteLine("Sample: " + f.ProjectNumber + " | " + nLevels + " levels, " + nGrids + " grids");
        var o = new PlanOptions();

        // 1. blank Revit template, first import
        var rv = FakeRevit.BlankTemplate();
        var p1 = Planner.Build(f, rv.M, o);
        Console.WriteLine("Run 1 preview: " + p1.Summary());
        Check(p1.Count("Level", PlanAction.Create) == nLevels, "run 1 creates every level (" + nLevels + ")");
        Check(p1.Count("Grid", PlanAction.Create) == nGrids, "run 1 creates every grid (" + nGrids + ")");
        Check(p1.Count(PlanAction.Conflict) == 0, "run 1 has no conflicts");
        rv.Apply(f, p1);
        int levelsAfter1 = rv.M.Levels.Count, gridsAfter1 = rv.M.Grids.Count;
        Console.WriteLine("After run 1: " + levelsAfter1 + " levels in model (" + rv.M.Levels.Count(l => l.TagProjectId == f.ProjectStableId) + " BASE), " + gridsAfter1 + " grids");

        // 2. same file again: nothing to do, no duplicates
        var p2 = Planner.Build(f, rv.M, o);
        Console.WriteLine("Run 2 preview: " + p2.Summary());
        Check(!p2.HasChanges && p2.Count(PlanAction.Conflict) == 0, "run 2 is all Skip");
        rv.Apply(f, p2);
        Check(rv.M.Levels.Count == levelsAfter1 && rv.M.Grids.Count == gridsAfter1, "run 2 leaves counts unchanged (" + rv.M.Levels.Count + " levels, " + rv.M.Grids.Count + " grids)");
        Check(rv.M.Levels.Single(l => l.Name == "First Floor").ElevationMm == 4600, "First Floor at +4.600");
        Check(rv.M.ProjectNumber == f.ProjectNumber && rv.M.ClientName == f.ClientName, "project number and client written");

        // 3. revised export: first floor height changed, grid B renamed and moved 300 mm east
        var rev = ImportFile.Parse(json);
        rev.Levels.Single(l => l.Name == "First Floor").ElevationMm = 4800;
        var gb = rev.Grids.Single(g => g.Name == "B"); gb.Name = "C"; gb.X1 += 300; gb.X2 += 300;
        var p3 = Planner.Build(rev, rv.M, o);
        Console.WriteLine("Revised preview: " + p3.Summary());
        Check(p3.Count("Level", PlanAction.Update) == 1 && p3.Count("Grid", PlanAction.Update) == 1 && p3.Count(PlanAction.Create) == 0, "revision = 1 level update + 1 grid update, no creates");
        rv.Apply(rev, p3);
        Check(rv.M.Levels.Count == levelsAfter1 && rv.M.Grids.Count == gridsAfter1, "revision leaves counts unchanged");
        Check(!Planner.Build(rev, rv.M, o).HasChanges, "revision imported twice: second run all Skip");
        Check(rv.M.Grids.Single(g => g.Name == "C").X1 == 12300, "grid moved, not duplicated");

        // 4. existing model with an untagged level using the same name
        var rv4 = FakeRevit.BlankTemplate();
        rv4.M.Levels.Add(new ExistingLevel { ElementId = "900", Name = "Ground Floor", ElevationMm = 600 });
        var p4 = Planner.Build(f, rv4.M, o);
        Check(p4.Items.Single(i => i.Name == "Ground Floor").Action == PlanAction.Conflict, "untagged same-name level is flagged as conflict");
        var p4b = Planner.Build(f, rv4.M, new PlanOptions { AdoptSameName = true });
        var adopt = p4b.Items.Single(i => i.Name == "Ground Floor");
        Check(adopt.Action == PlanAction.Update && adopt.Adopt && adopt.TargetElementId == "900", "with 'link same name' the existing level is adopted, not duplicated");
        rv4.Apply(f, p4b);
        Check(rv4.M.Levels.Count(l => l.Name == "Ground Floor") == 1, "only one Ground Floor after adopting");

        // 5. model linked to another BASE project
        var rv5 = FakeRevit.BlankTemplate();
        rv5.M.LinkedProjectId = "another"; rv5.M.LinkedProjectNumber = "BASE-2026-099";
        var p5 = Planner.Build(f, rv5.M, o);
        Check(p5.Count(PlanAction.Conflict) == 1 && !p5.HasChanges, "model linked to another project: blocked with one conflict");

        // 6. provisional levels skipped unless ticked
        var prov = ImportFile.Parse(json);
        prov.Levels[0].Status = "provisional";
        var p6 = Planner.Build(prov, FakeRevit.BlankTemplate().M, o);
        Check(p6.Items.Single(i => i.StableId == prov.Levels[0].StableId).Action == PlanAction.Skip, "provisional level skipped by default");
        var p6b = Planner.Build(prov, FakeRevit.BlankTemplate().M, new PlanOptions { IncludeProvisional = true });
        Check(p6b.Items.Single(i => i.StableId == prov.Levels[0].StableId).Action == PlanAction.Create, "provisional level created when ticked");

        // 7. name swap between two BASE levels is allowed (two-phase rename in Revit)
        var swap = ImportFile.Parse(json);
        var a = swap.Levels.Single(l => l.Name == "First Floor"); var b = swap.Levels.Single(l => l.Name == "Second Floor");
        a.Name = "Second Floor"; b.Name = "First Floor";
        var rv7 = FakeRevit.BlankTemplate(); rv7.Apply(f, Planner.Build(f, rv7.M, o));
        var p7 = Planner.Build(swap, rv7.M, o);
        Check(p7.Count(PlanAction.Conflict) == 0 && p7.Count("Level", PlanAction.Update) == 2, "name swap planned as 2 updates, no conflict");

        // 8. grid direction changed: conflict unless 'recreate' is ticked
        var rot = ImportFile.Parse(json);
        var g1 = rot.Grids[0]; g1.X2 = g1.X1 + 5000;
        var p8 = Planner.Build(rot, rv7.M, o);
        Check(p8.Items.Single(i => i.StableId == g1.StableId).Action == PlanAction.Conflict, "rotated grid flagged");
        var p8b = Planner.Build(rot, rv7.M, new PlanOptions { RecreateChangedGrids = true });
        Check(p8b.Items.Single(i => i.StableId == g1.StableId).RecreateGrid, "rotated grid recreated when ticked");

        // 9. bad files are refused with a clear message
        bool refused = false;
        try { ImportFile.Parse(json.Replace("base-office/revit-import", "something-else")); } catch (FormatException) { refused = true; }
        Check(refused, "wrong schema refused");
        refused = false;
        try { ImportFile.Parse(json.Replace("\"schemaVersion\": \"1.0\"", "\"schemaVersion\": \"2.0\"")); } catch (FormatException) { refused = true; }
        Check(refused, "future major version refused");
        Check(!json.Contains("fee") && !json.Contains("payment") && !json.Contains("fils"), "sample export contains no financial fields");

        Console.WriteLine(failures == 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED");
        return failures == 0 ? 0 : 1;
    }
}
