"""Controller registry for the SecureTest Flask app."""
from importlib import import_module

_CONTROLLER_MODULES = (
    "auth_controller",
    "student_controller",
    "admin_controller",
    "instructor_controller",
    "quiz_controller",
    "camera_controller",
    "face_controller",
    "settings_controller",
    "system_controller",
    "rest_api_controller",
    "socket_controller",
)


def register_controllers():
    for module_name in _CONTROLLER_MODULES:
        import_module(f"{__name__}.{module_name}")
