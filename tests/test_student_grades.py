#!/usr/bin/env python3
"""Test student grades loading"""

import sys
sys.path.insert(0, '.')

from app import pg_list_student_grades, pg_conn
from psycopg2 import extras

def test_student_grades():
    """Test if student grades are loading correctly"""
    
    print("Testing student grades...")
    
    try:
        # Get a student
        with pg_conn() as conn, conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute("""
                SELECT cs.student_id, cs.class_id
                FROM class_students cs
                LIMIT 1
            """)
            result = cur.fetchone()
            
            if not result:
                print("❌ No student found")
                return False
            
            student_id = result['student_id']
            class_id = result['class_id']
            
            print(f"✓ Found student {student_id} in class {class_id}")
            
            # Get grades for this student
            grades = pg_list_student_grades(str(student_id), str(class_id))
            
            print(f"✓ Found {len(grades)} grades")
            
            if grades:
                for g in grades:
                    print(f"  - {g.get('quiz_title', 'Unknown')}: {g.get('score', 0)}/{g.get('total_points', 0)} ({g.get('submitted_at_display', 'N/A')})")
            else:
                print("  No grades recorded yet")
            
            return True
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    test_student_grades()
