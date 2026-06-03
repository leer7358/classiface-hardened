#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from app import pg_conn
from datetime import datetime, date, time

# BM14 class
class_id = "30acd460-60c2-4cff-a867-bd1a508df646"

# Set new session window to include current time (22:37)
# Present: 22:00 - 23:30
new_present_start = time(22, 0)
new_present_until = time(23, 30)

with pg_conn() as conn, conn.cursor() as cur:
    cur.execute(
        """
        UPDATE class_sessions 
        SET present_start = %s, present_until = %s
        WHERE class_id = %s AND session_date = %s;
        """,
        (new_present_start, new_present_until, class_id, date.today())
    )
    conn.commit()
    print("✓ Updated BM14 session window:")
    print(f"  Present: {new_present_start} - {new_present_until}")
    print(f"  Current time: 22:37 is now WITHIN the window!")
