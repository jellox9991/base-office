"""SQLite storage for BASE Design Office.

All money is stored as integer fils (1 KWD = 1000 fils).
All lengths are stored as integer millimetres. NULL always means "not entered" (blank), never zero.
"""
import json
import sqlite3
import uuid
from flask import g, current_app

SCHEMA = r"""
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    contact_person TEXT,
    phone TEXT,
    whatsapp TEXT,
    email TEXT,
    language TEXT NOT NULL DEFAULT 'ar',
    notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,
    number TEXT NOT NULL UNIQUE,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    name TEXT NOT NULL,
    owner_name TEXT,
    area TEXT, block TEXT, street TEXT, plot TEXT, map_link TEXT,
    building_type TEXT,
    plot_area_m2 REAL,
    basement_count INTEGER,
    upper_floor_count INTEGER,
    has_roof INTEGER,
    roof_structures TEXT,
    typical_ftf_mm INTEGER,
    gf_ffl_mm INTEGER,
    building_notes TEXT,
    scope TEXT NOT NULL DEFAULT '[]',
    custom_scope TEXT,
    exclusions TEXT,
    excel_template TEXT,
    date_received TEXT,
    due_date TEXT,
    responsible TEXT,
    source_drawings TEXT,
    missing_info TEXT,
    agreed_fee_fils INTEGER,
    fee_basis TEXT,               -- 'quotation' | 'direct' | NULL (pending agreement)
    fee_basis_note TEXT,
    accepted_quotation_id INTEGER,
    internal_notes TEXT,
    work_status TEXT NOT NULL DEFAULT 'not_started',
    archived INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (agreed_fee_fils IS NULL OR agreed_fee_fils >= 0),
    CHECK (work_status IN ('not_started','in_progress','waiting','ready','completed'))
);

CREATE TABLE IF NOT EXISTS levels (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    uid TEXT NOT NULL UNIQUE,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,           -- basement | ground | upper | roof | roof_structure | other
    ffl_mm INTEGER,
    ftf_mm INTEGER,               -- floor-to-floor height up to the next level
    area_m2 REAL,
    typical_group TEXT,
    notes TEXT,
    fixed INTEGER NOT NULL DEFAULT 0,      -- manually fixed FFL, never moved by recalculation
    confirmed INTEGER NOT NULL DEFAULT 0,  -- 0 = provisional
    slab_offset_mm INTEGER,       -- structural slab top relative to FFL (usually negative)
    clear_height_mm INTEGER,
    slab_thk_mm INTEGER,
    finish_mm INTEGER,
    drawing_ref TEXT
);

CREATE TABLE IF NOT EXISTS grids (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    uid TEXT NOT NULL UNIQUE,
    seq INTEGER NOT NULL,
    name TEXT NOT NULL,
    x1_mm INTEGER, y1_mm INTEGER, x2_mm INTEGER, y2_mm INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS drawings (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('incoming','outgoing')),
    title TEXT NOT NULL,
    discipline TEXT,
    floor TEXT,
    revision TEXT,
    date_received TEXT,
    is_current INTEGER NOT NULL DEFAULT 1,
    original_name TEXT NOT NULL,
    stored_name TEXT NOT NULL UNIQUE,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    uploaded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    project_id INTEGER REFERENCES projects(id),
    language TEXT NOT NULL DEFAULT 'ar',
    status TEXT NOT NULL DEFAULT 'draft',
    quote_date TEXT NOT NULL,
    validity_days INTEGER,
    delivery_period TEXT,
    payment_terms TEXT,
    exclusions TEXT,
    notes TEXT,
    building_description TEXT,
    location_text TEXT,
    discount_fils INTEGER NOT NULL DEFAULT 0,
    items TEXT NOT NULL DEFAULT '[]',     -- [{"description":..., "scope":..., "amount_fils":...}]
    scope TEXT NOT NULL DEFAULT '[]',
    sent_at TEXT,
    pdf_stored_name TEXT,                 -- immutable snapshot of what was sent
    pdf_sha256 TEXT,
    accepted_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (number, revision),
    CHECK (status IN ('draft','sent','accepted','rejected','expired','superseded')),
    CHECK (discount_fils >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    date_received TEXT NOT NULL,
    amount_fils INTEGER NOT NULL,         -- negative only for a reversal
    method TEXT,
    reference TEXT,
    payer TEXT,
    notes TEXT,
    reverses_payment_id INTEGER REFERENCES payments(id),
    request_key TEXT NOT NULL UNIQUE,     -- retry-safe write id
    created_at TEXT NOT NULL,
    CHECK (amount_fils <> 0)
);

CREATE TABLE IF NOT EXISTS allocations (
    id INTEGER PRIMARY KEY,
    payment_id INTEGER NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    project_id INTEGER NOT NULL REFERENCES projects(id),
    amount_fils INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    delivery_date TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '[]',
    completes_scope INTEGER NOT NULL DEFAULT 0,
    drawing_id INTEGER REFERENCES drawings(id),
    file_revision TEXT,
    recipient TEXT,
    method TEXT,
    notes TEXT,
    rules_used TEXT NOT NULL DEFAULT '[]',   -- [{"rule_id":..,"revision":..,"text":..}]
    request_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    language TEXT NOT NULL,
    to_addr TEXT,
    subject TEXT,
    body TEXT,
    attachments TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'sent',
    sent_via TEXT,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL UNIQUE,              -- stable entry ID (survives export/import)
    kind TEXT NOT NULL DEFAULT 'instruction' CHECK (kind IN ('instruction','decision')),
    topic TEXT,
    status TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('approved','draft','example')),
    source_ref TEXT,
    effective_date TEXT,
    request_key TEXT UNIQUE,               -- duplicate capture on retry
    scope TEXT NOT NULL CHECK (scope IN ('company','client','project')),
    client_id INTEGER REFERENCES clients(id),
    project_id INTEGER REFERENCES projects(id),
    trade TEXT,
    title TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    current_revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((scope = 'company' AND client_id IS NULL AND project_id IS NULL)
        OR (scope = 'client' AND client_id IS NOT NULL AND project_id IS NULL)
        OR (scope = 'project' AND project_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS rule_revisions (
    id INTEGER PRIMARY KEY,
    rule_id INTEGER NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL,
    text TEXT NOT NULL,
    original_wording TEXT,
    reason TEXT,
    author TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (rule_id, revision)
);

CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor TEXT,
    project_id INTEGER,
    client_id INTEGER,
    action TEXT NOT NULL,
    detail TEXT,
    before TEXT,
    after TEXT
);

CREATE INDEX IF NOT EXISTS ix_projects_client ON projects(client_id);
CREATE INDEX IF NOT EXISTS ix_levels_project ON levels(project_id, seq);
CREATE INDEX IF NOT EXISTS ix_grids_project ON grids(project_id, seq);
CREATE INDEX IF NOT EXISTS ix_drawings_project ON drawings(project_id);
CREATE INDEX IF NOT EXISTS ix_alloc_project ON allocations(project_id);
CREATE INDEX IF NOT EXISTS ix_payments_date ON payments(date_received);
CREATE INDEX IF NOT EXISTS ix_activity_project ON activity(project_id, at);
"""

