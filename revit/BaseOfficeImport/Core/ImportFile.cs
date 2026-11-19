// The BASE Office -> Revit 2022 handoff file (schema "base-office/revit-import", version 1.x).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

namespace BaseOffice.RevitImport.Core
{
    public class ImportLevel
    {
        public string StableId;
        public string Name;
        public double ElevationMm;
        public string Datum;    // FFL or SSL
        public string Status;   // confirmed or provisional
        public bool IsProvisional { get { return Status != "confirmed"; } }
    }

    public class ImportGrid
    {
        public string StableId;
        public string Name;
        public double X1, Y1, X2, Y2;   // mm from the Revit internal origin
    }

    public class ImportFile
    {
        public const string SchemaId = "base-office/revit-import";
        public string SchemaVersion;
        public string ProjectStableId;
        public string ProjectNumber;
        public string ProjectName;
        public string ClientName;
        public string Address;
        public List<ImportLevel> Levels = new List<ImportLevel>();
        public List<ImportGrid> Grids = new List<ImportGrid>();
        public List<string> Warnings = new List<string>();

        public static ImportFile Parse(string json)
        {
            var root = MiniJson.Parse(json) as Dictionary<string, object>;
            if (root == null) throw new FormatException("The file is not a JSON object.");
            if (Str(root, "schema") != SchemaId)
                throw new FormatException("This is not a BASE Office Revit export (schema '" + Str(root, "schema") + "').");
            var f = new ImportFile();
            f.SchemaVersion = Str(root, "schemaVersion");
            if (f.SchemaVersion == null || !f.SchemaVersion.StartsWith("1."))
                throw new FormatException("Unsupported export version " + f.SchemaVersion + ". This importer reads version 1.x; update the add-in.");
            var units = root.ContainsKey("units") ? root["units"] as Dictionary<string, object> : null;
            if (units == null || Str(units, "length") != "mm") throw new FormatException("The export must use millimetres.");
            var p = root.ContainsKey("project") ? root["project"] as Dictionary<string, object> : null;
            if (p == null) throw new FormatException("Missing 'project'.");
            f.ProjectStableId = Req(p, "stableId");
            f.ProjectNumber = Req(p, "number");
            f.ProjectName = Str(p, "name") ?? "";
            f.ClientName = Str(p, "client") ?? "";
            f.Address = Str(p, "address") ?? "";
            foreach (var o in List(root, "levels"))
            {
                var d = (Dictionary<string, object>)o;
                f.Levels.Add(new ImportLevel
                {
                    StableId = Req(d, "stableId"), Name = Req(d, "name").Trim(), ElevationMm = Num(d, "elevationMm"),
                    Datum = Str(d, "datum") ?? "FFL", Status = Str(d, "status") ?? "provisional"
                });
            }
            foreach (var o in List(root, "grids"))
            {
                var d = (Dictionary<string, object>)o;
                var s = PointOf(d, "startMm");
                var e = PointOf(d, "endMm");
                var g = new ImportGrid { StableId = Req(d, "stableId"), Name = Req(d, "name").Trim(), X1 = s[0], Y1 = s[1], X2 = e[0], Y2 = e[1] };
                if (Math.Abs(g.X1 - g.X2) < 1 && Math.Abs(g.Y1 - g.Y2) < 1) throw new FormatException("Grid " + g.Name + " has zero length.");
                f.Grids.Add(g);
            }
            Unique(f.Levels.Select(l => l.StableId), "level stableId");
            Unique(f.Levels.Select(l => l.Name), "level name");
            Unique(f.Grids.Select(g => g.StableId), "grid stableId");
            Unique(f.Grids.Select(g => g.Name), "grid name");
            return f;
        }

        static void Unique(IEnumerable<string> values, string what)
        {
            var dup = values.GroupBy(v => v, StringComparer.OrdinalIgnoreCase).FirstOrDefault(g => g.Count() > 1);
            if (dup != null) throw new FormatException("Duplicate " + what + ": " + dup.Key);
        }

        static double[] PointOf(Dictionary<string, object> d, string key)
        {
            var l = d.ContainsKey(key) ? d[key] as List<object> : null;
            if (l == null || l.Count < 2) throw new FormatException("Grid point '" + key + "' needs [x, y] in mm.");
            return new[] { Convert.ToDouble(l[0], CultureInfo.InvariantCulture), Convert.ToDouble(l[1], CultureInfo.InvariantCulture) };
        }

        static IEnumerable<object> List(Dictionary<string, object> d, string key)
        {
            var l = d.ContainsKey(key) ? d[key] as List<object> : null;
            return l ?? new List<object>();
        }

        static string Str(Dictionary<string, object> d, string key)
        {
            return d.ContainsKey(key) && d[key] != null ? Convert.ToString(d[key], CultureInfo.InvariantCulture) : null;
        }

        static string Req(Dictionary<string, object> d, string key)
        {
            var s = Str(d, key);
            if (string.IsNullOrWhiteSpace(s)) throw new FormatException("Missing '" + key + "'.");
            return s;
        }

        static double Num(Dictionary<string, object> d, string key)
        {
            if (!d.ContainsKey(key) || d[key] == null) throw new FormatException("Missing number '" + key + "'.");
            double v = Convert.ToDouble(d[key], CultureInfo.InvariantCulture);
            if (double.IsNaN(v) || double.IsInfinity(v) || Math.Abs(v) > 1000000) throw new FormatException("Invalid " + key);
            return v;
        }
    }
}
