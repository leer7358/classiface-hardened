import psycopg2
import yaml

with open('configs/database.yaml', 'r') as f:
    config = yaml.safe_load(f)

pg_conf = config['postgres']
conn = psycopg2.connect(
    host=pg_conf['host'],
    port=pg_conf['port'],
    database=pg_conf['database'],
    user=pg_conf['user'],
    password=pg_conf['password']
)

cur = conn.cursor()

try:
    # Get first quiz and set it as active
    cur.execute("SELECT id, title FROM quizzes LIMIT 1;")
    quiz = cur.fetchone()
    
    if quiz:
        quiz_id, title = quiz
        cur.execute("UPDATE quizzes SET is_active = TRUE WHERE id = %s;", (quiz_id,))
        conn.commit()
        print(f"✓ Activated quiz: {title}")
        
        # Verify
        cur.execute("SELECT id, title, is_active FROM quizzes WHERE id = %s;", (quiz_id,))
        result = cur.fetchone()
        print(f"✓ Verification: {result}")
    else:
        print("✗ No quizzes found")
finally:
    cur.close()
    conn.close()
