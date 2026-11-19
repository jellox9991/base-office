// Adds the "BASE Design" ribbon tab with the Import Project Data button.
using System;
using System.Reflection;
using Autodesk.Revit.UI;

namespace BaseOffice.RevitImport
{
    public class App : IExternalApplication
    {
        public Result OnStartup(UIControlledApplication app)
        {
            const string tab = "BASE Design";
            try { app.CreateRibbonTab(tab); } catch (Autodesk.Revit.Exceptions.ArgumentException) { /* tab already exists */ }
            RibbonPanel panel = app.CreateRibbonPanel(tab, "BASE Office");
            var data = new PushButtonData("BaseOfficeImport", "Import\nProject Data", Assembly.GetExecutingAssembly().Location,
                                          "BaseOffice.RevitImport.ImportCommand");
            data.ToolTip = "Import project number, name, client, levels and grids from a BASE Office export (.json). Shows a preview first.";
            data.LongDescription = "Matches levels and grids by their BASE stable ID, so importing the same project again updates instead of duplicating.";
            panel.AddItem(data);
            return Result.Succeeded;
        }

        public Result OnShutdown(UIControlledApplication app) { return Result.Succeeded; }
    }
}
