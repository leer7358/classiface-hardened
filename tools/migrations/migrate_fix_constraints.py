#!/usr/bin/env python3
"""
Migration: Update session constraint from session_date to start_date/end_date range
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
        print("Updating database constraints...")
        
        # Drop the old UNIQUE constraint if it exists
        try:
            cur.execute('''
                ALTER TABLE class_sessions 
                DROP CONSTRAINT IF EXISTS class_sessions_class_id_session_date_key;
            ''')
            print("✅ Dropped old constraint on (class_id, session_date)")
        except Exception as e:
            print(f"   Note: {e}")
        
        # Add the new UNIQUE constraint on (class_id, start_date, end_date)
        try:
            cur.execute('''
                ALTER TABLE class_sessions
                ADD CONSTRAINT class_sessions_class_id_date_range_key 
                UNIQUE (class_id, start_date, end_date);
            ''')
            print("✅ Added constraint on (class_id, start_date, end_date)")
        except psycopg2.Error as e:
            if 'already exists' in str(e):
                print("✅ Constraint on (class_id, start_date, end_date) already exists")
            else:
                raise
        
        conn.commit()
        print("\n✅ Migration completed!")
        sys.exit(0)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
