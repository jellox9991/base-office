"""Per-trade readiness and missing-information list.

Floor count and built-up area are never enough for measured concrete or rebar quantities, so any
figure derived from them is labelled a PRELIMINARY ALLOWANCE.
"""
from .logic import scope_label

TRADE_NEEDS = {
    "concrete": ("Structural", ["Foundation / raft plan with sections", "Column and wall schedule",
                                "Beam and slab plans for every level", "Structural sections and levels (SSL)"]),
    "rebar": ("Structural", ["Reinforcement details for footings, columns, beams and slabs",
                             "Bar schedules or typical details (cover, laps, hooks)", "Slab reinforcement plans"]),
    "blockwork": ("Architectural", ["Floor plans with wall thicknesses", "Sections and elevations (wall heights)",
                                    "Door and window schedule (opening deductions)", "Spec: internal black / external white block"]),
    "plaster": ("Architectural", ["Floor plans", "Elevations", "Sections (ceiling heights)"]),
    "waterproofing": ("Architectural", ["Roof plan", "Basement / retaining wall details", "Wet area layout"]),
    "tiling": ("Architectural", ["Floor finishes schedule", "Floor plans with room names"]),
    "skirting": ("Architectural", ["Finishes schedule (skirting type)", "Floor plans"]),
    "ceilings": ("Architectural", ["Reflected ceiling plans", "Ceiling heights"]),
    "cladding": ("Architectural", ["Elevations with cladding zones", "Facade details"]),
}

CAN_PREPARE = {
    "concrete": "Floor table and level list. Slab areas from built-up area = PRELIMINARY ALLOWANCE only.",
    "rebar": "Nothing measurable yet. Any kg/m³ ratio is a PRELIMINARY ALLOWANCE only.",
    "blockwork": "Floor table, floor heights for wall heights.",
    "plaster": "Floor table and heights.",
    "waterproofing": "Roof and basement areas from built-up area = PRELIMINARY ALLOWANCE only.",
    "tiling": "Built-up area per floor = PRELIMINARY ALLOWANCE only.",
    "skirting": "Floor list only.",
    "ceilings": "Built-up area per floor = PRELIMINARY ALLOWANCE only.",
    "cladding": "Floor heights for facade height.",
}


def trade_readiness(scope, drawings, levels):
    incoming = [d for d in drawings if d["direction"] == "incoming" and d["is_current"]]
    have_area = any(l["area_m2"] is not None for l in levels)
    out = []
    for trade in scope:
        disc, needs = TRADE_NEEDS.get(trade, ("", ["Define the drawings needed for this custom item"]))
        got = [d for d in incoming if (d["discipline"] or "").lower().startswith(disc.lower()[:4])] if disc else []
        status = "drawings_received" if got else "drawings_needed"
        prep = CAN_PREPARE.get(trade, "Custom item: confirm method.")
        if not have_area and "area" in prep:
            prep += " (enter built-up areas first)"
        out.append(dict(trade=trade, label=scope_label(trade), discipline=disc, status=status,
                        received=[f"{d['title']} rev {d['revision'] or '-'}" for d in got],
                        needs=needs, can_prepare=prep))
    return out


def missing_information(project, levels, drawings, scope):
    items = []

    def add(cat, text):
        items.append(dict(category=cat, item=text))

    if not project["due_date"]:
        add("Job", "Target submission date")
    if project["agreed_fee_fils"] is None:
        add("Commercial", "Agreed service fee (pending agreement)")
    if not scope:
        add("Scope", "BOQ scope not selected")
    if not project["excel_template"]:
        add("Scope", "Client's required Excel template (or confirm BASE standard)")
    if not levels:
        add("Floors", "Floor table not generated")
    for l in levels:
        if l["ffl_mm"] is None:
            add("Floors", f"{l['name']}: FFL")
        if l["area_m2"] is None and l["kind"] not in ("roof_structure",):
            add("Floors", f"{l['name']}: built-up area")
        if not l["confirmed"]:
            add("Floors", f"{l['name']}: elevation not confirmed from drawings (provisional)")
        if l["slab_offset_mm"] is None and any(s in scope for s in ("concrete", "rebar")):
            add("Floors", f"{l['name']}: structural slab top (SSL) offset from FFL")
    for r in trade_readiness(scope, drawings, levels):
        if r["status"] == "drawings_needed":
            for n in r["needs"]:
                add(r["label"], n)
    if project["missing_info"]:
        for line in project["missing_info"].splitlines():
            if line.strip():
                add("Noted", line.strip())
    return items
