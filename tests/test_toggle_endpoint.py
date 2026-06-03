import psycopg2
import yaml
import json

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
    # Get a quiz
    cur.execute("SELECT id, title, COALESCE(is_active, FALSE) FROM quizzes LIMIT 1;")
    result = cur.fetchone()
    
    if result:
        quiz_id, title, is_active = result
        print(f"\n✓ Test Quiz: {title}")
        print(f"  Current state: {'ACTIVE' if is_active else 'INACTIVE'}")
        
        # Toggle it
        new_state = not is_active
        cur.execute("UPDATE quizzes SET is_active = %s WHERE id = %s;", (new_state, quiz_id))
        conn.commit()
        
        # Verify
        cur.execute("SELECT is_active FROM quizzes WHERE id = %s;", (quiz_id,))
        verify = cur.fetchone()
        print(f"  New state: {'ACTIVE' if verify[0] else 'INACTIVE'}")
        print(f"\n✓ Toggle successful!")
    else:
        print("✗ No quizzes found")
        
finally:
    cur.close()
    conn.close()
