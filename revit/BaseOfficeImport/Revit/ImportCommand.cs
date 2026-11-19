using System;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using BaseOffice.RevitImport.Core;

namespace BaseOffice.RevitImport
{
    [Transaction(TransactionMode.Manual)]
    public class ImportCommand : IExternalCommand
    {
        public Result Execute(ExternalCommandData data, ref string message, ElementSet elements)
        {
            UIDocument uidoc = data.Application.ActiveUIDocument;
            if (uidoc == null || uidoc.Document.IsFamilyDocument)
            {
                TaskDialog.Show("BASE Office", "Open a Revit project (blank or existing) first.");
                return Result.Cancelled;
            }
            Document doc = uidoc.Document;
            if (doc.IsReadOnly)
            {
                TaskDialog.Show("BASE Office", "This document is read-only.");
                return Result.Cancelled;
            }
            string path;
            using (var dlg = new OpenFileDialog())
            {
                dlg.Title = "Choose the BASE Office export (Export to Revit 2022)";
                dlg.Filter = "BASE Office export (*.json)|*.json";
                if (dlg.ShowDialog() != DialogResult.OK) return Result.Cancelled;
                path = dlg.FileName;
            }
            ImportFile f;
            try { f = ImportFile.Parse(File.ReadAllText(path, Encoding.UTF8)); }
            catch (Exception ex)
            {
                TaskDialog.Show("BASE Office", "Could not read the file:\n" + ex.Message);
                return Result.Failed;
            }

            ModelSnapshot snap = RevitModel.Read(doc);
            PlanOptions options;
            bool createViews;
            Plan plan;
            using (var form = new PreviewForm(f, path, o => Planner.Build(f, snap, o)))
            {
                if (form.ShowDialog() != DialogResult.OK) return Result.Cancelled;
                options = form.Options;
                createViews = form.CreateViews;
                plan = Planner.Build(f, snap, options);
            }

            ApplyResult res;
            try { res = RevitModel.Apply(doc, f, plan, createViews); }
            catch (Exception ex)
            {
                TaskDialog.Show("BASE Office", "Nothing was changed.\n" + ex.Message);
                return Result.Failed;
            }

            // verification: read the model again and plan the same import - it must be all Skip
            ModelSnapshot after = RevitModel.Read(doc);
            Plan again = Planner.Build(f, after, options);
            int baseLevels = after.Levels.Count(l => l.TagProjectId == f.ProjectStableId);
            int baseGrids = after.Grids.Count(g => g.TagProjectId == f.ProjectStableId);
            var sb = new StringBuilder();
            sb.AppendLine(f.ProjectNumber + "  " + f.ProjectName);
            sb.AppendLine("Applied: " + plan.Summary());
            sb.AppendLine("Levels created " + res.LevelsCreated + ", updated " + res.LevelsUpdated + "; grids created " + res.GridsCreated +
                          ", updated " + res.GridsUpdated + "; floor plan views created " + res.ViewsCreated + ".");
            sb.AppendLine();
            sb.AppendLine("Model now: " + after.Levels.Count + " levels (" + baseLevels + " from BASE Office), " +
                          after.Grids.Count + " grids (" + baseGrids + " from BASE Office).");
            sb.AppendLine("Repeat-import check: " + (again.HasChanges ? "NOT clean - " + again.Summary() : "OK, importing again would change nothing."));
            if (res.Warnings.Count > 0) sb.AppendLine("Revit messages: " + string.Join(" | ", res.Warnings.Take(5)));
            string logPath = path + ".import-log.txt";
            try
            {
                var log = new StringBuilder(sb.ToString());
                log.AppendLine();
                log.AppendLine("Document: " + doc.Title + " (" + doc.PathName + ")");
                log.AppendLine("Time: " + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss"));
                foreach (var it in plan.Items) log.AppendLine(it.Action + "\t" + it.Category + "\t" + it.Name + "\t" + it.Detail);
                File.AppendAllText(logPath, log.ToString() + Environment.NewLine + new string('-', 60) + Environment.NewLine, Encoding.UTF8);
                sb.AppendLine("Log: " + logPath);
            }
            catch { }
            TaskDialog td = new TaskDialog("BASE Office import");
            td.MainInstruction = again.HasChanges ? "Import finished - check the repeat-import note" : "Import finished";
            td.MainContent = sb.ToString();
            td.Show();
            return Result.Succeeded;
        }
    }
}
