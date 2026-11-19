// Import planning: compares the BASE Office export with what is already in the Revit model and decides,
// per record, Create / Update / Skip / Conflict. No Revit API here, so it is unit-tested outside Revit.
//
// Matching is by stable ID (stored on each level/grid in Revit extensible storage), never by name alone,
// so importing the same or a revised export twice does not duplicate levels or grids.
using System;
using System.Collections.Generic;
using System.Linq;

namespace BaseOffice.RevitImport.Core
{
    public enum PlanAction { Create, Update, Skip, Conflict }

    public class ExistingLevel
    {
        public string ElementId;
        public string Name;
        public double ElevationMm;     // relative to the internal origin
        public string TagProjectId;    // null when the level was not created/linked by BASE Office
        public string TagStableId;
        public bool Pinned;
    }

    public class ExistingGrid
    {
        public string ElementId;
        public string Name;
        public bool IsStraight = true;
        public double X1, Y1, X2, Y2;  // mm
        public string TagProjectId;
        public string TagStableId;
        public bool Pinned;
    }

    public class ModelSnapshot
    {
        public List<ExistingLevel> Levels = new List<ExistingLevel>();
        public List<ExistingGrid> Grids = new List<ExistingGrid>();
        public string ProjectName, ProjectNumber, ClientName, Address;
        public string LinkedProjectId;      // BASE project this model is linked to (null = never imported)
        public string LinkedProjectNumber;
    }

    public class PlanOptions
    {
        public bool IncludeProvisional;     // provisional elevations are skipped unless ticked
        public bool AdoptSameName;          // link untagged existing levels/grids with the same name instead of flagging
        public bool RecreateChangedGrids;   // delete + recreate grids whose direction/length changed
        public bool UpdateProjectInfo = true;
        public bool RelinkProject;          // allow importing into a model linked to another BASE project
    }

    public class PlanItem
    {
        public string Category;             // Project info | Level | Grid
        public PlanAction Action;
        public string StableId;
        public string Name;
        public string Detail;
        public string TargetElementId;      // existing element for Update
        public ImportLevel Level;
        public ImportGrid Grid;
        public bool Adopt;                  // write the BASE tag on an existing untagged element
        public bool Rename;
        public bool SetElevation;
        public bool MoveGrid;
        public double MoveDx, MoveDy;
        public bool RecreateGrid;
        public bool WriteProjectInfo;       // project name / number / client / address change
    }

    public class Plan
    {
        public List<PlanItem> Items = new List<PlanItem>();
        public int Count(PlanAction a) { return Items.Count(i => i.Action == a); }
        public int Count(string category, PlanAction a) { return Items.Count(i => i.Category == category && i.Action == a); }
        public bool HasChanges { get { return Items.Any(i => i.Action == PlanAction.Create || i.Action == PlanAction.Update); } }
        public string Summary()
        {
            return "Create " + Count(PlanAction.Create) + ", Update " + Count(PlanAction.Update) + ", Skip " + Count(PlanAction.Skip) +
                   ", Conflict " + Count(PlanAction.Conflict);
        }
    }

    public static class Planner
    {
        public const double Tol = 0.5; // mm

        public static Plan Build(ImportFile f, ModelSnapshot m, PlanOptions o)
        {
            var plan = new Plan();
            // ---- project link
            bool linkConflict = m.LinkedProjectId != null && m.LinkedProjectId != f.ProjectStableId && !o.RelinkProject;
            if (linkConflict)
            {
                plan.Items.Add(new PlanItem
                {
                    Category = "Project info", Action = PlanAction.Conflict, StableId = f.ProjectStableId, Name = f.ProjectNumber,
                    Detail = "This model is linked to BASE project " + (m.LinkedProjectNumber ?? m.LinkedProjectId) +
                             ". Open the right model, or tick 'Allow linking this model to a different BASE project'."
                });
                foreach (var l in f.Levels) plan.Items.Add(Blocked("Level", l.StableId, l.Name));
                foreach (var g in f.Grids) plan.Items.Add(Blocked("Grid", g.StableId, g.Name));
                return plan;
            }
            plan.Items.Add(ProjectInfo(f, m, o));
            PlanLevels(f, m, o, plan);
            PlanGrids(f, m, o, plan);
            return plan;
        }

        static PlanItem Blocked(string cat, string id, string name)
        {
            return new PlanItem { Category = cat, Action = PlanAction.Skip, StableId = id, Name = name, Detail = "Blocked by the project link conflict" };
        }

