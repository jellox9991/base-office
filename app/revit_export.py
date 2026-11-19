"""Versioned JSON handoff for the BASE Office Revit 2022 importer.

Only whitelisted fields are written: project metadata, levels and explicitly entered straight grids.
Fees, payments, quotations, emails and internal notes are never read here.
"""
import hashlib
import json
from .util import now_iso

SCHEMA_ID = "base-office/revit-import"
SCHEMA_VERSION = "1.0"


def build_export(project, client_name, levels, grids, drawings):
    lv_out, gr_out, omitted = [], [], []
    for r in levels:
        if r["ffl_mm"] is None:
            omitted.append(dict(type="level", name=r["name"], reason="FFL blank"))
            continue
        status = "confirmed" if r["confirmed"] else "provisional"
        lv_out.append(dict(stableId=r["uid"], name=r["name"], elevationMm=int(r["ffl_mm"]),
                           datum="FFL", kind=r["kind"], status=status))
        if r["slab_offset_mm"] is not None:
            lv_out.append(dict(stableId=r["uid"] + ":ssl", name=f"{r['name']} - Slab Top",
                               elevationMm=int(r["ffl_mm"] + r["slab_offset_mm"]), datum="SSL",
                               kind=r["kind"], status=status))
    for gr in grids:
        coords = (gr["x1_mm"], gr["y1_mm"], gr["x2_mm"], gr["y2_mm"])
        if any(c is None for c in coords):
            omitted.append(dict(type="grid", name=gr["name"], reason="coordinates not entered"))
            continue
        if (coords[0], coords[1]) == (coords[2], coords[3]):
            omitted.append(dict(type="grid", name=gr["name"], reason="start and end are the same point"))
            continue
        gr_out.append(dict(stableId=gr["uid"], name=gr["name"], startMm=[int(coords[0]), int(coords[1])],
                           endMm=[int(coords[2]), int(coords[3])]))
    location = ", ".join(x for x in [
        project["area"], f"Block {project['block']}" if project["block"] else None,
        f"Street {project['street']}" if project["street"] else None,
        f"Plot {project['plot']}" if project["plot"] else None] if x)
    data = {
        "schema": SCHEMA_ID,
        "schemaVersion": SCHEMA_VERSION,
        "generator": "BASE Design Office",
        "exportedAt": now_iso(),
        "units": {
            "length": "mm",
            "elevations": "Millimetres relative to project ±0.000, placed at the Revit internal origin (Z = 0).",
            "coordinates": "Millimetres in plan from the Revit internal origin (0,0). +X = project east, +Y = project north.",
        },
        "project": {
            "stableId": project["uid"],
            "number": project["number"],
            "name": project["name"],
            "client": client_name,
            "owner": project["owner_name"] or "",
            "address": location,
            "buildingType": project["building_type"] or "",
        },
        "levels": lv_out,
        "grids": gr_out,
        "omitted": omitted,
        "sourceReferences": [
            dict(title=d["title"], discipline=d["discipline"] or "", revision=d["revision"] or "",
                 file=d["original_name"], sha256=d["sha256"])
            for d in drawings if d["direction"] == "incoming" and d["is_current"]
        ],
        "notes": "Drawings are listed as source references only. The importer creates model elements only from 'levels' and 'grids'.",
    }
    body = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False)
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()