DEFAULT_SETTINGS = {
    "company_name": "BASE Design",
    "company_name_ar": "بيس ديزاين",
    "company_email": "basedesignkw@gmail.com",
    "company_phone": "",
    "company_address": "Kuwait",
    "quotation_footer": "BASE Design | Quantity takeoff & BOQ services | Kuwait",
    "quotation_footer_ar": "BASE Design | خدمات حصر الكميات وجداول الكميات | الكويت",
    "number_prefix": "BASE",
    "quote_prefix": "QT",
    "default_validity_days": "14",
    "default_payment_terms": "50% on acceptance, 50% on delivery",
    "default_payment_terms_ar": "٥٠٪ عند الموافقة و ٥٠٪ عند التسليم",
    "logo_stored_name": "",
    "scope_presets": json.dumps({
        "هيكل أسود + طابوق": ["concrete", "rebar", "blockwork"],
        "Structure only / هيكل أسود": ["concrete", "rebar"],
        "Finishes / تشطيبات": ["plaster", "waterproofing", "tiling", "skirting", "ceilings", "cladding"],
        "Full BOQ / كامل": ["concrete", "rebar", "blockwork", "plaster", "waterproofing", "tiling",
                             "skirting", "ceilings", "cladding"],
    }, ensure_ascii=False),
}


def connect(path):
    conn = sqlite3.connect(path, timeout=15, detect_types=0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path):
    conn = connect(path)
    conn.executescript(SCHEMA)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


def new_uid():
    return uuid.uuid4().hex


def get_settings(db=None):
    db = db or get_db()
    return {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}


def set_setting(db, key, value):
    db.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, value))
