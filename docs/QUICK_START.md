# ⚡ Quick Start Checklist

## 1️⃣ PostgreSQL Setup (5 min)
- [ ] PostgreSQL installed and running on port 5432
- [ ] Remember your PostgreSQL password
- [ ] Created database: `attendance_system`

### Quick Test:
```powershell
psql -U postgres -d attendance_system -c "SELECT 1;"
```
Should return: `1`

---

## 2️⃣ Database Schema (2 min)
- [ ] Executed `database/setup_database.sql` in the `attendance_system` database

### Quick Test:
```powershell
psql -U postgres -d attendance_system -c "SELECT * FROM users LIMIT 1;"
```
Should work without error

---

## 3️⃣ Update Config (1 min)
- [ ] Opened `configs/database.yaml`
- [ ] Updated password if different from `BVL-2026`
- [ ] Saved file

---

## 4️⃣ Python Setup (5 min)
- [ ] Created virtual environment: `python -m venv venv`
- [ ] Activated it: `.\venv\Scripts\Activate.ps1`
- [ ] Installed requirements: `pip install -r requirements.txt`
- [ ] Installed dlib: `pip install vendor/dlib-19.22.99-cp310-cp310-win_amd64.whl`

---

## 5️⃣ Run the App (1 min)
```powershell
# Activate venv first:
.\venv\Scripts\Activate.ps1

# Then run:
python app.py
```

Expected: `Running on http://127.0.0.1:5000`

---

## 6️⃣ Access & Test (2 min)
- [ ] Open: http://localhost:5000
- [ ] Click Register
- [ ] Create test account
- [ ] Try login

---

## ✅ DONE! App is running!

**Total Time: ~20 minutes**

### Common Issues

| Issue | Solution |
|-------|----------|
| "Connection refused" | Ensure PostgreSQL is running |
| "Password authentication failed" | Update password in `configs/database.yaml` |
| "No module named X" | Run `pip install -r requirements.txt` |
| "Port 5000 in use" | Close other Flask apps or use different port |

---

## 📖 Need More Details?
See full guide: [SETUP_GUIDE.md](SETUP_GUIDE.md)
