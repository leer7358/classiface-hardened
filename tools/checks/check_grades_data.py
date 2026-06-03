#!/usr/bin/env python3
"""Check database for student grades data"""

import sys
sys.path.insert(0, '.')

from app import pg_conn
from psycopg2 import extras

with pg_conn() as conn, conn.cursor(cursor_factory=extras.DictCursor) as cur:
    # Check if there are any class_students
    cur.execute('SELECT COUNT(*) as count FROM class_students')
    result = cur.fetchone()
    count = result['count']
    print(f'class_students records: {count}')
    
    # Check if there are quiz_attempts with scores
    cur.execute('''
        SELECT COUNT(*) as count FROM quiz_attempts
        WHERE score IS NOT NULL AND submitted_at IS NOT NULL
    ''')
    result = cur.fetchone()
    count = result['count']
    print(f'Quiz attempts with scores: {count}')
    
    # Show a sample
    cur.execute('''
        SELECT qa.user_id, qa.quiz_id, qa.score, qa.total_points, qa.submitted_at
        FROM quiz_attempts qa
        WHERE qa.submitted_at IS NOT NULL
        LIMIT 5
    ''')
    
    print('\nSample quiz attempts:')
    for row in cur.fetchall():
        print(f"  User: {row['user_id']}, Quiz: {row['quiz_id']}, Score: {row['score']}/{row['total_points']}, Submitted: {row['submitted_at']}")
