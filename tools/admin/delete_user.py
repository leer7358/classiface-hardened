import psycopg2
import os

pg_password = os.environ.get("PGPASSWORD")
if not pg_password:
    raise RuntimeError("Set PGPASSWORD before running this admin tool.")

conn = psycopg2.connect(
    host=os.environ.get("PGHOST", "localhost"),
    database=os.environ.get("PGDATABASE", "attendance_system"),
    user=os.environ.get("PGUSER", "postgres"),
    password=pg_password
)
cur = conn.cursor()

# Find the test@example.com user
cur.execute("SELECT id FROM users WHERE email='test@example.com'")
row = cur.fetchone()

if row:
    user_id = row[0]
    print(f'Found user ID: {user_id}')
    
    cur.execute("DELETE FROM class_students WHERE student_id=%s", (user_id,))
    cur.execute("DELETE FROM class_instructors WHERE instructor_id=%s", (user_id,))
    cur.execute("DELETE FROM users WHERE id=%s", (user_id,))
    
    conn.commit()
    print(f'Deleted user successfully')
else:
    print('User not found')

cur.close()
conn.close()
