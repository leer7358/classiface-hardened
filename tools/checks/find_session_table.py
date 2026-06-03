#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from app import pg_conn

# Find the correct table name
with pg_conn() as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name LIKE '%session%'
        ORDER BY table_name;
    """)
    tables = cur.fetchall()
    if tables:
        print("Found session-related tables:")
        for row in tables:
            print(f"  - {row['table_name']}")
    else:
        print("No session tables found")
