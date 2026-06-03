# Project Structure

Runtime files stay at the project root so `python app.py` keeps working.

- `app.py` - Flask app bootstrap, configuration, shared database helpers, and controller registration.
- `controllers/` - HTTP and Socket.IO controllers split by feature area.
- `services/`, `utils/`, `detection/` - reusable application logic.
- `template/`, `static/`, `configs/` - Flask templates, assets, and configuration.
- `database/` - SQL setup files.
- `tools/` - maintenance scripts grouped by purpose:
  - `admin/` - admin/user utilities.
  - `checks/` - database and feature diagnostics.
  - `debug/` - focused debugging scripts.
  - `migrations/` - schema/data migration helpers.
- `tests/` - manual and script-based test helpers.
- `docs/` - setup and project documentation.
- `vendor/` - local binary packages such as the dlib wheel.
- `logs/` - runtime logs.
