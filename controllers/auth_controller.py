"""Authentication, registration, login/logout, and basic navigation routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


@app.route("/")
def index():
    return redirect(url_for("login"))

@app.route("/menu")
def menu():
    if not session.get("logged_in"):
        return redirect(url_for("login"))

    role = (session.get("role") or "").strip().lower()
    if role == "admin":
        return redirect(url_for("admin_dashboard"))
    if role == "instructor":
        return redirect(url_for("class_lists"))
    return redirect(url_for("class_lists"))

@app.route("/login", methods=["GET"])
def login():
    _ensure_csrf_token()
    return render_template("login.html")

@app.route("/admin-login", methods=["GET"])
def admin_login():
    """Dedicated admin portal login page."""
    _ensure_csrf_token()
    return render_template("admin_login.html")

@app.route("/logout", methods=["GET", "POST"])
def logout():
    # --------------------------------------------------------
    # CLEAR LIVENESS STATE
    # --------------------------------------------------------
    stream_key = session.get("stream_key")
    if stream_key:
        _clear_liveness_state(stream_key)

    # --------------------------------------------------------
    # CLEAR EMBEDDING CACHE
    # Prevents stale cached embeddings remaining in memory
    # after logout/session switch.
    # --------------------------------------------------------
    try:
        firebase_uid = session.get("firebase_uid")
        if firebase_uid:
            clear_embedding_cache(firebase_uid)
    except Exception as err:
        app.logger.warning(f"Failed to clear embedding cache: {type(err).__name__}")

    session.clear()

    return redirect(url_for("login"))

@app.route("/register", methods=["GET"])
def register():

    # --------------------------------------------------------
    # NORMAL PAGE REFRESH SHOULD RESET CAPTURE PROGRESS
    # --------------------------------------------------------
    # After successful capture:
    # /capture redirects to /register?keep=1
    #
    # Manual refresh/opening /register normally resets back to 0/5
    keep = request.args.get("keep") == "1"

    if not keep:

        ts_key = session.get("ts")

        if ts_key:
            _pending_store_pop(ts_key)

        session.pop("ts", None)
        session.pop("challenge_text", None)
        session.pop("challenge_direction", None)
        session.pop("challenge_blinks", None)
        session.pop("camera_mode", None)

        _delete_preview_file()

    _ensure_csrf_token()

    ts_key = session.get("ts", "")

    captures_done = (
        _pending_store_get_count(ts_key)
        if ts_key else 0
    )

    return render_template(
        "register.html",
        captures_done=captures_done,
        captures_required=REGISTRATION_SAMPLE_COUNT,
    )

@app.route("/api/auth/register-profile", methods=["POST"])
def api_auth_register_profile():
    """
    Frontend should:
      - Create Firebase Auth user (email+password)
      - Get idToken
      - Call this endpoint with:
        { idToken, first_name, last_name, role }
    Saves:
      - users.firebase_uid mapping + profile in Postgres
      - CHANGED: list of embeddings into Firebase RTDB at Embeddings/<firebase_uid>/embeddings_enc_list

    NOTE:
      - Public registration only allows student/instructor
      - Admin must be created manually
    """
    if not _require_csrf_json():
        return fail("CSRF failed", 400)

    data = request.get_json(silent=True) or {}
    id_token = (data.get("idToken") or "").strip()
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    role = (data.get("role") or "student").strip().lower()

    app.logger.info(f"Registration request: role={role}")

    if not id_token:
        return fail("Missing idToken", 400)
    if not first_name or not last_name:
        return fail("Missing first_name/last_name", 400)
    if role not in ("student", "instructor"):
        return fail("Invalid role", 400)

    decoded = fb_verify_id_token(id_token)
    if not decoded:
        return fail("Invalid or expired token", 401)

    firebase_uid = str(fb_get_claim(decoded, "uid", ""))
    email = (fb_get_claim(decoded, "email", "") or "").lower()

    if not firebase_uid or not email:
        return fail("Invalid token payload (uid/email missing)", 401)

    ts_key = session.get("ts")
    app.logger.debug(f"Timestamp key from session: {ts_key is not None}")
    if not ts_key:
        return fail("Please capture your face first.", 400)

    emb_lists = _pending_store_get(ts_key)
    app.logger.debug(f"Embeddings retrieved: {emb_lists is not None}, count: {len(emb_lists) if emb_lists else 0}")
    if emb_lists is None or len(emb_lists) == 0:
        return fail("Capture expired or missing. Please capture your face again.", 400)

    # CHANGED: Enforce minimum number of samples before registration is allowed
    if len(emb_lists) < REGISTRATION_SAMPLE_COUNT:
        remaining = REGISTRATION_SAMPLE_COUNT - len(emb_lists)
        return fail(
            f"Not enough face samples. You have {len(emb_lists)}/{REGISTRATION_SAMPLE_COUNT}. "
            f"Please capture {remaining} more time(s) before registering.",
            400
        )

    # CHANGED: Validate each embedding in the list
    for i, emb in enumerate(emb_lists):
        if not isinstance(emb, list) or len(emb) != 128:
            return fail(f"Invalid capture data at sample {i+1}. Please capture again.", 400)

    try:
        encrypt_embedding(emb_lists[0])
    except Exception as e:
        app.logger.error(f"Embedding encryption error: {type(e).__name__}: {str(e)}")
        return fail(f"Embedding encryption error: {str(e)}", 500)

    try:
        pg_create_or_update_user_profile(
            firebase_uid=firebase_uid,
            first_name=first_name,
            last_name=last_name,
            email=email,
            role=role,
        )
        app.logger.info(f"User profile created in PostgreSQL: {_mask_uid(firebase_uid)}")
    except Exception as e:
        app.logger.error(f"PostgreSQL error: {type(e).__name__}: {str(e)}")
        return fail(f"PostgreSQL error: {str(e)}", 500)

    try:
        app.logger.info(f"Saving {len(emb_lists)} embedding(s) to Firebase for {_mask_uid(firebase_uid)}")
        fb_set_embedding_enc_list(firebase_uid, emb_lists)
        app.logger.info(f"{len(emb_lists)} embedding(s) saved to Firebase for user {_mask_uid(firebase_uid)}")
    except Exception as e:
        app.logger.error(f"Firebase DB error: {type(e).__name__}: {str(e)}", exc_info=True)
        return fail(f"Firebase DB error: {str(e)}", 500)

    _pending_store_pop(ts_key)
    stream_key = session.get("stream_key")
    if stream_key:
        _clear_liveness_state(stream_key)
    session.pop("stream_key", None)
    session.pop("ts", None)
    session.pop("challenge_text", None)
    session.pop("challenge_direction", None)
    session.pop("challenge_blinks", None)
    session.pop("camera_mode", None)
    _delete_preview_file()

    row = pg_find_user_by_firebase_uid(firebase_uid) or {}
    return ok(
        {"firebase_uid": firebase_uid, "user_id": str(row.get("id", "")), "email": email, "role": role},
        "Profile registered"
    )

@app.route("/api/auth/session", methods=["POST"])
def api_auth_session():
    """
    Frontend sends: { idToken }
    Backend verifies and creates Flask session.
    """
    data = request.get_json(silent=True) or {}
    id_token = (data.get("idToken") or "").strip()
    if not id_token:
        return fail("Missing idToken", 400)

    decoded = fb_verify_id_token(id_token)
    if not decoded:
        return fail("Invalid or expired token", 401)

    firebase_uid = str(fb_get_claim(decoded, "uid", ""))
    email = (fb_get_claim(decoded, "email", "") or "").lower()
    name = fb_get_claim(decoded, "name", "") or email or "User"

    if not firebase_uid:
        return fail("Invalid token payload (no uid)", 401)

    user_row = pg_find_user_by_firebase_uid(firebase_uid)

    # If user doesn't exist in PostgreSQL, auto-create a profile from Firebase data
    if not user_row:
        try:
            firebase_name = name if name != email else ""
            names = firebase_name.split(" ", 1) if firebase_name else ["User", ""]
            first_name = names[0] if names else "User"
            last_name = names[1] if len(names) > 1 else ""
            full_name = f"{first_name} {last_name}".strip()

            with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO users (firebase_uid, email, first_name, last_name, full_name, role)
                    VALUES (%s, %s, %s, %s, %s, 'student')
                    RETURNING id, role
                """, (firebase_uid, email, first_name, last_name, full_name))

                user_row = cur.fetchone()
                conn.commit()
        except Exception as err:
            print(f"Error auto-creating user profile: {str(err)}")
            return fail(f"Error creating user profile: {str(err)}", 500)

    role = (user_row.get("role") or "student").strip().lower()
    if role not in ("student", "instructor", "admin"):
        role = "student"

    # Public login only for students and instructors - admins must use /admin-login
    if role == "admin":
        return fail("Account not found. Please check your credentials.", 401)

    pg_user_id = str(user_row.get("id"))

    stream_key = session.get("stream_key")
    if stream_key:
        _clear_liveness_state(stream_key)
    session.clear()
    _ensure_csrf_token()
    session["logged_in"] = True
    session["role"] = role
    session["user_id"] = pg_user_id
    session["firebase_uid"] = firebase_uid

    if role == "instructor":
        session["instructor_name"] = user_row.get("full_name") or name
    elif role == "admin":
        session["admin_name"] = user_row.get("full_name") or name
    else:
        session["student_name"] = user_row.get("full_name") or name
        session["quiz_verified"] = False
        session["verified_name"] = ""
        session.pop("pending_quiz_id", None)

    session.pop("active_class_id", None)
    session.pop("active_class_name", None)

    return ok(
        {"firebase_uid": firebase_uid, "user_id": pg_user_id, "email": email, "role": role},
        "Session created"
    )