        static PlanItem ProjectInfo(ImportFile f, ModelSnapshot m, PlanOptions o)
        {
            var changes = new List<string>();
            if ((m.ProjectName ?? "") != f.ProjectName) changes.Add("Name: '" + m.ProjectName + "' -> '" + f.ProjectName + "'");
            if ((m.ProjectNumber ?? "") != f.ProjectNumber) changes.Add("Number: '" + m.ProjectNumber + "' -> '" + f.ProjectNumber + "'");
            if ((m.ClientName ?? "") != f.ClientName) changes.Add("Client: '" + m.ClientName + "' -> '" + f.ClientName + "'");
            if (!string.IsNullOrEmpty(f.Address) && (m.Address ?? "") != f.Address) changes.Add("Address -> '" + f.Address + "'");
            bool newLink = m.LinkedProjectId != f.ProjectStableId;
            var item = new PlanItem { Category = "Project info", StableId = f.ProjectStableId, Name = f.ProjectNumber + " " + f.ProjectName };
            if (!o.UpdateProjectInfo)
            {
                item.Action = newLink ? PlanAction.Update : PlanAction.Skip;
                item.Detail = newLink ? "Link this model to the BASE project (project information left unchanged)" : "Project information left unchanged (option)";
            }
            else if (changes.Count == 0 && !newLink)
            {
                item.Action = PlanAction.Skip;
                item.Detail = "Up to date";
            }
            else
            {
                item.Action = PlanAction.Update;
                item.WriteProjectInfo = changes.Count > 0;
                if (newLink) changes.Add(m.LinkedProjectId == null ? "Link model to BASE project" : "Re-link model from " + (m.LinkedProjectNumber ?? m.LinkedProjectId));
                item.Detail = string.Join("; ", changes);
            }
            return item;
        }

        // ------------------------------------------------------------------ levels
        static void PlanLevels(ImportFile f, ModelSnapshot m, PlanOptions o, Plan plan)
        {
            var items = new List<PlanItem>();
            var matched = new Dictionary<string, ExistingLevel>();   // stableId -> element
            foreach (var l in f.Levels)
            {
                var tagged = m.Levels.Where(e => e.TagProjectId == f.ProjectStableId && e.TagStableId == l.StableId).ToList();
                if (tagged.Count > 1)
                {
                    items.Add(new PlanItem { Category = "Level", Action = PlanAction.Conflict, StableId = l.StableId, Name = l.Name, Level = l,
                        Detail = tagged.Count + " levels carry the same BASE ID (copied elements?). Delete the extra ones." });
                    continue;
                }
                if (tagged.Count == 1) matched[l.StableId] = tagged[0];
            }
            // adoption candidates (untagged, same name) are resolved before name checks
            var adopted = new Dictionary<string, ExistingLevel>();
            foreach (var l in f.Levels)
            {
                if (matched.ContainsKey(l.StableId) || items.Any(i => i.StableId == l.StableId)) continue;
                if (l.IsProvisional && !o.IncludeProvisional) continue;
                var same = m.Levels.FirstOrDefault(e => SameName(e.Name, l.Name));
                if (same != null && same.TagProjectId == null && o.AdoptSameName && !matched.Values.Contains(same)) adopted[l.StableId] = same;
            }
            // final names of elements this import controls
            var finalName = new Dictionary<ExistingLevel, string>();
            foreach (var l in f.Levels)
            {
                if (l.IsProvisional && !o.IncludeProvisional) continue;
                ExistingLevel e;
                if (matched.TryGetValue(l.StableId, out e) || adopted.TryGetValue(l.StableId, out e)) finalName[e] = l.Name;
            }
            Func<string, ExistingLevel, ExistingLevel> holder = (name, self) =>
                m.Levels.FirstOrDefault(e => e != self && SameName(FinalName(e, finalName), name));

            foreach (var l in f.Levels)
            {
                if (items.Any(i => i.StableId == l.StableId)) continue;
                var it = new PlanItem { Category = "Level", StableId = l.StableId, Name = l.Name, Level = l };
                items.Add(it);
                ExistingLevel e;
                bool isMatched = matched.TryGetValue(l.StableId, out e);
                if (l.IsProvisional && !o.IncludeProvisional)
                {
                    it.Action = PlanAction.Skip;
                    it.Detail = "Provisional elevation (" + Fmt(l.ElevationMm) + ") - tick 'Include provisional levels' to import";
                    if (isMatched) it.TargetElementId = e.ElementId;
                    continue;
                }
                if (!isMatched && adopted.TryGetValue(l.StableId, out e)) it.Adopt = true;
                if (e != null)
                {
                    it.TargetElementId = e.ElementId;
                    var changes = new List<string>();
                    if (e.Name != l.Name)
                    {
                        var h = holder(l.Name, e);
                        if (h != null)
                        {
                            it.Action = PlanAction.Conflict;
                            it.Detail = "Rename '" + e.Name + "' -> '" + l.Name + "' blocked: another level already uses that name";
                            continue;
                        }
                        it.Rename = true;
                        changes.Add("rename '" + e.Name + "' -> '" + l.Name + "'");
                    }
                    if (Math.Abs(e.ElevationMm - l.ElevationMm) > Tol)
                    {
                        if (e.Pinned)
                        {
                            it.Action = PlanAction.Conflict;
                            it.Detail = "Level is pinned; unpin it to move " + Fmt(e.ElevationMm) + " -> " + Fmt(l.ElevationMm);
                            continue;
                        }
                        it.SetElevation = true;
                        changes.Add("elevation " + Fmt(e.ElevationMm) + " -> " + Fmt(l.ElevationMm));
                    }
                    if (it.Adopt) changes.Insert(0, "link existing untagged level");
                    it.Action = changes.Count > 0 ? PlanAction.Update : PlanAction.Skip;
                    it.Detail = changes.Count > 0 ? string.Join("; ", changes) : "Up to date (" + Fmt(l.ElevationMm) + ")";
                    continue;
                }
                var clash = holder(l.Name, null);
                if (clash != null)
                {
                    it.Action = PlanAction.Conflict;
                    it.TargetElementId = clash.ElementId;
                    it.Detail = clash.TagProjectId == null
                        ? "A level named '" + clash.Name + "' already exists and is not linked. Tick 'Link existing levels and grids with the same name', or rename it."
                        : "Name used by a level from another BASE record (" + clash.TagStableId + ")";
                    continue;
                }
                it.Action = PlanAction.Create;
                it.Detail = "New level at " + Fmt(l.ElevationMm) + " (" + l.Datum + ")";
            }
            foreach (var e in m.Levels.Where(x => x.TagProjectId == f.ProjectStableId && !f.Levels.Any(l => l.StableId == x.TagStableId)))
                items.Add(new PlanItem { Category = "Level", Action = PlanAction.Skip, StableId = e.TagStableId, Name = e.Name, TargetElementId = e.ElementId,
                    Detail = "In the model but no longer in the export - left unchanged (delete it manually if needed)" });
            plan.Items.AddRange(items);
        }

