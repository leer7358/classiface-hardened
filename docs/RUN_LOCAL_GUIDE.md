# Run SecureTest Locally

This guide starts the app from a fresh terminal on your own machine.

## 1. Open The Project

Open PowerShell, then go to the project folder:

```powershell
cd "C:\Users\Hyst3ria\Downloads\SecureTest Version 3\Intelligent-Face-Recognition-Attendance-System"
```

## 2. Activate The Virtual Environment

```powershell
.\venv\Scripts\Activate.ps1
```

If PowerShell blocks activation, run this once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Then activate the venv again.

## 3. Install Dependencies If Needed

If this is the first time running after moving/copying the project:

```powershell
pip install -r requirements.txt
pip install vendor\dlib-19.22.99-cp310-cp310-win_amd64.whl
```

## 4. Check Configuration

Create your local config from the example:

```powershell
Copy-Item configs\database.example.yaml configs\database.yaml
```

Open:

```text
configs\database.yaml
```

Make sure these are correct:

- PostgreSQL host, port, database, user, and password.
- Firebase `databaseURL`.
- Firebase `pathToServiceAccount`, usually `configs/serviceAccountKey.json`.

Make sure this file exists:

```text
configs\serviceAccountKey.json
```

Do not commit `configs\database.yaml`, `configs\serviceAccountKey.json`, or `.env`.

Make sure `.env` exists and contains `EMBED_KEY`. This key encrypts face embeddings before they are saved to Firebase:

```powershell
$bytes = New-Object byte[] 32
$rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
$rng.GetBytes($bytes)
$rng.Dispose()
"EMBED_KEY=`"$([Convert]::ToBase64String($bytes))`"" | Set-Content .env
```

## 5. Start PostgreSQL

Make sure PostgreSQL is running before starting the app.

Quick database check:

```powershell
.\venv\Scripts\python.exe tools\checks\verify_connection.py
```

## 6. Run The App

```powershell
$env:PYTHONIOENCODING="utf-8"
python app.py
```

Expected output includes:

```text
Running on http://127.0.0.1:5000
```

Open the app:

```text
http://127.0.0.1:5000
```

Health check:

```text
http://127.0.0.1:5000/api/health
```

## 7. Stop The App

In the terminal where the app is running, press:

```text
CTRL + C
```

## Useful Commands

Run a database/session diagnostic:

```powershell
.\venv\Scripts\python.exe tools\checks\find_session_table.py
```

Create an admin database record:

```powershell
.\venv\Scripts\python.exe tools\admin\create_admin.py --default
```

Run Python compile check:

```powershell
.\venv\Scripts\python.exe -m compileall -q app.py controllers tools tests services utils detection
```

## Folder Notes

- `app.py` starts the whole Flask app.
- `controllers/` contains the split route/controller files.
- `configs/` contains database and Firebase config.
- `database/` contains SQL setup files.
- `tools/` contains admin, check, debug, and migration scripts.
- `tests/` contains manual test scripts.
- `logs/` contains runtime logs.
- `vendor/` contains local wheel files like dlib.

## Common Problems

### Port 5000 Is Already In Use

Find the process:

```powershell
netstat -ano | Select-String ":5000"
```

Stop it by PID:

```powershell
Stop-Process -Id <PID> -Force
```

### Missing `cv2`, `flask`, Or Other Packages

Activate the venv, then install requirements:

```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Firebase Service Account Error

Check that this file exists:

```text
configs\serviceAccountKey.json
```

Then confirm `configs\database.yaml` points to it.

### PostgreSQL Connection Error

Check:

- PostgreSQL service is running.
- Database name exists.
- Username and password in `configs\database.yaml` are correct.
