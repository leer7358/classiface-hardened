"""Health, session status, and debug utility routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


@app.route("/api/health")
def api_health():
    return ok({"status": "running"}, "API is healthy")

@app.route("/api/me")
def api_me():
    return ok(
        {
            "logged_in": bool(session.get("logged_in")),
            "role": session.get("role", ""),
            "user_id": session.get("user_id", ""),
            "firebase_uid": session.get("firebase_uid", ""),
            "student_name": session.get("student_name", ""),
            "instructor_name": session.get("instructor_name", ""),
            "admin_name": session.get("admin_name", ""),
            "active_class_id": session.get("active_class_id", ""),
            "active_class_name": session.get("active_class_name", ""),
        },
        "Session status",
    )

@app.route("/api/debug/which-db")
def api_debug_which_db():
    # SECURITY: Admin-only debug endpoint
    if not session.get("logged_in") or session.get("role") != "admin":
        return fail("Admin access only.", 403)
    
    with pg_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT current_database() AS db, current_schema() AS schema;")
        row = cur.fetchone() or {}
    return ok(row, "Connected Postgres target")
