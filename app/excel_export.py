"""XLSX intake sheet, floor table (with live formulas), floor-by-floor BOQ worksheet and missing-info list."""
import io
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from .logic import scope_label, WORK_LABELS
from .readiness import missing_information, trade_readiness
from .util import fmt_date

INK = "0A0A0A"
TEAL = "F90130"
HEAD = PatternFill("solid", fgColor=INK)
SUB = PatternFill("solid", fgColor="F2F2F2")
ALLOW = PatternFill("solid", fgColor="FFF4CC")
THIN = Side(style="thin", color="C9D2D9")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

TRADE_ITEMS = {
    "concrete": [("Slab concrete", "m³"), ("Beams concrete", "m³"), ("Columns / walls concrete", "m³")],
    "rebar": [("Reinforcement - all elements", "kg")],
    "blockwork": [("External blockwork (white)", "m²"), ("Internal blockwork (black)", "m²")],
    "plaster": [("Internal plaster", "m²"), ("External plaster", "m²")],
    "waterproofing": [("Waterproofing", "m²")],
    "tiling": [("Floor tiling", "m²"), ("Wall tiling", "m²")],
    "skirting": [("Skirting", "m")],
    "ceilings": [("Suspended ceilings", "m²")],
    "cladding": [("Cladding", "m²")],
}


def _header(ws, row, headers, widths=None):
    for i, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=i, value=h)
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = HEAD
        c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        c.border = BOX
    if widths:
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w


def _title(ws, text, sub):
    ws["A1"] = text
    ws["A1"].font = Font(bold=True, size=14, color=INK)
    ws["A2"] = sub
    ws["A2"].font = Font(italic=True, color=TEAL)