@app.route("/api/auth/password-session", methods=["POST"])
def api_auth_password_session():
    """
    Backend email/password login.
    This avoids browser-side Firebase network failures by letting Render call
    Firebase Auth REST, then reusing the normal verified-token session flow.
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return fail("Missing email or password", 400)

    try:
        res = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={firebase_web_api_key()}",
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=20,
        )
    except requests.RequestException as err:
        app.logger.error(f"Firebase Auth REST request failed: {type(err).__name__}: {err}")
        return fail("Unable to reach Firebase Auth. Please try again.", 502)

    try:
        auth_payload = res.json() if res.content else {}
    except ValueError:
        app.logger.error("Firebase Auth REST returned non-JSON response: status=%s body=%s", res.status_code, res.text[:300])
        return fail(f"Firebase Auth returned an invalid response ({res.status_code})", 502)

    if not res.ok:
        firebase_code = ((auth_payload.get("error") or {}).get("message") or "LOGIN_FAILED")
        app.logger.warning(f"Firebase password login failed for {_mask_email(email)}: {firebase_code}")
        if firebase_code in ("EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS"):
            return fail("Account not found. Please check your credentials.", 401)
        return fail(f"Firebase Auth error: {firebase_code}", 502)

    id_token = (auth_payload.get("idToken") or "").strip()
    if not id_token:
        return fail("Firebase did not return an ID token", 502)

    decoded = fb_verify_id_token(id_token)
    if not decoded:
        return fail("Invalid or expired token", 401)

    firebase_uid = str(fb_get_claim(decoded, "uid", ""))
    verified_email = (fb_get_claim(decoded, "email", "") or email).lower()
    name = fb_get_claim(decoded, "name", "") or verified_email or "User"

    if not firebase_uid:
        return fail("Invalid token payload (no uid)", 401)

    user_row = pg_find_user_by_firebase_uid(firebase_uid)

    if not user_row:
        try:
            firebase_name = name if name != verified_email else ""
            names = firebase_name.split(" ", 1) if firebase_name else ["User", ""]
            first_name = names[0] if names else "User"
            last_name = names[1] if len(names) > 1 else ""
            full_name = f"{first_name} {last_name}".strip()

            with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    INSERT INTO users (firebase_uid, email, first_name, last_name, full_name, role)
                    VALUES (%s, %s, %s, %s, %s, 'student')
                    RETURNING id, role, full_name
                """, (firebase_uid, verified_email, first_name, last_name, full_name))
                user_row = cur.fetchone()
                conn.commit()
        except Exception as err:
            app.logger.error(f"Error auto-creating user profile: {type(err).__name__}: {err}")
            return fail(f"Error creating user profile: {str(err)}", 500)

    role = (user_row.get("role") or "student").strip().lower()
    if role not in ("student", "instructor", "admin"):
        role = "student"
    if role == "admin":
        return fail("Account not found. Please check your credentials.", 401)

    pg_user_id = str(user_row.get("id"))

    stream_key = session.get("stream_key")
    if stream_key:
        _clear_liveness_state(stream_key)
    session.clear()
    _ensure_csrf_token()
    session["logged_in"] = True
    session["role"] = role
    session["user_id"] = pg_user_id
    session["firebase_uid"] = firebase_uid

    if role == "instructor":
        session["instructor_name"] = user_row.get("full_name") or name
    else:
        session["student_name"] = user_row.get("full_name") or name
        session["quiz_verified"] = False
        session["verified_name"] = ""
        session.pop("pending_quiz_id", None)

    session.pop("active_class_id", None)
    session.pop("active_class_name", None)

    return ok(
        {"firebase_uid": firebase_uid, "user_id": pg_user_id, "email": verified_email, "role": role},
        "Session created"
    )

