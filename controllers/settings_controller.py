"""Instructor and student settings/password routes."""

from ._shared import _set_liveness_running, load_app_context


load_app_context(globals())


@app.route("/instructor/settings", methods=["GET"])
def instructor_settings():
    guard = instructor_required()
    if guard:
        return guard

    instructor_id = str(session.get("user_id") or "")
    
    # Fetch user data from database
    user_data = pg_find_user_by_pg_id(instructor_id)
    
    if not user_data:
        return redirect_with_msg("/class-lists", "User profile not found.")
    
    user_email = user_data.get("email") or ""
    user_name = user_data.get("full_name") or ""

    return render_template(
        "instructor_settings.html",
        user_email=user_email,
        user_name=user_name,
        active_page="settings"
    )

@app.route("/api/instructor/settings/save", methods=["POST"])
def api_save_instructor_settings():
    guard = instructor_required()
    if guard:
        return guard

    try:
        return ok(True, "Settings updated")
    except Exception as e:
        print(f"Error saving settings: {str(e)}")
        return ok(False, f"Error: {str(e)}"), 500

@app.route("/api/instructor/password/change", methods=["POST"])
def api_change_instructor_password():
    guard = instructor_required()
    if guard:
        return guard

    try:
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all([current_password, new_password, confirm_password]):
            return fail("All password fields are required", 400)

        if new_password != confirm_password:
            return fail("New passwords do not match", 400)

        # Validate password strength with Firebase policy
        is_valid, error_message, _ = validate_password_strength(new_password)
        if not is_valid:
            app.logger.warning(f"Password validation failed for instructor: {_mask_identifier(str(session.get('user_id')))}")
            return fail(error_message, 400)

        # Get instructor data from database
        instructor_id = str(session.get("user_id") or "")
        user_data = pg_find_user_by_pg_id(instructor_id)
        
        if not user_data:
            return fail("User profile not found", 404)
        
        firebase_uid = user_data.get("firebase_uid") or ""
        user_email = user_data.get("email") or ""
        
        if not firebase_uid or not user_email:
            return fail("Firebase UID or email not found", 400)
        
        # Verify current password using Firebase REST API.
        verify_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={firebase_web_api_key()}"
        verify_payload = {
            "email": user_email,
            "password": current_password,
            "returnSecureToken": False
        }
        
        verify_response = requests.post(verify_url, json=verify_payload)
        
        if verify_response.status_code != 200:
            error_data = verify_response.json()
            error_message = error_data.get("error", {}).get("message", "Invalid current password")
            if "INVALID_PASSWORD" in error_message or "INVALID_LOGIN_CREDENTIALS" in error_message:
                return fail("Current password is incorrect", 401)
            return fail("Failed to verify current password", 400)
        
        # Update password in Firebase
        fb_auth.update_user(firebase_uid, password=new_password)
        
        return ok(True, "Password changed successfully", 200)

    except Exception as e:
        app.logger.error(f"Student password change failed: {type(e).__name__}")
        return fail("Operation failed", 500)

@app.route("/student/settings", methods=["GET"])
def student_settings():
    guard = student_required()
    if guard:
        return guard

    student_id = str(session.get("user_id") or "")
    
    # Fetch user data from database
    user_data = pg_find_user_by_pg_id(student_id)
    
    if not user_data:
        return redirect_with_msg(url_for("stud_class_home"), "User profile not found.")
    
    user_email = user_data.get("email") or ""
    user_name = user_data.get("full_name") or ""

    return render_template(
        "student_settings.html",
        user_email=user_email,
        user_name=user_name,
        student_id=student_id,
        active_page="settings"
    )

@app.route("/api/student/settings/save", methods=["POST"])
def api_save_student_settings():
    guard = student_required()
    if guard:
        return guard

    try:
        return ok(True, "Settings updated")
    except Exception as e:
        app.logger.error(f"Admin settings update failed: {type(e).__name__}")
        return ok(False, "Operation failed"), 500

@app.route("/api/student/password/change", methods=["POST"])
def api_change_student_password():
    guard = student_required()
    if guard:
        return guard

    try:
        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")

        if not all([current_password, new_password, confirm_password]):
            return fail("All password fields are required", 400)

        if new_password != confirm_password:
            return fail("New passwords do not match", 400)

        # Validate password strength with Firebase policy
        is_valid, error_message, _ = validate_password_strength(new_password)
        if not is_valid:
            app.logger.warning(f"Password validation failed for student: {_mask_identifier(str(session.get('user_id')))}")
            return fail(error_message, 400)

        # Get student data from database
        student_id = str(session.get("user_id") or "")
        user_data = pg_find_user_by_pg_id(student_id)
        
        if not user_data:
            return fail("User profile not found", 404)
        
        firebase_uid = user_data.get("firebase_uid") or ""
        user_email = user_data.get("email") or ""
        
        if not firebase_uid or not user_email:
            return fail("Firebase UID or email not found", 400)
        
        # Verify current password using Firebase REST API.
        verify_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key={firebase_web_api_key()}"
        verify_payload = {
            "email": user_email,
            "password": current_password,
            "returnSecureToken": False
        }
        
        verify_response = requests.post(verify_url, json=verify_payload)
        
        if verify_response.status_code != 200:
            error_data = verify_response.json()
            error_message = error_data.get("error", {}).get("message", "Invalid current password")
            if "INVALID_PASSWORD" in error_message or "INVALID_LOGIN_CREDENTIALS" in error_message:
                return fail("Current password is incorrect", 401)
            return fail("Failed to verify current password", 400)
        
        # Update password in Firebase
        fb_auth.update_user(firebase_uid, password=new_password)
        
        return ok(True, "Password changed successfully", 200)

    except Exception as e:
        app.logger.error(f"Student password change failed: {type(e).__name__}")
        return fail("Operation failed", 500)
