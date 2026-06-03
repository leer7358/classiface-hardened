#!/usr/bin/env python3
"""
Migration: Add session_end column to class_sessions table
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
        print("Adding session_end column...")
        
        cur.execute('''
            ALTER TABLE class_sessions
            ADD COLUMN IF NOT EXISTS session_end time without time zone;
        ''')
        
        print("✅ session_end column added")
        
        conn.commit()
        print("\n✅ Migration completed!")
        sys.exit(0)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
