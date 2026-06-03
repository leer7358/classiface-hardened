#!/usr/bin/env python3
"""
Migration: Populate session_end for existing all-day sessions
"""
import sys
import os
import psycopg2

try:
    pg_password = os.environ.get("PGPASSWORD")
    if not pg_password:
        raise RuntimeError("Set PGPASSWORD before running this migration.")

    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        database=os.environ.get("PGDATABASE", "setupdatabase"),
        user=os.environ.get("PGUSER", "postgres"),
        password=pg_password
    )
    
    with conn.cursor() as cur:
        print("Populating session_end for all-day sessions...")
        
        # For all-day sessions, set session_end to 23:59
        cur.execute('''
            UPDATE class_sessions
            SET session_end = '23:59:00'::time
            WHERE is_all_day = TRUE AND session_end IS NULL;
        ''')
        
        rows_updated = cur.rowcount
        print(f"✅ Updated {rows_updated} all-day sessions with session_end = 23:59")
        
        conn.commit()
        print("\n✅ Migration completed!")
        sys.exit(0)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
