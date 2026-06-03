#!/usr/bin/env python3
"""
Verify and debug session data
"""
import sys
import os
import psycopg2

try:
    pg_password = os.environ.get("PGPASSWORD")
    if not pg_password:
        raise RuntimeError("Set PGPASSWORD before running this check.")

    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        database=os.environ.get("PGDATABASE", "setupdatabase"),
        user=os.environ.get("PGUSER", "postgres"),
        password=pg_password
    )
    
    with conn.cursor() as cur:
        print("=" * 80)
        print("SESSION DATA VERIFICATION")
        print("=" * 80)
        
        cur.execute('''
            SELECT 
              id,
              class_id,
              start_date,
              end_date,
              present_start,
              present_until,
              session_end,
              is_all_day,
              created_at
            FROM class_sessions
            ORDER BY created_at DESC;
        ''')
        
        sessions = cur.fetchall()
        if not sessions:
            print("No sessions found!")
        else:
            print(f"\nTotal sessions: {len(sessions)}\n")
            for i, sess in enumerate(sessions, 1):
                sess_id, class_id, start_date, end_date, present_start, present_until, session_end, is_all_day, created_at = sess
                print(f"Session {i}:")
                print(f"  ID: {sess_id}")
                print(f"  Date Range: {start_date} to {end_date}")
                print(f"  Is All Day: {is_all_day}")
                print(f"  Present Start: {present_start}")
                print(f"  Present Until: {present_until}")
                print(f"  Session End: {session_end}")
                print(f"  Created: {created_at}")
                print()
        
        # Check for potential issues
        print("=" * 80)
        print("POTENTIAL ISSUES:")
        print("=" * 80)
        
        # Check for manual sessions marked as all-day
        cur.execute('''
            SELECT COUNT(*) FROM class_sessions
            WHERE is_all_day = TRUE AND present_start != '00:00:00';
        ''')
        
        bad_count = cur.fetchone()[0]
        if bad_count > 0:
            print(f"⚠️  Found {bad_count} all-day sessions with non-00:00 start times!")
            print("   These should have is_all_day = FALSE")
            
            print("\nFixing...")
            cur.execute('''
                UPDATE class_sessions
                SET is_all_day = FALSE
                WHERE is_all_day = TRUE AND present_start != '00:00:00';
            ''')
            conn.commit()
            print(f"✅ Fixed {cur.rowcount} sessions")
        else:
            print("✅ No mixed sessions found")
        
        print("\n" + "=" * 80)
        print("VERIFICATION COMPLETE")
        print("=" * 80)
        
    conn.close()
    sys.exit(0)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
