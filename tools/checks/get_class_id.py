#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from app import pg_conn

# Get first class
with pg_conn() as conn, conn.cursor() as cur:
    cur.execute("SELECT id, section_name FROM classes LIMIT 1;")
    row = cur.fetchone()
    if row:
        print(f"Class ID: {row['id']}")
        print(f"Section: {row['section_name']}")
