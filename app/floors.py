"""Floor table generation and elevation recalculation.

Convention (per BASE Design / Revit 2022 handoff):
  * FFL, structural slab top offset, floor-to-floor height and clear height are separate fields.
  * ftf_mm on a row = height from this level's FFL up to the next level's FFL.
  * Fixed rows keep their FFL; non-fixed rows are recalculated from the nearest fixed row
    below them (or, for rows under the lowest fixed row, from the nearest fixed row above).
  * A blank height makes the levels that depend on it blank: blank is never treated as zero.
  * Every physical floor is its own row. A "typical" floor is expanded into named rows once,
    so its area is never multiplied a second time.
"""
from .db import new_uid

ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth", "Tenth"]
ORDINALS_AR = ["الأول", "الثاني", "الثالث", "الرابع", "الخامس", "السادس", "السابع", "الثامن", "التاسع", "العاشر"]


def upper_name(i):
    return f"{ORDINALS[i - 1]} Floor" if i <= len(ORDINALS) else f"Floor {i}"


def count_explanation(basements, uppers, has_roof, roof_structures):
    basements = basements or 0
    uppers = uppers or 0
    floors = basements + 1 + uppers
    parts = [f"{basements} basement" + ("s" if basements != 1 else ""), "ground", f"{uppers} upper"]
    text = " + ".join(parts) + f" = {floors} floors"
    extra = []
    if has_roof:
        extra.append("roof")
    if roof_structures:
        extra.append("roof structure")
    if extra:
        text += " (+ " + " + ".join(extra) + f" = {floors + len(extra)} levels)"
    return text


def generate(basements, uppers, has_roof, roof_structures, gf_ffl_mm, typical_ftf_mm):
    """Return a list of row dicts, bottom to top."""
    basements = basements or 0
    uppers = uppers or 0
    rows = []

    def row(name, kind, group=None, fixed=0):
        rows.append(dict(uid=new_uid(), name=name, kind=kind, ffl_mm=None, ftf_mm=typical_ftf_mm,
                         area_m2=None, typical_group=group, notes=None, fixed=fixed, confirmed=0,
                         slab_offset_mm=None, clear_height_mm=None, slab_thk_mm=None, finish_mm=None,
                         drawing_ref=None))
        return rows[-1]

    for b in range(basements, 0, -1):
        row("Basement" if basements == 1 else f"Basement {b}", "basement")
    gf = row("Ground Floor", "ground", fixed=1)
    gf["ffl_mm"] = gf_ffl_mm
    if gf_ffl_mm is None:
        gf["fixed"] = 0
    group = "TYP" if uppers >= 2 else None
    for i in range(1, uppers + 1):
        row(upper_name(i), "upper", group=group)
    if has_roof:
        r = row("Roof", "roof")
        if not roof_structures:
            r["ftf_mm"] = None
    if roof_structures:
        r = row("Upper Roof", "roof_structure")
        r["ftf_mm"] = None
        r["notes"] = roof_structures
    if rows:
        rows[-1]["ftf_mm"] = None  # nothing above the top level
    for i, r in enumerate(rows):
        r["seq"] = i
    return recalculate(rows)[0]


def recalculate(rows):
    """Recalculate FFLs. Returns (new_rows, warnings). Input rows are bottom-to-top dicts."""
    rows = [dict(r) for r in rows]
    n = len(rows)
    anchors = [i for i, r in enumerate(rows) if r.get("fixed") and r.get("ffl_mm") is not None]
    warnings = []
    if not anchors:
        return rows, ["No fixed level: tick 'Fixed' on one level (normally Ground Floor) and enter its FFL."]

    def chain_up(a, i):
        total = rows[a]["ffl_mm"]
        for k in range(a, i):
            h = rows[k].get("ftf_mm")
            if h is None:
                return None
            total += h
        return total

    def chain_down(a, i):
        total = rows[a]["ffl_mm"]
        for k in range(i, a):
            h = rows[k].get("ftf_mm")
            if h is None:
                return None
            total -= h
        return total

    for i in range(n):
        below = [a for a in anchors if a < i]
        if rows[i].get("fixed") and rows[i].get("ffl_mm") is not None:
            if below:
                expected = chain_up(below[-1], i)
                if expected is not None and expected != rows[i]["ffl_mm"]:
                    warnings.append(
                        f"{rows[i]['name']} is fixed at {rows[i]['ffl_mm'] / 1000:+.3f} m; the heights below would "
                        f"give {expected / 1000:+.3f} m. The fixed value is kept.")
            continue
        if below:
            rows[i]["ffl_mm"] = chain_up(below[-1], i)
        else:
            above = [a for a in anchors if a > i]
            rows[i]["ffl_mm"] = chain_down(above[0], i)
    return rows, warnings


def preview_changes(old_rows, new_rows):
    """List the levels whose FFL changes, for the 'which higher levels will move' preview."""
    old = {r["uid"]: r for r in old_rows}
    moves = []
    for r in new_rows:
        o = old.get(r["uid"])
        before = o["ffl_mm"] if o else None
        if before != r["ffl_mm"]:
            moves.append(dict(name=r["name"], before=before, after=r["ffl_mm"], fixed=bool(r.get("fixed"))))
    return moves


def duplicate(rows, index):
    """Duplicate one row (e.g. a typical floor) directly above itself as a new named floor."""
    src = rows[index]
    copy = dict(src)
    copy["uid"] = new_uid()
    copy["fixed"] = 0
    copy["confirmed"] = 0
    copy["ffl_mm"] = None
    base = src["name"]
    existing = {r["name"] for r in rows}
    k = 2
    name = f"{base} ({k})"
    while name in existing:
        k += 1
        name = f"{base} ({k})"
    copy["name"] = name
    out = rows[:index + 1] + [copy] + rows[index + 1:]
    for i, r in enumerate(out):
        r["seq"] = i
    return recalculate(out)[0]


def total_area(rows):
    vals = [r["area_m2"] for r in rows if r.get("area_m2") is not None]
    return round(sum(vals), 2) if vals else None
