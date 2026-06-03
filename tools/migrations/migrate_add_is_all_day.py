#!/usr/bin/env python3
"""
Migration: Add is_all_day column to track session type
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
        print("Adding is_all_day column...")
        
        cur.execute('''
            ALTER TABLE class_sessions
            ADD COLUMN IF NOT EXISTS is_all_day BOOLEAN DEFAULT FALSE;
        ''')
        
        print("✅ is_all_day column added")
        
        # Mark sessions with 00:00 start time as all-day (for existing data)
        cur.execute('''
            UPDATE class_sessions
            SET is_all_day = TRUE
            WHERE present_start = '00:00:00' AND is_all_day = FALSE;
        ''')
        
        rows_updated = cur.rowcount
        print(f"✅ Marked {rows_updated} existing all-day sessions")
        
        conn.commit()
        print("\n✅ Migration completed!")
        sys.exit(0)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
