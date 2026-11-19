// Preview window: shows what will be created, updated, skipped or flagged before anything changes in the model.
using System;
using System.Drawing;
using System.Windows.Forms;
using BaseOffice.RevitImport.Core;

namespace BaseOffice.RevitImport
{
    public class PreviewForm : Form
    {
        readonly Func<PlanOptions, Plan> planner;
        readonly DataGridView grid = new DataGridView();
        readonly Label summary = new Label();
        readonly Button apply = new Button();
        readonly CheckBox cbProvisional = new CheckBox();
        readonly CheckBox cbAdopt = new CheckBox();
        readonly CheckBox cbRecreate = new CheckBox();
        readonly CheckBox cbInfo = new CheckBox();
        readonly CheckBox cbRelink = new CheckBox();
        readonly CheckBox cbViews = new CheckBox();

        public PlanOptions Options
        {
            get
            {
                return new PlanOptions
                {
                    IncludeProvisional = cbProvisional.Checked, AdoptSameName = cbAdopt.Checked, RecreateChangedGrids = cbRecreate.Checked,
                    UpdateProjectInfo = cbInfo.Checked, RelinkProject = cbRelink.Checked
                };
            }
        }

        public bool CreateViews { get { return cbViews.Checked; } }

        public PreviewForm(ImportFile f, string path, Func<PlanOptions, Plan> planner)
        {
            this.planner = planner;
            Text = "BASE Office - Import preview (" + f.ProjectNumber + ")";
            Width = 1100; Height = 720;
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Segoe UI", 9f);

            var header = new Label
            {
                Dock = DockStyle.Top, Height = 92, Padding = new Padding(10, 8, 10, 0),
                Text = f.ProjectNumber + "   " + f.ProjectName + "\r\nClient: " + f.ClientName +
                       (string.IsNullOrEmpty(f.Address) ? "" : "    Address: " + f.Address) +
                       "\r\nUnits: millimetres. Level elevations are relative to project ±0.000 placed at the Revit internal origin (Z = 0).\r\n" +
                       "Grid coordinates are measured in plan from the Revit internal origin (0,0): +X = project east, +Y = project north.\r\n" +
                       "File: " + path + "   (export v" + f.SchemaVersion + ")"
            };

            var opts = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 58, Padding = new Padding(8, 2, 8, 2), WrapContents = true };
            Setup(cbProvisional, "Include provisional levels", false, opts);
            Setup(cbAdopt, "Link existing levels and grids with the same name", false, opts);
            Setup(cbRecreate, "Recreate grids whose direction or length changed", false, opts);
            Setup(cbInfo, "Update project information (name, number, client)", true, opts);
            Setup(cbRelink, "Allow linking this model to a different BASE project", false, opts);
            Setup(cbViews, "Create floor plan views for new levels", true, opts);

            grid.Dock = DockStyle.Fill;
            grid.ReadOnly = true;
            grid.AllowUserToAddRows = false;
            grid.AllowUserToDeleteRows = false;
            grid.RowHeadersVisible = false;
            grid.SelectionMode = DataGridViewSelectionMode.FullRowSelect;
            grid.AutoSizeRowsMode = DataGridViewAutoSizeRowsMode.AllCells;
            grid.Columns.Add("action", "Action");
            grid.Columns.Add("type", "Type");
            grid.Columns.Add("name", "Name");
            grid.Columns.Add("detail", "Details");
            grid.Columns[0].Width = 80; grid.Columns[1].Width = 90; grid.Columns[2].Width = 220;
            grid.Columns[3].AutoSizeMode = DataGridViewAutoSizeColumnMode.Fill;
            grid.Columns[3].DefaultCellStyle.WrapMode = DataGridViewTriState.True;

            var bottom = new Panel { Dock = DockStyle.Bottom, Height = 48 };
            summary.SetBounds(10, 14, 700, 24);
            summary.Font = new Font("Segoe UI", 10f, FontStyle.Bold);
            apply.Text = "Apply changes";
            apply.SetBounds(820, 8, 130, 32);
            apply.DialogResult = DialogResult.OK;
            apply.Anchor = AnchorStyles.Right | AnchorStyles.Top;
            var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, Anchor = AnchorStyles.Right | AnchorStyles.Top };
            cancel.SetBounds(960, 8, 110, 32);
            bottom.Controls.Add(summary); bottom.Controls.Add(apply); bottom.Controls.Add(cancel);
            AcceptButton = apply; CancelButton = cancel;

            Controls.Add(grid);
            Controls.Add(opts);
            Controls.Add(header);
            Controls.Add(bottom);
            Refill();
        }

        void Setup(CheckBox cb, string text, bool on, Control parent)
        {
            cb.Text = text; cb.Checked = on; cb.AutoSize = true; cb.Margin = new Padding(4, 4, 16, 2);
            cb.CheckedChanged += delegate { Refill(); };
            parent.Controls.Add(cb);
        }

        void Refill()
        {
            Plan plan = planner(Options);
            grid.Rows.Clear();
            foreach (var it in plan.Items)
            {
                int i = grid.Rows.Add(it.Action.ToString(), it.Category, it.Name, it.Detail);
                Color c = it.Action == PlanAction.Create ? Color.FromArgb(227, 245, 233)
                        : it.Action == PlanAction.Update ? Color.FromArgb(229, 238, 250)
                        : it.Action == PlanAction.Conflict ? Color.FromArgb(253, 232, 232) : Color.White;
                grid.Rows[i].DefaultCellStyle.BackColor = c;
            }
            summary.Text = plan.Summary() + (plan.Count(PlanAction.Conflict) > 0 ? "   (conflicts are not applied)" : "");
            apply.Enabled = plan.HasChanges;
            apply.Text = plan.HasChanges ? "Apply changes" : "Nothing to apply";
        }
    }
}
