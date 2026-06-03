import sys
sys.path.insert(0, '.')
from app import pg_conn

try:
    with pg_conn() as conn, conn.cursor() as cur:
        # Find all quizzes
        cur.execute("SELECT id, title, class_id FROM quizzes ORDER BY id;")
        quizzes = cur.fetchall()
        if quizzes:
            print("Quizzes found:")
            for q in quizzes:
                print(f"  Quiz {q['id']}: {q['title']} (Class: {q['class_id']})")
        else:
            print("No quizzes found in database")
        
        # Find all classes
        cur.execute("SELECT id, class_code, section_name FROM classes ORDER BY id;")
        classes = cur.fetchall()
        print("\nClasses found:")
        for c in classes:
            print(f"  Class {c['id']}: {c['class_code']} - {c['section_name']}")
except Exception as e:
    print(f"Error: {e}")
