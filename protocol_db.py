"""
Protocol Library Database — SQLite Schema & Query Layer
========================================================
Manages the relational store that maps ACR Appropriateness Criteria 
(Service A / RAG output) to hospital-specific scan "recipes."

Design:
  - 10 normalized tables across 4 domains
  - JSON columns for flexible parameters (phases, sequences, renal adjustments)
  - Hospital-swappable: change PROTOCOL_DB_PATH in .env to swap institutions
  - Dual-mode ready: works with synthetic FHIR bundles (demo) and live EHR data
"""

import os
import json
import sqlite3
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
from datetime import datetime

# Default path — override via PROTOCOL_DB_PATH env var
DEFAULT_DB_PATH = os.path.join("data", "protocols", "skyridge_protocols.db")


def get_db_path() -> str:
    """Resolve the active protocol database file path."""
    return os.environ.get("PROTOCOL_DB_PATH", DEFAULT_DB_PATH)


@contextmanager
def get_connection(db_path: Optional[str] = None):
    """
    Context manager for SQLite connections.
    Returns rows as sqlite3.Row (dict-like access).
    """
    path = db_path or get_db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Schema Creation
# ─────────────────────────────────────────────

SCHEMA_SQL = """
-- =============================================
-- Domain 1: Institutional Identity
-- =============================================

CREATE TABLE IF NOT EXISTS institution (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    ehr_system      TEXT,
    timezone        TEXT DEFAULT 'America/Denver',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scanner (
    id              TEXT PRIMARY KEY,
    institution_id  TEXT NOT NULL REFERENCES institution(id),
    modality        TEXT NOT NULL CHECK(modality IN ('CT','MRI','US','NM','FLUORO','ANGIO','XRAY')),
    manufacturer    TEXT,
    model           TEXT,
    capabilities    TEXT,   -- JSON array, e.g. ["dual_energy","photon_counting"]
    is_active       BOOLEAN DEFAULT 1
);

-- =============================================
-- Domain 2: Diagnostic Imaging Protocols
-- =============================================

CREATE TABLE IF NOT EXISTS imaging_protocol (
    id                      TEXT PRIMARY KEY,
    institution_id          TEXT NOT NULL REFERENCES institution(id),
    scanner_id              TEXT REFERENCES scanner(id),
    name                    TEXT NOT NULL,
    modality                TEXT NOT NULL CHECK(modality IN ('CT','MRI','US','NM','FLUORO','XRAY')),
    body_region             TEXT NOT NULL,
    clinical_indication     TEXT,
    contrast_type           TEXT CHECK(contrast_type IN ('iv','oral','iv_oral','none','rectal','iv_rectal',NULL)),
    contrast_agent          TEXT,
    contrast_volume_ml      REAL,
    contrast_rate_ml_s      REAL,
    phases                  TEXT,   -- JSON array, e.g. ["portal_venous"]
    oral_prep               TEXT,
    oral_prep_conditions    TEXT,   -- JSON, e.g. {"bmi_lt": 25}
    patient_position        TEXT DEFAULT 'supine',
    slice_thickness_mm      REAL,
    reconstruction          TEXT,   -- JSON array, e.g. ["soft_tissue","bone"]
    special_instructions    TEXT,
    estimated_time_min      INTEGER,
    requires_iv_access      BOOLEAN DEFAULT 0,
    is_pediatric            BOOLEAN DEFAULT 0,
    is_active               BOOLEAN DEFAULT 1,
    version                 INTEGER DEFAULT 1,
    updated_by              TEXT,
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS protocol_step (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol_id     TEXT NOT NULL REFERENCES imaging_protocol(id),
    step_order      INTEGER NOT NULL,
    sequence_name   TEXT,
    parameters      TEXT,   -- JSON, e.g. {"TR":4000,"TE":80,"flip_angle":150}
    timing_description TEXT,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS contrast_rule (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    protocol_id             TEXT NOT NULL REFERENCES imaging_protocol(id),
    rule_type               TEXT NOT NULL CHECK(rule_type IN (
        'egfr_check','allergy_check','pregnancy_check','bmi_check','age_check'
    )),
    condition_json          TEXT NOT NULL,
    action_if_triggered     TEXT NOT NULL CHECK(action_if_triggered IN (
        'flag','substitute_protocol','require_premedication','cancel','hard_stop'
    )),
    substitute_protocol_id  TEXT REFERENCES imaging_protocol(id),
    premedication_text      TEXT,
    alert_message           TEXT NOT NULL,
    severity                TEXT DEFAULT 'warning' CHECK(severity IN ('info','warning','hard_stop'))
);

-- =============================================
-- Domain 3: IR Procedural Protocols
-- =============================================

CREATE TABLE IF NOT EXISTS ir_protocol (
    id                          TEXT PRIMARY KEY,
    institution_id              TEXT NOT NULL REFERENCES institution(id),
    name                        TEXT NOT NULL,
    procedure_category          TEXT NOT NULL CHECK(procedure_category IN (
        'biopsy','drainage','embolization','venous_access','ablation',
        'angioplasty','stent','thrombolysis','filter','other'
    )),
    body_region                 TEXT,
    sir_bleeding_risk           TEXT NOT NULL CHECK(sir_bleeding_risk IN ('low','moderate','significant')),
    imaging_guidance            TEXT,
    sedation_type               TEXT CHECK(sedation_type IN ('local_only','moderate','deep','general',NULL)),
    consent_required            BOOLEAN DEFAULT 1,
    estimated_time_min          INTEGER,
    pre_procedure_instructions  TEXT,
    post_procedure_instructions TEXT,
    special_equipment           TEXT,   -- JSON array
    is_active                   BOOLEAN DEFAULT 1,
    version                     INTEGER DEFAULT 1,
    updated_by                  TEXT,
    created_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at                  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ir_lab_threshold (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ir_protocol_id      TEXT NOT NULL REFERENCES ir_protocol(id),
    lab_name            TEXT NOT NULL,
    loinc_code          TEXT,
    threshold_operator  TEXT NOT NULL CHECK(threshold_operator IN ('<=','>=','<','>','between','==')),
    threshold_value     REAL NOT NULL,
    threshold_value_upper REAL,
    max_result_age_hours INTEGER DEFAULT 72,
    action_if_not_met   TEXT NOT NULL CHECK(action_if_not_met IN ('flag','hard_stop','correct_and_recheck')),
    correction_guidance TEXT,
    sir_reference       TEXT
);

CREATE TABLE IF NOT EXISTS ir_med_hold (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ir_protocol_id      TEXT NOT NULL REFERENCES ir_protocol(id),
    medication_name     TEXT NOT NULL,
    medication_class    TEXT CHECK(medication_class IN (
        'doac','vitamin_k_antagonist','antiplatelet','heparin','lmwh','nsaid','other'
    )),
    rxnorm_code         TEXT,
    hold_hours_before   INTEGER NOT NULL,
    resume_hours_after  INTEGER,
    bridging_required   BOOLEAN DEFAULT 0,
    bridging_protocol   TEXT,
    renal_adjustment    TEXT,   -- JSON, e.g. {"egfr_lt_30": {"hold_hours_before": 72}}
    sir_reference       TEXT
);

-- =============================================
-- Domain 4: ACR ↔ Protocol Bridge
-- =============================================

CREATE TABLE IF NOT EXISTS acr_protocol_map (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    institution_id      TEXT NOT NULL REFERENCES institution(id),
    acr_scenario_text   TEXT NOT NULL,
    acr_procedure_text  TEXT NOT NULL,
    acr_appropriateness TEXT,
    imaging_protocol_id TEXT REFERENCES imaging_protocol(id),
    ir_protocol_id      TEXT REFERENCES ir_protocol(id),
    match_confidence    REAL DEFAULT 0.0,
    mapping_method      TEXT DEFAULT 'manual_review' CHECK(mapping_method IN (
        'manual_review','automated_fuzzy_match','llm_assisted'
    )),
    mapped_by           TEXT,
    notes               TEXT,
    is_active           BOOLEAN DEFAULT 1,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- At least one protocol must be referenced
    CHECK (imaging_protocol_id IS NOT NULL OR ir_protocol_id IS NOT NULL)
);

-- Indexes for fast lookups
CREATE INDEX IF NOT EXISTS idx_acr_map_scenario ON acr_protocol_map(acr_scenario_text);
CREATE INDEX IF NOT EXISTS idx_acr_map_procedure ON acr_protocol_map(acr_procedure_text);
CREATE INDEX IF NOT EXISTS idx_acr_map_institution ON acr_protocol_map(institution_id);
CREATE INDEX IF NOT EXISTS idx_imaging_protocol_institution ON imaging_protocol(institution_id);
CREATE INDEX IF NOT EXISTS idx_imaging_protocol_modality ON imaging_protocol(modality, body_region);
CREATE INDEX IF NOT EXISTS idx_ir_protocol_institution ON ir_protocol(institution_id);
CREATE INDEX IF NOT EXISTS idx_contrast_rule_protocol ON contrast_rule(protocol_id);
CREATE INDEX IF NOT EXISTS idx_ir_lab_protocol ON ir_lab_threshold(ir_protocol_id);
CREATE INDEX IF NOT EXISTS idx_ir_med_protocol ON ir_med_hold(ir_protocol_id);
"""