        static string FinalName(ExistingLevel e, Dictionary<ExistingLevel, string> finalName)
        {
            string n;
            return finalName.TryGetValue(e, out n) ? n : e.Name;
        }

        // ------------------------------------------------------------------ grids
        static void PlanGrids(ImportFile f, ModelSnapshot m, PlanOptions o, Plan plan)
        {
            var items = new List<PlanItem>();
            var matched = new Dictionary<string, ExistingGrid>();
            var dupIds = new HashSet<string>();
            foreach (var g in f.Grids)
            {
                var tagged = m.Grids.Where(e => e.TagProjectId == f.ProjectStableId && e.TagStableId == g.StableId).ToList();
                if (tagged.Count > 1) dupIds.Add(g.StableId);
                else if (tagged.Count == 1) matched[g.StableId] = tagged[0];
            }
            var adopted = new Dictionary<string, ExistingGrid>();
            foreach (var g in f.Grids)
            {
                if (matched.ContainsKey(g.StableId) || dupIds.Contains(g.StableId)) continue;
                var same = m.Grids.FirstOrDefault(e => SameName(e.Name, g.Name));
                if (same != null && same.TagProjectId == null && o.AdoptSameName && !matched.Values.Contains(same)) adopted[g.StableId] = same;
            }
            var finalName = new Dictionary<ExistingGrid, string>();
            foreach (var g in f.Grids)
            {
                ExistingGrid e;
                if (matched.TryGetValue(g.StableId, out e) || adopted.TryGetValue(g.StableId, out e)) finalName[e] = g.Name;
            }
            Func<string, ExistingGrid, ExistingGrid> holder = (name, self) =>
                m.Grids.FirstOrDefault(e => e != self && SameName(FinalGridName(e, finalName), name));

            foreach (var g in f.Grids)
            {
                var it = new PlanItem { Category = "Grid", StableId = g.StableId, Name = g.Name, Grid = g };
                items.Add(it);
                if (dupIds.Contains(g.StableId))
                {
                    it.Action = PlanAction.Conflict;
                    it.Detail = "Several grids carry the same BASE ID (copied elements?). Delete the extra ones.";
                    continue;
                }
                ExistingGrid e;
                if (!matched.TryGetValue(g.StableId, out e) && adopted.TryGetValue(g.StableId, out e)) it.Adopt = true;
                if (e != null)
                {
                    it.TargetElementId = e.ElementId;
                    var changes = new List<string>();
                    if (it.Adopt) changes.Add("link existing untagged grid");
                    if (e.Name != g.Name)
                    {
                        if (holder(g.Name, e) != null)
                        {
                            it.Action = PlanAction.Conflict;
                            it.Detail = "Rename '" + e.Name + "' -> '" + g.Name + "' blocked: another grid already uses that name";
                            continue;
                        }
                        it.Rename = true;
                        changes.Add("rename '" + e.Name + "' -> '" + g.Name + "'");
                    }
                    if (!SameGeometry(e, g))
                    {
                        double dx = 0, dy = 0;
                        bool translation = e.IsStraight && IsTranslation(e, g, out dx, out dy);
                        if (e.Pinned)
                        {
                            it.Action = PlanAction.Conflict;
                            it.Detail = "Grid is pinned; unpin it so its position can change";
                            continue;
                        }
                        if (translation && !it.Adopt)
                        {
                            it.MoveGrid = true; it.MoveDx = dx; it.MoveDy = dy;
                            changes.Add("move by (" + Fmt(dx) + ", " + Fmt(dy) + ")");
                        }
                        else if (o.RecreateChangedGrids && !it.Adopt)
                        {
                            it.RecreateGrid = true;
                            changes.Add("direction/length changed: delete and recreate (dimensions/tags on it will be lost)");
                        }
                        else
                        {
                            it.Action = PlanAction.Conflict;
                            it.Detail = it.Adopt
                                ? "A grid named '" + e.Name + "' exists in a different position. Rename or delete it, then import again."
                                : "Direction or length changed. Tick 'Recreate grids whose direction or length changed' or edit it manually.";
                            continue;
                        }
                    }
                    it.Action = changes.Count > 0 ? PlanAction.Update : PlanAction.Skip;
                    it.Detail = changes.Count > 0 ? string.Join("; ", changes) : "Up to date";
                    continue;
                }
                var clash = holder(g.Name, null);
                if (clash != null)
                {
                    it.Action = PlanAction.Conflict;
                    it.TargetElementId = clash.ElementId;
                    it.Detail = clash.TagProjectId == null
                        ? "A grid named '" + clash.Name + "' already exists and is not linked. Tick 'Link existing levels and grids with the same name', or rename it."
                        : "Name used by a grid from another BASE record";
                    continue;
                }
                it.Action = PlanAction.Create;
                it.Detail = "New grid (" + Fmt(g.X1) + ", " + Fmt(g.Y1) + ") -> (" + Fmt(g.X2) + ", " + Fmt(g.Y2) + ")";
            }
            foreach (var e in m.Grids.Where(x => x.TagProjectId == f.ProjectStableId && !f.Grids.Any(g => g.StableId == x.TagStableId)))
                items.Add(new PlanItem { Category = "Grid", Action = PlanAction.Skip, StableId = e.TagStableId, Name = e.Name, TargetElementId = e.ElementId,
                    Detail = "In the model but no longer in the export - left unchanged" });
            plan.Items.AddRange(items);
        }

