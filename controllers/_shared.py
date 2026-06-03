"""Shared controller bootstrap helpers.

Controller modules keep the original app-level helpers available while the
monolith is being split into smaller files. The app module is imported lazily so
`python app.py` and `flask --app app` both resolve to the same module object.
"""
from importlib import import_module


def app_module():
    return import_module("app")


def load_app_context(namespace: dict):
    module = app_module()
    namespace.update(
        {
            name: value
            for name, value in vars(module).items()
            if name not in {
                "__builtins__",
                "__cached__",
                "__doc__",
                "__file__",
                "__loader__",
                "__name__",
                "__package__",
                "__spec__",
            }
        }
    )
    namespace["_app_module"] = module


def _set_liveness_running(value: bool):
    app_module().is_liveness_running = bool(value)


# ============================================================
# HUMAN-READABLE VIOLATION LABELS
# Shared across:
# - admin dashboard
# - instructor monitoring
# - quiz monitoring
# ============================================================
VIOLATION_LABELS = {
    "tab_left": "Student Left the Quiz Tab",
    "tab_returned": "Student Returned to Quiz Tab",
    "face_mismatch": "Face Mismatch Detected",
    "no_face_pause": "No Face Detected for Too Long",
    "multiple_faces_pause": "Multiple Faces Detected",
    "re_verify_failed": "Re-verification Failed",
    "face_out_of_frame": "Face Out of Camera Frame",
    "looking_away": "Looking Away from Screen",
    "excessive_movement": "Excessive Movement Detected",
}


def human_violation_label(violation_type: str) -> str:
    key = str(violation_type or "").strip()
    return VIOLATION_LABELS.get(
        key,
        key.replace("_", " ").title(),
    )