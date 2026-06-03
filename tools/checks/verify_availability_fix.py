import sys
sys.path.insert(0, '.')

from app import _build_quiz_cards_for_class
from datetime import datetime, time
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
    # Get a class with quizzes
    cur.execute("""
        SELECT DISTINCT class_id FROM quizzes LIMIT 1;
    """)
    result = cur.fetchone()
    
    if result:
        class_id = result[0]
        
        print("\n" + "="*80)
        print("QUIZ AVAILABILITY FIX VERIFICATION")
        print("="*80)
        
        # Test 1: Instructor view (quiz_available=False, quiz_status="no_session")
        print("\n1. INSTRUCTOR VIEW (No session parameter):")
        print("-" * 80)
        quizzes_instructor = _build_quiz_cards_for_class(str(class_id), quiz_available=False, quiz_status="no_session")
        
        for q in quizzes_instructor:
            status = "✓ AVAILABLE" if q['is_available'] else "✗ UNAVAILABLE"
            active = "🟢 ACTIVE" if q['is_active'] else "🔴 INACTIVE"
            print(f"{q['title']:25} {active}  →  {status}")
        
        # Test 2: Student view - session NOT open (quiz_available=False)
        print("\n2. STUDENT VIEW (Session window CLOSED):")
        print("-" * 80)
        quizzes_student_closed = _build_quiz_cards_for_class(str(class_id), quiz_available=False, quiz_status="no_session")
        
        for q in quizzes_student_closed:
            status = "✓ AVAILABLE" if q['is_available'] else "✗ UNAVAILABLE"
            active = "🟢 ACTIVE" if q['is_active'] else "🔴 INACTIVE"
            print(f"{q['title']:25} {active}  →  {status}  (session closed)")
        
        # Test 3: Student view - session IS open (quiz_available=True)
        print("\n3. STUDENT VIEW (Session window OPEN):")
        print("-" * 80)
        quizzes_student_open = _build_quiz_cards_for_class(str(class_id), quiz_available=True, quiz_status="available")
        
        for q in quizzes_student_open:
            status = "✓ AVAILABLE" if q['is_available'] else "✗ UNAVAILABLE"
            active = "🟢 ACTIVE" if q['is_active'] else "🔴 INACTIVE"
            print(f"{q['title']:25} {active}  →  {status}  (session open)")
        
        print("\n" + "="*80)
        print("EXPECTED BEHAVIOR:")
        print("="*80)
        print("✓ Instructor view: Shows all quizzes, status based on is_active flag")
        print("✓ Student view (session closed): Shows all quizzes, all UNAVAILABLE") 
        print("✓ Student view (session open): Only ACTIVE quizzes show as AVAILABLE")
        print("="*80 + "\n")
    else:
        print("✗ No quizzes found")
        
finally:
    cur.close()
    conn.close()
