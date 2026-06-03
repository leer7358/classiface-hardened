#!/usr/bin/env python3
"""Debug the monitor API endpoints"""

import sys
import json
sys.path.insert(0, '.')

# Test direct database connection
from app import pg_conn
from psycopg2 import extras

def test_violations_data():
    """Check what violations exist in the database"""
    
    print("=" * 60)
    print("TESTING MONITOR API ENDPOINTS")
    print("=" * 60)
    
    try:
        with pg_conn() as conn, conn.cursor(cursor_factory=extras.DictCursor) as cur:
            # First, check if any violations exist
            print("\n1. Checking if violations exist in database...")
            cur.execute("SELECT COUNT(*) as count FROM quiz_attempt_violations")
            result = cur.fetchone()
            violation_count = result['count'] if result else 0
            print(f"   Total violations in database: {violation_count}")
            
            if violation_count == 0:
                print("   ⚠️ NO VIOLATIONS FOUND - Need to trigger a violation first!")
                return False
            
            # Show sample violations
            print("\n2. Sample violations:")
            cur.execute("""
                SELECT id, attempt_id, user_id, violation_type, timestamp_iso
                FROM quiz_attempt_violations
                LIMIT 3
            """)
            for row in cur.fetchall():
                print(f"   ID: {row['id']}, Attempt: {row['attempt_id']}, User: {row['user_id']}, Type: {row['violation_type']}, Time: {row['timestamp_iso']}")
            
            # Check quiz_attempts join
            print("\n3. Checking quiz_attempts join...")
            cur.execute("""
                SELECT COUNT(*) as count
                FROM quiz_attempt_violations qv
                LEFT JOIN quiz_attempts qa ON qa.attempt_id::text = qv.attempt_id::text
                WHERE qa.attempt_id IS NOT NULL
            """)
            result = cur.fetchone()
            matched_count = result['count'] if result else 0
            print(f"   Violations that matched to quiz_attempts: {matched_count}/{violation_count}")
            
            # Check users join
            print("\n4. Checking users join...")
            cur.execute("""
                SELECT COUNT(*) as count
                FROM quiz_attempt_violations qv
                LEFT JOIN quiz_attempts qa ON qa.attempt_id::text = qv.attempt_id::text
                LEFT JOIN users u ON u.id = qv.user_id
                WHERE u.id IS NOT NULL
            """)
            result = cur.fetchone()
            user_count = result['count'] if result else 0
            print(f"   Violations with user names found: {user_count}/{violation_count}")
            
            # Check quizzes join
            print("\n5. Checking quizzes join...")
            cur.execute("""
                SELECT COUNT(*) as count
                FROM quiz_attempt_violations qv
                LEFT JOIN quiz_attempts qa ON qa.attempt_id::text = qv.attempt_id::text
                LEFT JOIN quizzes q ON q.id::text = qa.quiz_id
                WHERE q.id IS NOT NULL
            """)
            result = cur.fetchone()
            quiz_count = result['count'] if result else 0
            print(f"   Violations with quiz info found: {quiz_count}/{violation_count}")
            
            # Get an instructor to test with
            print("\n6. Finding a test instructor...")
            cur.execute("""
                SELECT ci.instructor_id, ci.class_id, u.full_name
                FROM class_instructors ci
                LEFT JOIN users u ON u.id = ci.instructor_id
                LIMIT 1
            """)
            result = cur.fetchone()
            if result:
                instructor_id, class_id, instructor_name = result['instructor_id'], result['class_id'], result['full_name']
                print(f"   Found: Instructor {instructor_name} (ID: {instructor_id}) in Class: {class_id}")
                
                # Test the actual endpoint query
                print("\n7. Testing the /api/instructor/students-violations query...")
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
                """, (str(instructor_id), str(class_id)))
                
                students = cur.fetchall()
                print(f"   Students with violations: {len(students)}")
                for row in students:
                    student_name = row['student_name']
                    count = row['violation_count']
                    print(f"   - {student_name}: {count} violations")
                
                if not students:
                    print("   ⚠️ Query returned no students!")
                    print("\n   Debugging: Checking if there are any violations for this class...")
                    cur.execute("""
                        SELECT COUNT(DISTINCT qv.id) as count
                        FROM quiz_attempt_violations qv
                        LEFT JOIN quiz_attempts qa ON qa.attempt_id::text = qv.attempt_id::text
                        LEFT JOIN quizzes q ON q.id::text = qa.quiz_id
                        JOIN class_instructors ci ON ci.class_id = q.class_id
                        WHERE ci.instructor_id = %s AND q.class_id = %s
                    """, (str(instructor_id), str(class_id)))
                    result = cur.fetchone()
                    count = result['count'] if result else 0
                    print(f"   Violations for this class: {count}")
            else:
                print("   ⚠️ No instructors found!")
            
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True

if __name__ == "__main__":
    success = test_violations_data()
    if not success:
        print("\n⚠️ Testing failed - see errors above")
    else:
        print("\n✓ Database debug complete")
