"""
Migration script to populate session_end for existing sessions.
Defaults to 23:59 for any session that doesn't have it set.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import time as dtime
import sys
import os

# Import db connection from app
sys.path.insert(0, os.path.dirname(__file__))
from app import pg_conn

def migrate_session_end():
    """Update all sessions to calculate session_end = late_until + 45 minutes (absent window)"""
    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Check how many sessions need updating
            cur.execute(
                """
                SELECT COUNT(*) as count
                FROM class_sessions
                WHERE session_end IS NULL
                """
            )
            result = cur.fetchone()
            count = result['count'] if result else 0
            
            if count == 0:
                print("✓ All sessions already have session_end set.")
                return
            
            print(f"Updating {count} sessions with session_end = late_until + 45 minutes...")
            
            # Update sessions: session_end = late_until + 45 minutes (absent window)
            cur.execute(
                """
                UPDATE class_sessions
                SET session_end = late_until + INTERVAL '45 minutes'
                WHERE session_end IS NULL
                """
            )
            conn.commit()
            print(f"✓ Successfully updated {cur.rowcount} sessions")
            
    except Exception as e:
        print(f"❌ Migration error: {str(e)}", flush=True)
        raise

if __name__ == "__main__":
    migrate_session_end()
