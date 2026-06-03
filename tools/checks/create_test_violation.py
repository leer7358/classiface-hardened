#!/usr/bin/env python3
"""Create a test violation to verify the monitor page works"""

import sys
import uuid
sys.path.insert(0, '.')

from app import pg_conn
from psycopg2 import extras

def create_test_violation():
    """Create a test violation for debugging"""
    
    print("=" * 60)
    print("CREATING TEST VIOLATION")
    print("=" * 60)
    
    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=extras.DictCursor) as cur:
            
            # Get a student and quiz from a class
            print("\n1. Finding test data...")
            cur.execute("""
                SELECT 
                  cs.student_id,
                  c.id as class_id,
                  q.id as quiz_id,
                  u.full_name,
                  u.id as user_id
                FROM class_students cs
                JOIN classes c ON c.id = cs.class_id
                JOIN quizzes q ON q.class_id = c.id
                JOIN users u ON u.id = cs.student_id
                LIMIT 1
            """)
            
            result = cur.fetchone()
            if not result:
                print("   ⚠️ No student/quiz combination found")
                return False
            
            student_id = result['student_id']
            class_id = result['class_id']
            quiz_id = result['quiz_id']
            student_name = result['full_name']
            user_id = result['user_id']
            
            print(f"   ✓ Student: {student_name} (ID: {user_id})")
            print(f"   ✓ Class: {class_id}")
            print(f"   ✓ Quiz: {quiz_id}")
            
            # Get or create a quiz attempt
            print("\n2. Getting quiz attempt...")
            cur.execute("""
                SELECT attempt_id FROM quiz_attempts
                WHERE user_id = %s AND quiz_id = %s
                LIMIT 1
            """, (str(user_id), str(quiz_id)))
            
            attempt_result = cur.fetchone()
            if attempt_result:
                attempt_id = attempt_result['attempt_id']
                print(f"   ✓ Found existing attempt: {attempt_id}")
            else:
                print("   ⚠️ No quiz attempts found for this student/quiz")
                print("   Creating a test attempt...")
                attempt_id = str(uuid.uuid4())
                
                cur.execute("""
                    INSERT INTO quiz_attempts 
                      (attempt_id, user_id, quiz_id, quiz_title, started_at)
                    VALUES (%s, %s, %s, %s, NOW())
                """, (attempt_id, str(user_id), str(quiz_id), "Test Quiz"))
                conn.commit()
                print(f"   ✓ Created attempt: {attempt_id}")
            
            # Create a test violation
            print("\n3. Creating test violation...")
            violation_id = str(uuid.uuid4())
            
            cur.execute("""
                INSERT INTO quiz_attempt_violations
                  (id, attempt_id, user_id, violation_type, timestamp_iso, time_remaining)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (violation_id, str(attempt_id), str(user_id), 'tab_left', '2026-03-29T13:30:00Z', 600))
            conn.commit()
            print(f"   ✓ Created violation: {violation_id}")
            
            # Verify it was created
            print("\n4. Verifying violation...")
            cur.execute("""
                SELECT COUNT(*) as count FROM quiz_attempt_violations
                WHERE attempt_id = %s
            """, (str(attempt_id),))
            
            result = cur.fetchone()
            count = result['count'] if result else 0
            print(f"   ✓ Violations for this attempt: {count}")
            
            # Test the query
            print("\n5. Testing monitor query...")
            cur.execute("""
                SELECT ci.instructor_id, ci.class_id
                FROM class_instructors ci
                WHERE ci.class_id = %s
                LIMIT 1
            """, (str(class_id),))
            
            instructor_result = cur.fetchone()
            if instructor_result:
                instructor_id = instructor_result['instructor_id']
                
                cur.execute("""
                    SELECT
                      u.id as user_id,
                      u.full_name as student_name,
                      COUNT(DISTINCT qv.id) as violation_count
                    FROM quiz_attempt_violations qv
                    LEFT JOIN quiz_attempts qa
                      ON qa.attempt_id = qv.attempt_id
                    LEFT JOIN users u
                      ON u.id = qv.user_id
                    LEFT JOIN quizzes q
                      ON q.id = qa.quiz_id
                    JOIN class_instructors ci
                      ON ci.class_id = q.class_id
                    WHERE ci.instructor_id = %s
                      AND q.class_id = %s
                    GROUP BY u.id, u.full_name
                """, (str(instructor_id), str(class_id)))
                
                students = cur.fetchall()
                print(f"   ✓ Found {len(students)} students with violations:")
                for student in students:
                    print(f"      - {student['student_name']}: {student['violation_count']} violations")
            
            print("\n✓ Test violation created successfully!")
            print("✓ Monitor API should now return this violation")
            return True
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    create_test_violation()