@app.route("/api/auth/admin-session", methods=["POST"])
def api_auth_admin_session():
    """
    Admin-only login endpoint.
    Frontend sends: { idToken }
    Backend verifies user is an admin and creates Flask session.
    """
    data = request.get_json(silent=True) or {}
    id_token = (data.get("idToken") or "").strip()

    if not id_token:
        return fail("Missing idToken", 400)

    decoded = fb_verify_id_token(id_token)
    if not decoded:
        return fail("Invalid or expired token", 401)

    firebase_uid = str(fb_get_claim(decoded, "uid", ""))
    email = (fb_get_claim(decoded, "email", "") or "").lower()
    name = fb_get_claim(decoded, "name", "") or email or "Admin"

    if not firebase_uid or not email:
        return fail("Invalid token payload", 401)

    # Check database for admin user
    try:
        pg_conn_obj = pg_conn()
        cursor = pg_conn_obj.cursor()
        cursor.execute(
            "SELECT * FROM users WHERE email = %s AND role = %s",
            (email, "admin")
        )
        user_row = cursor.fetchone()
        cursor.close()
        pg_conn_obj.close()

        if not user_row:
            return fail("User is not an admin. Access denied.", 403)

        # Update Firebase UID if needed
        if not user_row.get("firebase_uid") or user_row.get("firebase_uid") != firebase_uid:
            pg_conn_obj = pg_conn()
            cursor = pg_conn_obj.cursor()
            cursor.execute(
                "UPDATE users SET firebase_uid = %s WHERE email = %s AND role = %s",
                (firebase_uid, email, "admin")
            )
            pg_conn_obj.commit()
            cursor.close()
            pg_conn_obj.close()

    except Exception as e:
        return fail(f"Database error: {str(e)}", 500)

    # Create Flask session
    pg_user_id = str(user_row.get("id", ""))
    stream_key = session.get("stream_key")
    if stream_key:
        _clear_liveness_state(stream_key)
    session.clear()
    _ensure_csrf_token()
    session["logged_in"] = True
    session["role"] = "admin"
    session["user_id"] = pg_user_id
    session["firebase_uid"] = firebase_uid
    session["admin_name"] = user_row.get("full_name") or name

    return ok(
        {"firebase_uid": firebase_uid, "user_id": pg_user_id, "email": email, "role": "admin"},
        "Admin session created"
    )

