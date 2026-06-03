#!/usr/bin/env python3
"""Test the new monitor API endpoints"""

import sys
sys.path.insert(0, '.')

from utils.configuration import get_db_config
import psycopg2
from psycopg2 import extras

def test_endpoints():
    """Test if the new endpoints will work"""
    
    # Test if database connection works
    try:
        config = get_db_config()
        conn = psycopg2.connect(**config)
        cur = conn.cursor(cursor_factory=extras.DictCursor)
        
        # Get a sample class and instructor
        cur.execute("""
            SELECT ci.class_id, ci.instructor_id 
            FROM class_instructors 
            LIMIT 1
        """)
        result = cur.fetchone()
        
        if result:
            class_id, instructor_id = result['class_id'], result['instructor_id']
            print(f"✓ Found test class: {class_id}, instructor: {instructor_id}")
            
            # Test the violation query 
            cur.execute("""
                SELECT
                  u.id as user_id,
                  u.full_name as student_name,
                  COUNT(DISTINCT qv.id) as violation_count
                FROM quiz_attempt_violations qv
                LEFT JOIN quiz_attempts qa
                  ON qa.attempt_id::text = qv.attempt_id::text
                LEFT JOIN users u
                  ON u.id = qv.user_id
                LEFT JOIN quizzes q
                  ON q.id::text = qa.quiz_id
                JOIN class_instructors ci
                  ON ci.class_id = q.class_id
                WHERE ci.instructor_id = %s
                  AND q.class_id = %s
                GROUP BY u.id, u.full_name
                ORDER BY violation_count DESC, u.full_name ASC
                LIMIT 10
            """, (str(instructor_id), str(class_id)))
            
            violations = cur.fetchall()
            print(f"✓ Found {len(violations)} students with violations")
            
            for v in violations:
                print(f"  - {v['student_name']}: {v['violation_count']} violations")
        else:
            print("✗ No class_instructors found in database")
        
        cur.close()
        conn.close()
        
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_endpoints()