def initialize_db(db_path: Optional[str] = None):
    """Create all tables if they don't exist."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
    print(f"✅ Protocol database initialized at: {db_path or get_db_path()}")


# ─────────────────────────────────────────────
# Query Functions
# ─────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a sqlite3.Row to a plain dict, parsing JSON columns."""
    d = dict(row)
    json_fields = [
        'capabilities', 'phases', 'oral_prep_conditions', 'reconstruction',
        'parameters', 'condition_json', 'special_equipment', 'renal_adjustment'
    ]
    for field in json_fields:
        if field in d and d[field] and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def lookup_protocol_by_acr(
    acr_procedure_text: str,
    institution_id: str,
    acr_scenario_text: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Core bridge query: given an ACR procedure string (from RAG output),
    find the matching hospital-specific protocol(s).
    
    Uses LIKE matching for flexibility since ACR text may not be an exact match.
    Returns the full protocol details with all safety rules attached.
    """
    with get_connection(db_path) as conn:
        # First, find the bridge mapping
        query = """
            SELECT apm.*, 
                   ip.name as protocol_name, ip.modality, ip.body_region,
                   ip.contrast_type, ip.contrast_agent, ip.contrast_volume_ml,
                   ip.contrast_rate_ml_s, ip.phases, ip.oral_prep,
                   ip.oral_prep_conditions, ip.slice_thickness_mm,
                   ip.reconstruction, ip.special_instructions,
                   ip.estimated_time_min, ip.requires_iv_access,
                   ip.patient_position
            FROM acr_protocol_map apm
            LEFT JOIN imaging_protocol ip ON apm.imaging_protocol_id = ip.id
            WHERE apm.institution_id = ?
              AND apm.acr_procedure_text LIKE ?
              AND apm.is_active = 1
        """
        params = [institution_id, f"%{acr_procedure_text}%"]
        
        if acr_scenario_text:
            query += " AND apm.acr_scenario_text LIKE ?"
            params.append(f"%{acr_scenario_text}%")
        
        query += " ORDER BY apm.match_confidence DESC"
        
        rows = conn.execute(query, params).fetchall()
        results = [_row_to_dict(r) for r in rows]
        
        # Attach safety rules to each result
        for result in results:
            if result.get('imaging_protocol_id'):
                result['contrast_rules'] = get_contrast_rules(
                    result['imaging_protocol_id'], db_path
                )
            if result.get('ir_protocol_id'):
                ir_data = get_ir_protocol_details(
                    result['ir_protocol_id'], db_path
                )
                result['ir_details'] = ir_data
        
        return results


def get_contrast_rules(
    protocol_id: str, 
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get all safety/contrast rules for an imaging protocol."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM contrast_rule WHERE protocol_id = ?",
            [protocol_id]
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_ir_protocol_details(
    ir_protocol_id: str, 
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Get full IR protocol with lab thresholds and med holds."""
    with get_connection(db_path) as conn:
        # Base protocol
        row = conn.execute(
            "SELECT * FROM ir_protocol WHERE id = ?",
            [ir_protocol_id]
        ).fetchone()
        
        if not row:
            return {}
        
        result = _row_to_dict(row)
        
        # Lab thresholds
        labs = conn.execute(
            "SELECT * FROM ir_lab_threshold WHERE ir_protocol_id = ?",
            [ir_protocol_id]
        ).fetchall()
        result['lab_thresholds'] = [_row_to_dict(r) for r in labs]
        
        # Med holds
        meds = conn.execute(
            "SELECT * FROM ir_med_hold WHERE ir_protocol_id = ?",
            [ir_protocol_id]
        ).fetchall()
        result['med_holds'] = [_row_to_dict(r) for r in meds]
        
        return result


def get_protocol_steps(
    protocol_id: str, 
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Get ordered sequence steps (especially useful for MRI protocols)."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM protocol_step WHERE protocol_id = ? ORDER BY step_order",
            [protocol_id]
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_protocols(
    institution_id: str,
    modality: Optional[str] = None,
    body_region: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List all imaging protocols, optionally filtered."""
    with get_connection(db_path) as conn:
        query = "SELECT * FROM imaging_protocol WHERE institution_id = ? AND is_active = 1"
        params = [institution_id]
        
        if modality:
            query += " AND modality = ?"
            params.append(modality)
        if body_region:
            query += " AND body_region = ?"
            params.append(body_region)
        
        query += " ORDER BY modality, body_region, name"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def list_ir_protocols(
    institution_id: str,
    category: Optional[str] = None,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List all IR protocols, optionally filtered by category."""
    with get_connection(db_path) as conn:
        query = "SELECT * FROM ir_protocol WHERE institution_id = ? AND is_active = 1"
        params = [institution_id]
        
        if category:
            query += " AND procedure_category = ?"
            params.append(category)
        
        query += " ORDER BY procedure_category, name"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]


def search_protocols_fulltext(
    institution_id: str,
    search_term: str,
    db_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search across both imaging and IR protocols by name or indication.
    Used by the LLM-assisted mapping tool to find candidate matches.
    """
    with get_connection(db_path) as conn:
        imaging = conn.execute(
            """SELECT id, name, modality, body_region, clinical_indication, 
                      'imaging' as protocol_type
               FROM imaging_protocol 
               WHERE institution_id = ? AND is_active = 1
                 AND (name LIKE ? OR clinical_indication LIKE ?)
               ORDER BY name""",
            [institution_id, f"%{search_term}%", f"%{search_term}%"]
        ).fetchall()
        
        ir = conn.execute(
            """SELECT id, name, procedure_category as modality, body_region, 
                      pre_procedure_instructions as clinical_indication,
                      'ir' as protocol_type
               FROM ir_protocol 
               WHERE institution_id = ? AND is_active = 1
                 AND (name LIKE ? OR body_region LIKE ?)
               ORDER BY name""",
            [institution_id, f"%{search_term}%", f"%{search_term}%"]
        ).fetchall()
        
        return [_row_to_dict(r) for r in imaging] + [_row_to_dict(r) for r in ir]


# ─────────────────────────────────────────────
# CLI Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        db_path = sys.argv[2] if len(sys.argv) > 2 else None
        initialize_db(db_path)
    else:
        print("Usage: python protocol_db.py init [optional_db_path]")
        print("  Initializes the protocol database with the full schema.")
