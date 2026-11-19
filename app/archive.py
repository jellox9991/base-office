"""Archive | الأرشيف: Jalal's instructions and project decisions, with revisions.

* Explicit wording ("لكل المشاريع", "لهالمشروع بس", "for this client") is saved approved within that scope.
* Unclear scope is saved as a DRAFT on the job it came from and is not applied until confirmed.
* An AI guess is never promoted to an approved company rule.
* Issued BOQs keep the exact rule revision used (deliveries.rules_used).
"""
import hashlib
import json
from . import logic
from .db import new_uid
from .util import now_iso, today_kw

EXPORT_SCHEMA = "base-office/archive"
EXPORT_VERSION = "1.0"


def save_rule(db, wording, *, project=None, client_id=None, scope="auto", topic=None, kind="instruction",
              trade=None, source_ref=None, request_key=None, author=None, status=None, uid=None, effective_date=None):
    """Returns (rule_id, scope, status, why, created_bool)."""
    if request_key:
        dup = db.execute("SELECT id, scope, status FROM rules WHERE request_key = ?", (request_key,)).fetchone()
        if dup:
            return dup["id"], dup["scope"], dup["status"], "already saved (form sent twice)", False
    why = "chosen by you"
    auto_status = "approved"
    if scope == "auto":
        scope, why = logic.detect_rule_scope(wording, bool(project), bool(client_id))
        if why == "UNCLEAR":
            auto_status = "draft"
            why = "the wording does not say whether it is for all projects, this client or this job, so it is a draft"
    if scope == "project" and not project:
        scope = "company"
    if scope == "client" and not client_id:
        scope = "company"
    status = status or auto_status
    now = now_iso()
    rid = db.execute("""INSERT INTO rules(uid, kind, topic, status, source_ref, effective_date, request_key, scope, client_id,
                        project_id, trade, title, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (uid or new_uid(), kind, topic, status, source_ref, effective_date or today_kw().isoformat(),
                      request_key, scope, client_id if scope == "client" else None,
                      project["id"] if scope == "project" and project else None, trade,
                      wording.strip().splitlines()[0][:80], now, now)).lastrowid
    db.execute("INSERT INTO rule_revisions(rule_id, revision, text, original_wording, reason, author, created_at) VALUES (?,?,?,?,?,?,?)",
               (rid, 1, wording.strip(), wording.strip(), "first capture", author, now))
    logic.log(db, "rule_saved", f"Archive #{rid} ({scope}, {status}; {why}): {wording.strip()[:120]}",
              project_id=project["id"] if project else None, client_id=client_id, actor=author)
    return rid, scope, status, why, True


def revise_rule(db, rid, text, reason, author=None, expected_revision=None):
    r = db.execute("SELECT * FROM rules WHERE id = ?", (rid,)).fetchone()
    if expected_revision is not None and r["current_revision"] != expected_revision:
        return None  # someone revised it meanwhile
    rev = r["current_revision"] + 1
    db.execute("INSERT INTO rule_revisions(rule_id, revision, text, original_wording, reason, author, created_at) VALUES (?,?,?,?,?,?,?)",
               (rid, rev, text, text, reason, author, now_iso()))
    db.execute("UPDATE rules SET current_revision = ?, updated_at = ? WHERE id = ?", (rev, now_iso(), rid))
    logic.log(db, "rule_revised", f"Archive #{rid} rev {rev} ({reason or 'no reason given'}): {text[:120]}",
              project_id=r["project_id"], client_id=r["client_id"], actor=author)
    return rev


def search(db, q="", topic="", scope="", status="", client_id=None, project_id=None, include_retired=False):
    rows = db.execute("""SELECT r.*, rv.text, rv.original_wording, c.name AS client_name, p.number AS project_number, p.name AS project_name
                         FROM rules r JOIN rule_revisions rv ON rv.rule_id = r.id AND rv.revision = r.current_revision
                         LEFT JOIN clients c ON c.id = r.client_id LEFT JOIN projects p ON p.id = r.project_id
                         ORDER BY r.active DESC, CASE r.scope WHEN 'company' THEN 0 WHEN 'client' THEN 1 ELSE 2 END, r.id DESC""").fetchall()
    qn = logic.normalise_search(q)
    out = []
    for r in rows:
        if not include_retired and not r["active"]:
            continue
        if topic and r["topic"] != topic:
            continue
        if scope and r["scope"] != scope:
            continue
        if status and r["status"] != status:
            continue
        if client_id and r["client_id"] != client_id:
            continue
        if project_id and r["project_id"] != project_id:
            continue
        if qn:
            # search every revision, Arabic and English
            texts = " ".join(x["text"] + " " + (x["original_wording"] or "") for x in
                             db.execute("SELECT text, original_wording FROM rule_revisions WHERE rule_id = ?", (r["id"],)))
            if qn not in logic.normalise_search(texts + " " + (r["title"] or "") + " " + (r["source_ref"] or "")):
                continue
        out.append(r)
    return out


def export_json(db):
    rules = []
    for r in db.execute("""SELECT r.*, c.name AS client_name, p.uid AS project_uid, p.number AS project_number
                           FROM rules r LEFT JOIN clients c ON c.id = r.client_id LEFT JOIN projects p ON p.id = r.project_id
                           ORDER BY r.id"""):
        revs = [dict(revision=x["revision"], text=x["text"], originalWording=x["original_wording"], reason=x["reason"],
                     author=x["author"], createdAt=x["created_at"])
                for x in db.execute("SELECT * FROM rule_revisions WHERE rule_id = ? ORDER BY revision", (r["id"],))]
        rules.append(dict(uid=r["uid"], kind=r["kind"], topic=r["topic"], status=r["status"], active=bool(r["active"]),
                          scope=r["scope"], client=r["client_name"], projectUid=r["project_uid"],
                          projectNumber=r["project_number"], trade=r["trade"], sourceRef=r["source_ref"],
                          effectiveDate=r["effective_date"], currentRevision=r["current_revision"], revisions=revs))
    return json.dumps(dict(schema=EXPORT_SCHEMA, schemaVersion=EXPORT_VERSION, exportedAt=now_iso(), rules=rules),
                      ensure_ascii=False, indent=2)


def plan_import(db, payload):
    """Preview: which entries are new, changed (more revisions), unchanged or conflicting."""
    data = json.loads(payload)
    if data.get("schema") != EXPORT_SCHEMA or not str(data.get("schemaVersion", "")).startswith("1."):
        raise ValueError("Not a BASE Office Archive export (schema 1.x)")
    plan = []
    for e in data.get("rules", []):
        cur = db.execute("SELECT * FROM rules WHERE uid = ?", (e["uid"],)).fetchone()
        if not cur:
            plan.append(dict(action="new", entry=e, reason="not in this Archive"))
            continue
        local = {x["revision"]: x["text"] for x in db.execute("SELECT revision, text FROM rule_revisions WHERE rule_id = ?", (cur["id"],))}
        incoming = {x["revision"]: x["text"] for x in e["revisions"]}
        clash = [n for n in incoming if n in local and local[n] != incoming[n]]
        if clash:
            plan.append(dict(action="conflict", entry=e, reason=f"revision {clash[0]} text differs"))
        elif max(incoming) > max(local):
            plan.append(dict(action="update", entry=e, reason=f"adds revisions {max(local) + 1}..{max(incoming)}"))
        else:
            plan.append(dict(action="unchanged", entry=e, reason="same revisions"))
    return plan


def apply_import(db, payload, author=None):
    counts = dict(new=0, update=0, unchanged=0, conflict=0, skipped=0)
    for item in plan_import(db, payload):
        e = item["entry"]
        a = item["action"]
        if a == "new":
            project = db.execute("SELECT * FROM projects WHERE uid = ?", (e.get("projectUid"),)).fetchone() if e.get("projectUid") else None
            client = db.execute("SELECT id FROM clients WHERE name = ?", (e.get("client"),)).fetchone() if e.get("client") else None
            if (e["scope"] == "project" and not project) or (e["scope"] == "client" and not client):
                counts["skipped"] += 1  # its job or client is not in this database
                continue
            first = e["revisions"][0]
            rid, *_ = save_rule(db, first["text"], project=project, client_id=client["id"] if client else None,
                                scope=e["scope"], topic=e.get("topic"), kind=e.get("kind", "instruction"),
                                trade=e.get("trade"), source_ref=e.get("sourceRef"), status=e.get("status"),
                                uid=e["uid"], author=author, effective_date=e.get("effectiveDate"))
            for rv in e["revisions"][1:]:
                revise_rule(db, rid, rv["text"], rv.get("reason"), rv.get("author"))
        elif a == "update":
            cur = db.execute("SELECT * FROM rules WHERE uid = ?", (e["uid"],)).fetchone()
            for rv in e["revisions"]:
                if rv["revision"] > cur["current_revision"]:
                    revise_rule(db, cur["id"], rv["text"], rv.get("reason"), rv.get("author"))
        counts[a] = counts.get(a, 0) + 1
    return counts


def markdown_summary(db, settings):
    lines = [f"# {settings.get('company_name', 'BASE Design')} - active Archive rules", "",
             f"Generated {now_iso()}. Approved rules only; drafts and examples are listed at the end.", ""]
    rows = search(db)
    for status_key, heading in (("approved", None), ("draft", "## Drafts (scope unclear, not applied)"),
                                ("example", "## Examples (wording not confirmed, not applied)")):
        group = [r for r in rows if r["status"] == status_key]
        if not group:
            continue
        if heading:
            lines += ["", heading, ""]
        for scope in ("company", "client", "project"):
            sub = [r for r in group if r["scope"] == scope]
            if not sub:
                continue
            if status_key == "approved":
                lines += ["", f"## {logic.RULE_SCOPES[scope][0]}", ""]
            for r in sub:
                where = r["client_name"] if scope == "client" else (f"{r['project_number']} {r['project_name']}" if scope == "project" else "all projects")
                lines.append(f"- **#{r['id']} rev {r['current_revision']}** [{r['topic'] or 'general'}] ({where}): {r['text']}")
    return "\n".join(lines) + "\n"
