// Revit 2022 side: read the model into a ModelSnapshot, and apply a Plan inside one transaction.
// Stable IDs live in extensible storage on each Level / Grid, so they survive renames, copies of the file and sync.
using System;
using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.DB;
using Autodesk.Revit.DB.ExtensibleStorage;
using BaseOffice.RevitImport.Core;

namespace BaseOffice.RevitImport
{
    public class ApplyResult
    {
        public int LevelsCreated, LevelsUpdated, GridsCreated, GridsUpdated, ViewsCreated;
        public bool ProjectInfoUpdated;
        public List<string> Warnings = new List<string>();
    }

    public static class RevitModel
    {
        static readonly Guid SchemaGuid = new Guid("4E0B7C52-8F1D-4C6A-9B3E-2D5A7F10C3B1");
        const string ProjectStorageKey = "project";

        static double Ft(double mm) { return UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters); }
        static double Mm(double ft) { return UnitUtils.ConvertFromInternalUnits(ft, UnitTypeId.Millimeters); }

        static Schema GetOrCreateSchema()
        {
            Schema s = Schema.Lookup(SchemaGuid);
            if (s != null) return s;
            var b = new SchemaBuilder(SchemaGuid);
            b.SetSchemaName("BaseOfficeLink");
            b.SetDocumentation("BASE Design Office stable IDs for levels, grids and the project link.");
            b.SetReadAccessLevel(AccessLevel.Public);
            b.SetWriteAccessLevel(AccessLevel.Public);
            b.AddSimpleField("ProjectId", typeof(string));
            b.AddSimpleField("StableId", typeof(string));
            b.AddSimpleField("ProjectNumber", typeof(string));
            return b.Finish();
        }

        static bool ReadTag(Element e, Schema s, out string projectId, out string stableId, out string number)
        {
            projectId = stableId = number = null;
            if (s == null) return false;
            Entity ent = e.GetEntity(s);
            if (ent == null || !ent.IsValid()) return false;
            projectId = ent.Get<string>("ProjectId");
            stableId = ent.Get<string>("StableId");
            number = ent.Get<string>("ProjectNumber");
            return !string.IsNullOrEmpty(projectId);
        }

        static void WriteTag(Element e, Schema s, string projectId, string stableId, string number)
        {
            var ent = new Entity(s);
            ent.Set<string>("ProjectId", projectId);
            ent.Set<string>("StableId", stableId);
            ent.Set<string>("ProjectNumber", number ?? "");
            e.SetEntity(ent);
        }

        static DataStorage FindProjectStorage(Document doc, Schema s)
        {
            if (s == null) return null;
            foreach (DataStorage ds in new FilteredElementCollector(doc).OfClass(typeof(DataStorage)))
            {
                string p, id, n;
                if (ReadTag(ds, s, out p, out id, out n) && id == ProjectStorageKey) return ds;
            }
            return null;
        }

        public static ModelSnapshot Read(Document doc)
        {
            var m = new ModelSnapshot();
            Schema s = Schema.Lookup(SchemaGuid);
            foreach (Level l in new FilteredElementCollector(doc).OfClass(typeof(Level)).Cast<Level>())
            {
                var e = new ExistingLevel { ElementId = l.Id.IntegerValue.ToString(), Name = l.Name, ElevationMm = Mm(l.ProjectElevation), Pinned = l.Pinned };
                string n;
                ReadTag(l, s, out e.TagProjectId, out e.TagStableId, out n);
                m.Levels.Add(e);
            }
            foreach (Grid g in new FilteredElementCollector(doc).OfClass(typeof(Grid)).Cast<Grid>())
            {
                var e = new ExistingGrid { ElementId = g.Id.IntegerValue.ToString(), Name = g.Name, Pinned = g.Pinned };
                Line line = g.Curve as Line;
                if (line == null || g.IsCurved) e.IsStraight = false;
                else
                {
                    XYZ a = line.GetEndPoint(0), b = line.GetEndPoint(1);
                    e.X1 = Mm(a.X); e.Y1 = Mm(a.Y); e.X2 = Mm(b.X); e.Y2 = Mm(b.Y);
                }
                string n;
                ReadTag(g, s, out e.TagProjectId, out e.TagStableId, out n);
                m.Grids.Add(e);
            }
            ProjectInfo pi = doc.ProjectInformation;
            if (pi != null)
            {
                m.ProjectName = pi.Name; m.ProjectNumber = pi.Number; m.ClientName = pi.ClientName; m.Address = pi.Address;
            }
            DataStorage ds = FindProjectStorage(doc, s);
            if (ds != null)
            {
                string p, id, n;
                ReadTag(ds, s, out p, out id, out n);
                m.LinkedProjectId = p; m.LinkedProjectNumber = n;
            }
            return m;
        }

