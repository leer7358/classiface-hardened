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
    # Check quiz data in database
    cur.execute("""
        SELECT id, title, COALESCE(is_active, FALSE) as is_active 
        FROM quizzes 
        ORDER BY created_at DESC 
        LIMIT 10;
    """)
    
    quizzes = cur.fetchall()
    
    print("\n" + "="*80)
    print("DATABASE QUIZ STATUS")
    print("="*80)
    
    for quiz_id, title, is_active in quizzes:
        status = "✓ ACTIVE" if is_active else "✗ INACTIVE"
        print(f"{title:30} {status}")
    
    print("\n" + "="*80)
    print("EXPECTED BEHAVIOR AFTER FIX")
    print("="*80)
    
    print("\nINSTRUCTOR CLASS HOME PAGE:")
    print("(quiz_status='no_session', quiz_available=False)")
    for quiz_id, title, is_active in quizzes:
        status = "Available" if is_active else "Unavailable"
        print(f"  {title:28} → {status}")
    
    print("\nSTUDENT CLASS HOME PAGE (Session OPEN):")
    print("(quiz_status='available', quiz_available=True)")
    for quiz_id, title, is_active in quizzes:
        status = "Available" if is_active else "Unavailable"
        print(f"  {title:28} → {status}")
    
    print("\nSTUDENT CLASS HOME PAGE (Session CLOSED):")
    print("(quiz_status='no_session', quiz_available=False)")
    for quiz_id, title, is_active in quizzes:
        print(f"  {title:28} → Unavailable")
    
    print("\n" + "="*80)
    print("ACTIVATION TOGGLE TEST")
    print("="*80)
    
    # Try toggling one quiz
    if quizzes:
        quiz_id, title, is_active = quizzes[0]
        new_state = not is_active
        print(f"\nTesting toggle on: {title}")
        print(f"  Current: {'ACTIVE' if is_active else 'INACTIVE'}")
        
        cur.execute(
            "UPDATE quizzes SET is_active = %s WHERE id = %s;",
            (new_state, quiz_id)
        )
        conn.commit()
        
        # Verify
        cur.execute("SELECT is_active FROM quizzes WHERE id = %s;", (quiz_id,))
        result = cur.fetchone()
        print(f"  After toggle: {'ACTIVE' if result[0] else 'INACTIVE'}")
        
        # Toggle back
        cur.execute(
            "UPDATE quizzes SET is_active = %s WHERE id = %s;",
            (is_active, quiz_id)
        )
        conn.commit()
        print(f"  Toggled back to original state")
    
    print("\n" + "="*80 + "\n")
    
finally:
    cur.close()
    conn.close()
