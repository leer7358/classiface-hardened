#!/usr/bin/env python3
"""
Update existing sessions to populate start_date and end_date from session_date
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
        # Update sessions where start_date or end_date is NULL
        # Use session_date as the value for both start_date and end_date
        cur.execute('''
            UPDATE class_sessions
            SET 
                start_date = COALESCE(start_date, session_date),
                end_date = COALESCE(end_date, session_date)
            WHERE start_date IS NULL OR end_date IS NULL;
        ''')
        
        rows_updated = cur.rowcount
        conn.commit()
        
        print(f"✅ Updated {rows_updated} session records")
        
        # Show summary
        cur.execute('''
            SELECT COUNT(*) as total_sessions,
                   COUNT(CASE WHEN start_date IS NOT NULL AND end_date IS NOT NULL THEN 1 END) as sessions_with_dates
            FROM class_sessions;
        ''')
        
        total, with_dates = cur.fetchone()
        print(f"\nSession Summary:")
        print(f"  Total sessions: {total}")
        print(f"  Sessions with date ranges: {with_dates}")
        
    conn.close()
    print("\n✅ Migration completed!")
    sys.exit(0)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
