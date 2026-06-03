"""Force reset session_end to NULL for all sessions"""
from app import pg_conn

with pg_conn() as conn, conn.cursor() as cur:
    cur.execute("UPDATE class_sessions SET session_end = NULL")
    conn.commit()
    print(f"Reset {cur.rowcount} sessions to NULL")
