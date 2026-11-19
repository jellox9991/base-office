// Signatures-only stand-in for the Revit 2022 API members the add-in uses. Compile check only: never shipped.
using System;
using System.Collections;
using System.Collections.Generic;
namespace Autodesk.Revit.Attributes { public enum TransactionMode { Manual, ReadOnly } public class TransactionAttribute : Attribute { public TransactionAttribute(TransactionMode m) { } } }
namespace Autodesk.Revit.Exceptions { public class ArgumentException : System.ArgumentException { } }
namespace Autodesk.Revit.DB
{
    public class ForgeTypeId { }
    public static class UnitTypeId { public static ForgeTypeId Millimeters { get { return null; } } }
    public static class UnitUtils { public static double ConvertToInternalUnits(double v, ForgeTypeId u) { return v; } public static double ConvertFromInternalUnits(double v, ForgeTypeId u) { return v; } }
    public class ElementId { public ElementId(int i) { } public int IntegerValue { get { return 0; } } }
    public class XYZ { public XYZ(double x, double y, double z) { } public double X { get { return 0; } } public double Y { get { return 0; } } public double Z { get { return 0; } } }
    public class Curve { public XYZ GetEndPoint(int i) { return null; } }
    public class Line : Curve { public static Line CreateBound(XYZ a, XYZ b) { return null; } }
    public class Element { public ElementId Id { get { return null; } } public virtual string Name { get; set; } public bool Pinned { get; set; }
        public ExtensibleStorage.Entity GetEntity(ExtensibleStorage.Schema s) { return null; } public void SetEntity(ExtensibleStorage.Entity e) { } }
    public class Level : Element { public static Level Create(Document d, double elevation) { return null; } public double Elevation { get; set; } public double ProjectElevation { get { return 0; } } }
    public class Grid : Element { public Curve Curve { get { return null; } } public bool IsCurved { get { return false; } } public static Grid Create(Document d, Line l) { return null; } }
    public class ProjectInfo : Element { public string Number { get; set; } public string ClientName { get; set; } public string Address { get; set; } }
    public enum ViewFamily { FloorPlan, CeilingPlan }
    public class ViewFamilyType : Element { public ViewFamily ViewFamily { get { return ViewFamily.FloorPlan; } } }
    public class ViewPlan : Element { public static ViewPlan Create(Document d, ElementId type, ElementId level) { return null; } }
    public class DataStorage : Element { public static DataStorage Create(Document d) { return null; } }
    public class Document { public bool IsFamilyDocument { get { return false; } } public bool IsReadOnly { get { return false; } } public string Title { get { return ""; } } public string PathName { get { return ""; } }
        public ProjectInfo ProjectInformation { get { return null; } } public Element GetElement(ElementId id) { return null; } public ICollection<ElementId> Delete(ElementId id) { return null; } }
    public class FilteredElementCollector : IEnumerable<Element> { public FilteredElementCollector(Document d) { } public FilteredElementCollector OfClass(Type t) { return this; }
        public IEnumerator<Element> GetEnumerator() { yield break; } IEnumerator IEnumerable.GetEnumerator() { return GetEnumerator(); } }
    public static class ElementTransformUtils { public static void MoveElement(Document d, ElementId id, XYZ v) { } }
    public enum TransactionStatus { Uninitialized, Started, RolledBack, Committed, Pending, Error, Proceed }
    public enum FailureProcessingResult { Continue, ProceedWithCommit, ProceedWithRollBack, WaitForUserInput }
    public enum FailureSeverity { None, Warning, Error, DocumentCorruption }
    public class FailureMessageAccessor { public FailureSeverity GetSeverity() { return FailureSeverity.Warning; } public string GetDescriptionText() { return ""; } }
    public class FailuresAccessor { public IList<FailureMessageAccessor> GetFailureMessages() { return null; } public void DeleteWarning(FailureMessageAccessor m) { } }
    public interface IFailuresPreprocessor { FailureProcessingResult PreprocessFailures(FailuresAccessor a); }
    public class FailureHandlingOptions { public FailureHandlingOptions SetFailuresPreprocessor(IFailuresPreprocessor p) { return this; } }
    public class Transaction : IDisposable { public Transaction(Document d, string name) { } public TransactionStatus Start() { return 0; } public TransactionStatus Commit() { return 0; }
        public FailureHandlingOptions GetFailureHandlingOptions() { return null; } public void SetFailureHandlingOptions(FailureHandlingOptions o) { } public void Dispose() { } }
}
namespace Autodesk.Revit.DB.ExtensibleStorage
{
    public enum AccessLevel { Public, Vendor, Application }
    public class FieldBuilder { }
    public class Schema { public static Schema Lookup(Guid g) { return null; } }
    public class SchemaBuilder { public SchemaBuilder(Guid g) { } public SchemaBuilder SetSchemaName(string n) { return this; } public SchemaBuilder SetDocumentation(string d) { return this; }
        public SchemaBuilder SetReadAccessLevel(AccessLevel a) { return this; } public SchemaBuilder SetWriteAccessLevel(AccessLevel a) { return this; }
        public FieldBuilder AddSimpleField(string name, Type t) { return null; } public Schema Finish() { return null; } }
    public class Entity { public Entity(Schema s) { } public bool IsValid() { return false; } public T Get<T>(string field) { return default(T); } public void Set<T>(string field, T value) { } }
}
namespace Autodesk.Revit.UI
{
    using Autodesk.Revit.DB;
    public enum Result { Cancelled = 1, Succeeded = 0, Failed = -1 }
    public class ElementSet { }
    public class UIDocument { public Document Document { get { return null; } } }
    public class UIApplication { public UIDocument ActiveUIDocument { get { return null; } } }
    public class ExternalCommandData { public UIApplication Application { get { return null; } } }
    public interface IExternalCommand { Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements); }
    public class UIControlledApplication { public void CreateRibbonTab(string n) { } public RibbonPanel CreateRibbonPanel(string tab, string name) { return null; } }
    public interface IExternalApplication { Result OnStartup(UIControlledApplication a); Result OnShutdown(UIControlledApplication a); }
    public class RibbonItemData { public string ToolTip { get; set; } public string LongDescription { get; set; } }
    public class PushButtonData : RibbonItemData { public PushButtonData(string name, string text, string assembly, string className) { } }
    public class RibbonItem { }
    public class RibbonPanel { public RibbonItem AddItem(RibbonItemData d) { return null; } }
    public class TaskDialog { public TaskDialog(string title) { } public string MainInstruction { get; set; } public string MainContent { get; set; } public int Show() { return 0; }
        public static int Show(string title, string text) { return 0; } }
}
