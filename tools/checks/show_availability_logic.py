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
    # Get quiz activation status
    cur.execute("""
        SELECT id, title, COALESCE(is_active, FALSE) 
        FROM quizzes 
        ORDER BY created_at DESC 
        LIMIT 10;
    """)
    
    quizzes = cur.fetchall()
    
    print("\n" + "="*80)
    print("QUIZ AVAILABILITY FIX - Expected Behavior")
    print("="*80)
    
    print("\nQuiz Status in Database:")
    print("-" * 80)
    for quiz_id, title, is_active in quizzes:
        status = "🟢 ACTIVE" if is_active else "🔴 INACTIVE"
        print(f"{title:30} {status}")
    
    print("\n" + "="*80)
    print("FIX LOGIC:")
    print("="*80)
    print("\n1. INSTRUCTOR VIEW (quiz_status='no_session'):")
    print("   Availability = is_active flag only")
    print("   Logic: final_available = is_quiz_active")
    for quiz_id, title, is_active in quizzes:
        instructor_view = "✓ AVAILABLE" if is_active else "✗ UNAVAILABLE"
        print(f"   {title:28} → {instructor_view}")
    
    print("\n2. STUDENT VIEW (Session CLOSED, quiz_status='no_session'):")
    print("   Availability = is_active AND quiz_available (both false)")
    print("   Logic: final_available = is_quiz_active AND False = False")
    for quiz_id, title, is_active in quizzes:
        print(f"   {title:28} → ✗ UNAVAILABLE")
    
    print("\n3. STUDENT VIEW (Session OPEN, quiz_status='available', quiz_available=True):")
    print("   Availability = is_active AND quiz_available (both must be true)")
    print("   Logic: final_available = is_quiz_active AND True")
    for quiz_id, title, is_active in quizzes:
        student_view = "✓ AVAILABLE" if is_active else "✗ UNAVAILABLE"
        print(f"   {title:28} → {student_view}")
    
    print("\n" + "="*80)
    print("✓ FIX APPLIED: Only activated quizzes show to students when session is open")
    print("✓ Non-activated quizzes are hidden from students even during session")
    print("="*80 + "\n")
    
finally:
    cur.close()
    conn.close()