        static string FinalGridName(ExistingGrid e, Dictionary<ExistingGrid, string> finalName)
        {
            string n;
            return finalName.TryGetValue(e, out n) ? n : e.Name;
        }

        static bool SameGeometry(ExistingGrid e, ImportGrid g)
        {
            if (!e.IsStraight) return false;
            bool same = Near(e.X1, g.X1) && Near(e.Y1, g.Y1) && Near(e.X2, g.X2) && Near(e.Y2, g.Y2);
            bool reversed = Near(e.X1, g.X2) && Near(e.Y1, g.Y2) && Near(e.X2, g.X1) && Near(e.Y2, g.Y1);
            return same || reversed;
        }

        static bool IsTranslation(ExistingGrid e, ImportGrid g, out double dx, out double dy)
        {
            dx = g.X1 - e.X1; dy = g.Y1 - e.Y1;
            if (Near(e.X2 + dx, g.X2) && Near(e.Y2 + dy, g.Y2)) return true;
            dx = g.X2 - e.X1; dy = g.Y2 - e.Y1;   // stored in the opposite direction
            if (Near(e.X2 + dx, g.X1) && Near(e.Y2 + dy, g.Y1)) return true;
            dx = dy = 0;
            return false;
        }

        static bool Near(double a, double b) { return Math.Abs(a - b) <= Tol; }

        // Revit level and grid names are unique ignoring case
        static bool SameName(string a, string b) { return string.Equals((a ?? "").Trim(), (b ?? "").Trim(), StringComparison.OrdinalIgnoreCase); }

        public static string Fmt(double mm)
        {
            return (mm >= 0 ? "+" : "") + (mm / 1000.0).ToString("0.000", System.Globalization.CultureInfo.InvariantCulture) + " m";
        }
    }
}
