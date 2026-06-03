import sys
sys.path.insert(0, '.')

# Simple direct database query to verify Quiz 7 settings
import os
os.environ['DATABASE_URL'] = os.environ.get('DATABASE_URL', 'postgresql://localhost/classiface')

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

try:
    pool = SimpleConnectionPool(1, 5, os.environ['DATABASE_URL'])
    with pool.getconn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id, title, attempts_type, attempts_limit FROM quizzes ORDER BY id;")
            quizzes = cur.fetchall()
            print("All Quizzes in Database:")
            for q in quizzes:
                print(f"  {q['id']}: {q['title']} - attempts_type={q['attempts_type']}, attempts_limit={q['attempts_limit']}")
    pool.closeall()
except Exception as e:
    print(f"Error: {e}")
