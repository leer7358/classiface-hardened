1# 🚀 SecureTest App - Complete Setup Guide

## Prerequisites
- Windows 10/11
- Python 3.10+
- PostgreSQL 12+ (must be running)
- Git (optional)

---

## ✅ Step 1: Install & Start PostgreSQL

### Option A: Fresh PostgreSQL Installation
1. Download PostgreSQL from: https://www.postgresql.org/download/windows/
2. Run installer → Install with default settings
3. **Remember the password you set for postgres user** (default: 'postgres')
4. Keep default settings: Port 5432, default database
5. Start PostgreSQL service (should auto-start on Windows)

### Option B: If PostgreSQL is already installed
- Ensure it's running. Open Services (press `Win+R`, type `services.msc`)
- Look for "postgresql-x64-XX" and verify it says "Running"
- If not running, right-click → Start

---

## ✅ Step 2: Create the Database & Schema

### Using pgAdmin (Visual GUI)
1. Open pgAdmin from Start menu
2. Right-click "Databases" → Create → Database
3. Name: `attendance_system` → Save
4. Open "Tools" → Query Tool
5. Copy-paste the entire contents of `database/setup_database.sql`
6. Press F5 or click "Execute"

### Using Command Line (PostgreSQL 12+)
```powershell
# Open PowerShell, run:
psql -U postgres -c "CREATE DATABASE attendance_system;"

# Then import schema:
psql -U postgres -d attendance_system -f database/setup_database.sql
```

---

## ✅ Step 3: Update Database Configuration

The app expects database config in `configs/database.yaml`:

**Current config:**
```yaml
postgres:
  host: "localhost"
  port: 5432
  database: "attendance_system"
  user: "postgres"
  password: "password"
```

### ⚠️ IMPORTANT: Update the password!
If your PostgreSQL password is different from `BVL-2026`:

1. Open: `configs/database.yaml`
2. Update the `password:` field with your actual PostgreSQL password
3. Save the file

**Example if your password is "mypassword":**
```yaml
postgres:
  host: "localhost"
  port: 5432
  database: "attendance_system"
  user: "postgres"
  password: "mypassword"
```

---

## ✅ Step 4: Set Up Python Environment

### Option A: Using Python venv (Recommended)
```powershell
# Navigate to project folder
cd "c:\Users\gyank\OneDrive\Desktop\SecureTest app 2\SecureTest Version 3\Intelligent-Face-Recognition-Attendance-System"

# Create virtual environment
python -m venv venv

# Activate it (Windows)
.\venv\Scripts\Activate.ps1

# Upgrade pip
python -m pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

# Important: Install dlib separately (it's in the folder)
pip install vendor/dlib-19.22.99-cp310-cp310-win_amd64.whl
```

### Option B: Using Conda
```powershell
conda create -n classiface python=3.10
conda activate classiface
pip install -r requirements.txt
pip install vendor/dlib-19.22.99-cp310-cp310-win_amd64.whl
```

---

## ✅ Step 5: Verify Everything Works

### Test Database Connection
```powershell
# Make sure venv is activated, then run:
python -c "
from app import pg_conn
try:
    with pg_conn() as conn:
        print('✅ Database connection successful!')
except Exception as e:
    print(f'❌ Error: {e}')
"
```

Expected output: `✅ Database connection successful!`

---

## ✅ Step 6: Run the Flask App

```powershell
# Make sure venv is activated, then:
python app.py
```

Expected output:
```
 * Serving Flask app 'app'
 * Debug mode: on
 * Running on http://127.0.0.1:5000
```

---

## 🌐 Access the App

Open your browser and go to:
- **Home:** http://localhost:5000
- **Register:** http://localhost:5000/register
- **Login:** http://localhost:5000/login
- **Admin Login:** http://127.0.0.1:5000/admin-login
### Default Admin Credentials
To create an admin account, register first then update the role manually in pgAdmin:

1. Register an account normally
2. Open pgAdmin → Query Tool on `attendance_system`
3. Run:
   ```sql
   UPDATE users SET role = 'admin' WHERE email = 'your.email@example.com';
   ```

---

## ⚠️ Troubleshooting

### ❌ "Database connection refused"
- Check PostgreSQL is running (Services → postgresql)
- Verify password in `configs/database.yaml` matches your PostgreSQL password
- Verify database name is `attendance_system`

### ❌ "module not found"
- Make sure you're in the virtual environment
- Run: `pip install -r requirements.txt` again

### ❌ "dlib import error"
- Run: `pip install vendor/dlib-19.22.99-cp310-cp310-win_amd64.whl`
- If still fails, try: `pip install scikit-learn==0.23.1`

### ❌ "Firebase authentication failed"
- Verify `configs/serviceAccountKey.json` exists
- Check Firebase URL in `configs/database.yaml` is correct

### ❌ Port 5000 already in use
```powershell
# Use different port:
python -c "
import os
os.environ['FLASK_PORT'] = '5001'
exec(open('app.py').read())
"
# Then visit: http://localhost:5001
```

---

## 📁 Project Structure
```
Intelligent-Face-Recognition-Attendance-System/
├── app.py                      # Main Flask app
├── requirements.txt            # Python dependencies
├── database/setup_database.sql # Database schema
├── configs/
│   ├── database.yaml          # 🔓 UPDATE PASSWORD HERE
│   └── serviceAccountKey.json # Firebase config
├── services/                   # Face recognition & camera services
├── detection/                  # Face detection logic
├── utils/                      # Helper utilities
├── template/                   # HTML templates
└── static/                     # CSS, images
```

---

## 🎯 Next Steps After Setup

1. **Test Face Recognition:**
   - Go to: http://localhost:5000/register
   - Upload your photo to set up face enrollment

2. **Create a Class (Admin Only):**
   - Login as admin → Admin Dashboard → Create Class

3. **Test Attendance:**
   - Login as student → Take Class → Camera attendance

---

## 📞 Need Help?

If you encounter issues:
1. Check the troubleshooting section above
2. Verify all paths in `configs/database.yaml`
3. Make sure PostgreSQL service is running
4. Check Python version: `python --version` (should be 3.10+)

Good luck! 🎉
