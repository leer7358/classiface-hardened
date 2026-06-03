"""Reset incorrect session_end values to NULL"""
from app import pg_conn

with pg_conn() as conn, conn.cursor() as cur:
    cur.execute("UPDATE class_sessions SET session_end = NULL WHERE session_end = '23:59:00'")
    conn.commit()
    print(f"Reset {cur.rowcount} sessions to NULL")
