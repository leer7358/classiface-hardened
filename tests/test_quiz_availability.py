import sys
sys.path.insert(0, '.')

from app import _build_quiz_cards_for_class
import uuid

# Test with a sample class ID - get any class from DB to test
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

# Get a class with quizzes
cur.execute("""
    SELECT DISTINCT class_id FROM quizzes LIMIT 1;
""")
result = cur.fetchone()

if result:
    class_id = result[0]
    quizzes = _build_quiz_cards_for_class(str(class_id))
    
    print(f"\nQuizzes for class {class_id}:")
    print("-" * 80)
    for q in quizzes:
        print(f"Title: {q['title']}")
        print(f"  is_active: {q['is_active']}")
        print(f"  is_available: {q['is_available']}")
        print(f"  availability_status: {q['availability_status']}")
        print()
else:
    print("✗ No quizzes found in database")

cur.close()
conn.close()