        public static ApplyResult Apply(Document doc, ImportFile f, Plan plan, bool createViews)
        {
            var res = new ApplyResult();
            var todo = plan.Items.Where(i => i.Action == PlanAction.Create || i.Action == PlanAction.Update).ToList();
            if (todo.Count == 0) return res;
            var collector = new WarningCollector();
            using (var t = new Transaction(doc, "BASE Office import " + f.ProjectNumber))
            {
                t.Start();
                var opts = t.GetFailureHandlingOptions();
                opts.SetFailuresPreprocessor(collector);
                t.SetFailureHandlingOptions(opts);
                Schema s = GetOrCreateSchema();

                // 1. free names that are being swapped / renamed
                foreach (var it in todo.Where(i => i.Rename && i.TargetElementId != null))
                {
                    Element el = doc.GetElement(new ElementId(int.Parse(it.TargetElementId)));
                    el.Name = "~BASE~" + Guid.NewGuid().ToString("N").Substring(0, 10);
                }

                // 2. project information + project link
                foreach (var it in todo.Where(i => i.Category == "Project info"))
                {
                    ProjectInfo pi = doc.ProjectInformation;
                    if (it.WriteProjectInfo)
                    {
                        pi.Name = f.ProjectName;
                        pi.Number = f.ProjectNumber;
                        pi.ClientName = f.ClientName;
                        if (!string.IsNullOrEmpty(f.Address)) pi.Address = f.Address;
                        res.ProjectInfoUpdated = true;
                    }
                    DataStorage ds = FindProjectStorage(doc, s) ?? DataStorage.Create(doc);
                    WriteTag(ds, s, f.ProjectStableId, ProjectStorageKey, f.ProjectNumber);
                }

                // 3. levels
                ViewFamilyType planType = new FilteredElementCollector(doc).OfClass(typeof(ViewFamilyType)).Cast<ViewFamilyType>()
                    .FirstOrDefault(v => v.ViewFamily == ViewFamily.FloorPlan);
                foreach (var it in todo.Where(i => i.Category == "Level"))
                {
                    bool create = it.Action == PlanAction.Create;
                    Level lv = create ? Level.Create(doc, Ft(it.Level.ElevationMm))
                                      : (Level)doc.GetElement(new ElementId(int.Parse(it.TargetElementId)));
                    if (create || it.SetElevation)
                    {
                        double delta = Ft(it.Level.ElevationMm) - lv.ProjectElevation;
                        if (Math.Abs(delta) > 1e-9) lv.Elevation = lv.Elevation + delta;
                    }
                    if (create || it.Rename) lv.Name = it.Level.Name;
                    WriteTag(lv, s, f.ProjectStableId, it.StableId, f.ProjectNumber);
                    if (create)
                    {
                        res.LevelsCreated++;
                        if (createViews && planType != null)
                        {
                            try { ViewPlan.Create(doc, planType.Id, lv.Id); res.ViewsCreated++; }
                            catch (Exception ex) { res.Warnings.Add("Plan view for " + it.Level.Name + ": " + ex.Message); }
                        }
                    }
                    else res.LevelsUpdated++;
                }

                // 4. grids
                foreach (var it in todo.Where(i => i.Category == "Grid"))
                {
                    Grid g = it.TargetElementId != null ? doc.GetElement(new ElementId(int.Parse(it.TargetElementId))) as Grid : null;
                    if (g != null && it.RecreateGrid)
                    {
                        doc.Delete(g.Id);
                        g = null;
                    }
                    bool created = false;
                    if (g == null)
                    {
                        Line line = Line.CreateBound(new XYZ(Ft(it.Grid.X1), Ft(it.Grid.Y1), 0), new XYZ(Ft(it.Grid.X2), Ft(it.Grid.Y2), 0));
                        g = Grid.Create(doc, line);
                        created = true;
                    }
                    else if (it.MoveGrid)
                    {
                        ElementTransformUtils.MoveElement(doc, g.Id, new XYZ(Ft(it.MoveDx), Ft(it.MoveDy), 0));
                    }
                    if (created || it.Rename) g.Name = it.Grid.Name;
                    WriteTag(g, s, f.ProjectStableId, it.StableId, f.ProjectNumber);
                    if (created && it.Action == PlanAction.Create) res.GridsCreated++; else res.GridsUpdated++;
                }

                TransactionStatus st = t.Commit();
                res.Warnings.AddRange(collector.Messages);
                if (st != TransactionStatus.Committed)
                    throw new InvalidOperationException("Revit rolled back the import: " + string.Join("; ", collector.Messages));
            }
            return res;
        }
    }

    class WarningCollector : IFailuresPreprocessor
    {
        public List<string> Messages = new List<string>();
        public FailureProcessingResult PreprocessFailures(FailuresAccessor a)
        {
            foreach (FailureMessageAccessor fm in a.GetFailureMessages())
            {
                Messages.Add(fm.GetSeverity() + ": " + fm.GetDescriptionText());
                if (fm.GetSeverity() == FailureSeverity.Warning) a.DeleteWarning(fm);
            }
            return FailureProcessingResult.Continue;
        }
    }
}