def build_workbook(settings, project, client, levels, grids, drawings, scope, rules):
    wb = Workbook()
    company = settings.get("company_name", "BASE Design")
    tag = f"{project['number']} | {project['name']}"

    # ---------------- Intake
    ws = wb.active
    ws.title = "Intake"
    _title(ws, f"{company} - Project intake sheet", tag)
    rows = [
        ("Project number", project["number"]), ("Project name", project["name"]),
        ("Client / company", client["name"]), ("Contact person", client["contact_person"]),
        ("Phone / WhatsApp", client["whatsapp"] or client["phone"]), ("Client email", client["email"]),
        ("Project owner", project["owner_name"]),
        ("Area", project["area"]), ("Block", project["block"]), ("Street", project["street"]), ("Plot", project["plot"]),
        ("Map link", project["map_link"]),
        ("Building type", project["building_type"]), ("Plot area (m²)", project["plot_area_m2"]),
        ("Basements", project["basement_count"]), ("Upper floors (above ground)", project["upper_floor_count"]),
        ("Roof", "Yes" if project["has_roof"] else ("No" if project["has_roof"] is not None else None)),
        ("Roof structures", project["roof_structures"]),
        ("Typical floor-to-floor (m)", project["typical_ftf_mm"] / 1000 if project["typical_ftf_mm"] is not None else None),
        ("BOQ scope", ", ".join(scope_label(s) for s in scope)), ("Exclusions", project["exclusions"]),
        ("Client Excel template", project["excel_template"]),
        ("Date received", fmt_date(project["date_received"])), ("Target submission", fmt_date(project["due_date"])),
        ("Responsible", project["responsible"]), ("Source drawings", project["source_drawings"]),
        ("Work status", WORK_LABELS[project["work_status"]][0]),
    ]
    _header(ws, 4, ["Field", "Value"], [30, 60])
    for i, (k, v) in enumerate(rows, 5):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v).alignment = Alignment(wrap_text=True, vertical="top")
        for col in (1, 2):
            ws.cell(row=i, column=col).border = BOX
    ws.freeze_panes = "A5"
    ws.cell(row=len(rows) + 6, column=1,
            value="Commercial data (fees, payments) and internal notes are deliberately not included in this workbook.").font = Font(italic=True, color="888888")

    # ---------------- Floors
    wf = wb.create_sheet("Floors")
    _title(wf, "Floor table", f"{tag} | Units: metres unless noted. Blank = not entered (not zero). FFL formulas follow the floor-to-floor heights; fixed levels are typed values.")
    hdr = ["#", "Level", "Type", "Typical group", "FFL (m)", "Fixed", "Status", "Floor-to-floor (m)",
           "Slab top offset (m)", "SSL (m)", "Clear height (m)", "Slab thk (mm)", "Finish build-up (mm)",
           "Built-up area (m²)", "Drawing ref", "Notes"]
    _header(wf, 4, hdr, [5, 22, 14, 12, 11, 7, 12, 14, 14, 11, 12, 11, 13, 14, 16, 30])
    first = 5
    fixed_idx = [i for i, l in enumerate(levels) if l["fixed"] and l["ffl_mm"] is not None]
    for i, l in enumerate(levels):
        r = first + i
        m = lambda v: None if v is None else v / 1000
        vals = [i + 1, l["name"], l["kind"], l["typical_group"], None, "Yes" if l["fixed"] else "",
                "Confirmed" if l["confirmed"] else "Provisional", m(l["ftf_mm"]), m(l["slab_offset_mm"]), None,
                m(l["clear_height_mm"]), l["slab_thk_mm"], l["finish_mm"], l["area_m2"], l["drawing_ref"], l["notes"]]
        for c, v in enumerate(vals, 1):
            cell = wf.cell(row=r, column=c, value=v)
            cell.border = BOX
        # FFL: fixed -> value, otherwise a formula mirroring the app's recalculation
        if l["fixed"] and l["ffl_mm"] is not None:
            wf.cell(row=r, column=5, value=l["ffl_mm"] / 1000).font = Font(bold=True)
        elif any(a < i for a in fixed_idx):
            wf.cell(row=r, column=5, value=f'=IF(OR(E{r - 1}="",H{r - 1}=""),"",E{r - 1}+H{r - 1})')
        elif any(a > i for a in fixed_idx):
            wf.cell(row=r, column=5, value=f'=IF(OR(E{r + 1}="",H{r}=""),"",E{r + 1}-H{r})')
        else:
            wf.cell(row=r, column=5, value=m(l["ffl_mm"]))
        wf.cell(row=r, column=10, value=f'=IF(OR(E{r}="",I{r}=""),"",E{r}+I{r})')
        for c in (5, 8, 9, 10, 11):
            wf.cell(row=r, column=c).number_format = '+0.000;-0.000;0.000'
        wf.cell(row=r, column=14).number_format = "#,##0.00"
        if not l["confirmed"]:
            wf.cell(row=r, column=7).fill = ALLOW
    last = first + len(levels) - 1
    tr = last + 1
    wf.cell(row=tr, column=2, value="Total built-up area").font = Font(bold=True)
    wf.cell(row=tr, column=14, value=f"=SUM(N{first}:N{last})" if levels else 0).font = Font(bold=True)
    wf.cell(row=tr, column=14).number_format = "#,##0.00"
    wf.freeze_panes = "C5"
    if levels:
        wf.auto_filter.ref = f"A4:P{last}"

    # ---------------- BOQ worksheet (floor by floor)
    wq = wb.create_sheet("BOQ Worksheet")
    _title(wq, "Floor-by-floor BOQ worksheet",
           f"{tag} | Quantities stay blank until measured from drawings. Rows marked PRELIMINARY ALLOWANCE are not measured quantities.")
    _header(wq, 4, ["Trade", "Level", "Item", "Unit", "Quantity", "Basis", "Source drawing / rev", "Allowance area (m²)", "Notes"],
            [16, 22, 32, 7, 12, 30, 24, 16, 30])
    r = 5
    readiness = {t["trade"]: t for t in trade_readiness(scope, drawings, levels)}
    for trade in scope:
        items = TRADE_ITEMS.get(trade, [(trade, "")])
        rd = readiness.get(trade)
        for l in levels:
            for item, unit in items:
                basis = "Measured - drawings received" if rd and rd["status"] == "drawings_received" else "Pending drawings"
                vals = [scope_label(trade), l["name"], item, unit, None, basis, "", None, ""]
                for c, v in enumerate(vals, 1):
                    wq.cell(row=r, column=c, value=v).border = BOX
                wq.cell(row=r, column=5).number_format = "#,##0.000"
                r += 1
    # Explicit allowance rows from built-up area (clearly labelled)
    for l in levels:
        if l["area_m2"] is not None:
            vals = ["ALLOWANCE", l["name"], "Built-up area (reference for allowances)", "m²", None,
                    "PRELIMINARY ALLOWANCE - not a measured quantity", "Intake data", l["area_m2"], ""]
            for c, v in enumerate(vals, 1):
                cell = wq.cell(row=r, column=c, value=v)
                cell.border = BOX
                cell.fill = ALLOW
            r += 1
    wq.freeze_panes = "C5"
    if r > 5:
        wq.auto_filter.ref = f"A4:I{r - 1}"
    # summary by trade, formulas
    r += 1
    wq.cell(row=r, column=1, value="Summary by trade").font = Font(bold=True, color=INK)
    r += 1
    for trade in scope:
        for item, unit in TRADE_ITEMS.get(trade, [(trade, "")]):
            wq.cell(row=r, column=1, value=scope_label(trade))
            wq.cell(row=r, column=3, value=item)
            wq.cell(row=r, column=4, value=unit)
            wq.cell(row=r, column=5, value=f'=SUMIFS(E$5:E${r - 2},A$5:A${r - 2},A{r},C$5:C${r - 2},C{r})').number_format = "#,##0.000"
            r += 1

    # ---------------- Trade readiness + missing info
    wm = wb.create_sheet("Missing Info")
    _title(wm, "Readiness and missing information", tag)
    _header(wm, 4, ["Trade", "Drawings", "Can prepare now", "Still needed"], [18, 34, 50, 60])
    r = 5
    for t in readiness.values():
        vals = [t["label"], "; ".join(t["received"]) or "None received", t["can_prepare"],
                "" if t["status"] == "drawings_received" else "\n".join(t["needs"])]
        for c, v in enumerate(vals, 1):
            cell = wm.cell(row=r, column=c, value=v)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = BOX
        r += 1
    r += 1
    _header(wm, r, ["Category", "Missing item"])
    r += 1
    for it in missing_information(project, levels, drawings, scope):
        wm.cell(row=r, column=1, value=it["category"]).border = BOX
        wm.cell(row=r, column=2, value=it["item"]).border = BOX
        r += 1
    wm.freeze_panes = "A5"

    # ---------------- Grids
    wg = wb.create_sheet("Grids")
    _title(wg, "Grids", f"{tag} | mm from the Revit internal origin, +X east, +Y north")
    _header(wg, 4, ["Grid", "Start X (mm)", "Start Y (mm)", "End X (mm)", "End Y (mm)", "Length (mm)", "Notes"],
            [10, 14, 14, 14, 14, 14, 30])
    for i, gr in enumerate(grids, 5):
        vals = [gr["name"], gr["x1_mm"], gr["y1_mm"], gr["x2_mm"], gr["y2_mm"], None, gr["notes"]]
        for c, v in enumerate(vals, 1):
            wg.cell(row=i, column=c, value=v).border = BOX
        wg.cell(row=i, column=6, value=f'=IF(COUNT(B{i}:E{i})<4,"",ROUND(SQRT((D{i}-B{i})^2+(E{i}-C{i})^2),0))')

    # ---------------- Archive rules applied
    wr = wb.create_sheet("Rules")
    _title(wr, "Archive rules that apply to this project", tag)
    _header(wr, 4, ["Rule", "Revision", "Scope", "Why it applies", "Rule text"], [8, 9, 12, 40, 70])
    for i, ru in enumerate(rules, 5):
        vals = [ru["id"], ru["current_revision"], ru["scope"], ru["why"][0], ru["text"]]
        for c, v in enumerate(vals, 1):
            cell = wr.cell(row=i, column=c, value=v)
            cell.border = BOX
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
