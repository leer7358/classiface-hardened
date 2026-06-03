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
    # Query quizzes with their active status
    cur.execute("""
        SELECT id, title, COALESCE(is_active, FALSE), class_id 
        FROM quizzes 
        ORDER BY created_at DESC 
        LIMIT 5;
    """)
    
    quizzes = cur.fetchall()
    
    print("\nQuiz Activation Status:")
    print("-" * 80)
    for quiz in quizzes:
        quiz_id, title, is_active, class_id = quiz
        status = "🟢 ACTIVE" if is_active else "⚫ INACTIVE"
        print(f"{title:30} {status}")
    
finally:
    cur.close()
    conn.close()