@app.route("/api/auth/admin-password-session", methods=["POST"])
def api_auth_admin_password_session():
    """Backend email/password login for the admin portal."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return fail("Missing email or password", 400)

    try:
        res = requests.post(
            f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={firebase_web_api_key()}",
            json={"email": email, "password": password, "returnSecureToken": True},
            timeout=20,
        )
    except requests.RequestException as err:
        app.logger.error(f"Firebase admin Auth REST request failed: {type(err).__name__}: {err}")
        return fail("Unable to reach Firebase Auth. Please try again.", 502)

    try:
        auth_payload = res.json() if res.content else {}
    except ValueError:
        app.logger.error("Firebase admin Auth returned non-JSON response: status=%s body=%s", res.status_code, res.text[:300])
        return fail(f"Firebase Auth returned an invalid response ({res.status_code})", 502)

    if not res.ok:
        firebase_code = ((auth_payload.get("error") or {}).get("message") or "LOGIN_FAILED")
        app.logger.warning(f"Firebase admin password login failed for {_mask_email(email)}: {firebase_code}")
        if firebase_code in ("EMAIL_NOT_FOUND", "INVALID_PASSWORD", "INVALID_LOGIN_CREDENTIALS"):
            return fail("Admin account not found or password is incorrect.", 401)
        return fail(f"Firebase Auth error: {firebase_code}", 502)

    id_token = (auth_payload.get("idToken") or "").strip()
    decoded = fb_verify_id_token(id_token) if id_token else None
    if not decoded:
        return fail("Invalid or expired token", 401)

    firebase_uid = str(fb_get_claim(decoded, "uid", ""))
    verified_email = (fb_get_claim(decoded, "email", "") or email).lower()
    name = fb_get_claim(decoded, "name", "") or verified_email or "Admin"
    if not firebase_uid or not verified_email:
        return fail("Invalid token payload", 401)

    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM users WHERE email = %s AND role = %s",
                (verified_email, "admin"),
            )
            user_row = cur.fetchone()
            if not user_row:
                return fail("User is not an admin. Access denied.", 403)

            if not user_row.get("firebase_uid") or user_row.get("firebase_uid") != firebase_uid:
                cur.execute(
                    "UPDATE users SET firebase_uid = %s WHERE email = %s AND role = %s",
                    (firebase_uid, verified_email, "admin"),
                )
                conn.commit()
    except Exception as err:
        app.logger.error(f"Admin database login failed: {type(err).__name__}: {err}")
        return fail(f"Database error: {str(err)}", 500)

    pg_user_id = str(user_row.get("id", ""))
    stream_key = session.get("stream_key")
    if stream_key:
        _clear_liveness_state(stream_key)
    session.clear()
    _ensure_csrf_token()
    session["logged_in"] = True
    session["role"] = "admin"
    session["user_id"] = pg_user_id
    session["firebase_uid"] = firebase_uid
    session["admin_name"] = user_row.get("full_name") or name

    return ok(
        {"firebase_uid": firebase_uid, "user_id": pg_user_id, "email": verified_email, "role": "admin"},
        "Admin session created",
    )

@app.route("/test-email")
def test_email():
    # SECURITY: Admin-only debug endpoint
    if not session.get("logged_in") or session.get("role") != "admin":
        return "Unauthorized", 403
    
    try:
        sender_email = app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")

        print("MAIL SERVER:", app.config.get("MAIL_SERVER"), flush=True)
        print("MAIL PORT:", app.config.get("MAIL_PORT"), flush=True)
        print("MAIL USERNAME:", app.config.get("MAIL_USERNAME"), flush=True)
        print("MAIL DEFAULT SENDER:", app.config.get("MAIL_DEFAULT_SENDER"), flush=True)

        msg = Message(
            subject="ClassiFace Test Email",
            sender=sender_email,
            recipients=["leevillarama12@gmail.com"],
            body="This is a test email from ClassiFace."
        )

        mail.send(msg)
        return "Email sent successfully. Check your inbox or spam folder."

    except Exception as e:
        print("EMAIL ERROR:", e, flush=True)
        return f"Email failed: {e}"

@app.route("/api/auth/forgot-password", methods=["POST"])
def api_auth_forgot_password():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    print("FORGOT PASSWORD REQUEST RECEIVED for", _mask_email(email), flush=True)

    if not email:
        return jsonify({"ok": False, "message": "Please enter your email address."}), 400

    try:
        # 1. Check PostgreSQL
        with pg_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, firebase_uid, email, role
                FROM users
                WHERE LOWER(email) = LOWER(%s)
                LIMIT 1;
                """,
                (email,),
            )
            user_row = cur.fetchone()

        print("POSTGRES USER:", user_row, flush=True)

        if not user_row:
            return jsonify({
                "ok": False,
                "message": "Email exists in Firebase maybe, but not in PostgreSQL public.users."
            }), 404

        # 2. Check Firebase Auth
        try:
            firebase_user = fb_auth.get_user_by_email(email)
            print("FIREBASE USER FOUND: uid=", _mask_uid(firebase_user.uid), "email=", _mask_email(firebase_user.email), flush=True)
        except Exception as firebase_lookup_error:
            print("FIREBASE USER LOOKUP ERROR:", firebase_lookup_error, flush=True)
            return jsonify({
                "ok": False,
                "message": f"Email exists in PostgreSQL, but not found in Firebase Auth: {firebase_lookup_error}"
            }), 500

        # 3. Generate Firebase reset link
        try:
            reset_link = fb_auth.generate_password_reset_link(email)
            print("RESET LINK CREATED:", reset_link, flush=True)
        except Exception as reset_error:
            print("RESET LINK ERROR:", reset_error, flush=True)
            return jsonify({
                "ok": False,
                "message": f"Firebase could not create reset link: {reset_error}"
            }), 500

        # 4. Send email using Gmail SMTP
        try:
            sender_email = app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")

            print("MAIL SERVER:", app.config.get("MAIL_SERVER"), flush=True)
            print("MAIL PORT:", app.config.get("MAIL_PORT"), flush=True)
            print("MAIL USERNAME:", app.config.get("MAIL_USERNAME"), flush=True)
            print("MAIL SENDER:", sender_email, flush=True)

            msg = Message(
                subject="ClassiFace Password Reset",
                sender=sender_email,
                recipients=[email],
                body=f"""
Hello,

You requested to reset your ClassiFace password.

Click this link to reset your password:
{reset_link}

If you did not request this, please ignore this email.

ClassiFace System
"""
            )

            mail.send(msg)
            print("RESET EMAIL SENT SUCCESSFULLY", flush=True)

        except Exception as mail_error:
            print("MAIL SEND ERROR:", mail_error, flush=True)
            return jsonify({
                "ok": False,
                "message": f"Reset link was created, but email sending failed: {mail_error}"
            }), 500

        return jsonify({
            "ok": True,
            "message": "Password reset link has been sent. Check your inbox or spam folder."
        }), 200

    except Exception as e:
        print("FORGOT PASSWORD GENERAL ERROR:", e, flush=True)
        return jsonify({
            "ok": False,
            "message": f"Forgot password failed: {e}"
        }), 500
