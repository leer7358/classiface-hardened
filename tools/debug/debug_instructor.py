import psycopg2
from psycopg2.extras import RealDictCursor
from utils.configuration import load_yaml
import os

# Load config
config = load_yaml("configs/database.yaml")
pg = config.get("postgres", {})

dbname = os.environ.get("PGDATABASE", pg.get("database"))
user = os.environ.get("PGUSER", pg.get("user"))
password = os.environ.get("PGPASSWORD", pg.get("password"))
host = os.environ.get("PGHOST", pg.get("host", "localhost"))
port = os.environ.get("PGPORT", pg.get("port", 5432))

conn = psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port)
cur = conn.cursor(cursor_factory=RealDictCursor)

# Get the instructor
cur.execute("SELECT id, first_name, last_name FROM users WHERE role='instructor' LIMIT 5")
instructors = cur.fetchall()
print(f"All instructors: {instructors}")

for instr in instructors:
    instructor_id = instr['id']
    print(f"\nChecking instructor: {instr['first_name']} {instr['last_name']} (ID: {instructor_id})")
    
    # Check class assignments count
    cur.execute("SELECT COUNT(DISTINCT class_id) as count FROM class_instructors WHERE instructor_id = %s", (instructor_id,))
    count_result = cur.fetchone()
    print(f"Class count from subquery: {count_result['count']}")
    
    # Check actual assignments
    cur.execute("SELECT * FROM class_instructors WHERE instructor_id = %s", (instructor_id,))
    assignments = cur.fetchall()
    print(f"Actual assignments: {assignments}")

cur.close()
conn.close()
