"""
Seed the Protocol Library database.
Run: python seed_db.py
"""

import os
import sys
sys.stdout.reconfigure(encoding='utf-8')
from protocol_db import initialize_db, get_db_path, get_connection

def seed_skyridge(db_path=None):
    """Initialize DB schema and load Sky Ridge seed data."""
    path = db_path or get_db_path()
    
    # 1. Create schema
    initialize_db(path)
    
    # 2. Execute seed SQL
    seed_sql_path = os.path.join("data", "protocols", "skyridge_seed.sql")
    if not os.path.exists(seed_sql_path):
        print(f"❌ Seed file not found: {seed_sql_path}")
        return
    
    with open(seed_sql_path, 'r', encoding='utf-8') as f:
        seed_sql = f.read()
    
    with get_connection(path) as conn:
        conn.executescript(seed_sql)
    
    # 3. Verify
    with get_connection(path) as conn:
        counts = {}
        for table in ['institution', 'scanner', 'imaging_protocol', 'protocol_step',
                       'contrast_rule', 'ir_protocol', 'ir_lab_threshold', 
                       'ir_med_hold', 'acr_protocol_map']:
            row = conn.execute(f"SELECT COUNT(*) as cnt FROM {table}").fetchone()
            counts[table] = row['cnt']
    
    print("\n📊 Database Summary:")
    print("─" * 40)
    for table, count in counts.items():
        print(f"  {table:25s} {count:>4d} rows")
    print("─" * 40)
    print(f"\n✅ Sky Ridge seed data loaded at: {path}")


if __name__ == "__main__":
    seed_skyridge()
