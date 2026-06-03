#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from app import pg_conn, pg_list_quizzes_for_class

with pg_conn() as conn, conn.cursor() as cur:
    cur.execute("SELECT id, section_name FROM classes ORDER BY section_name;")
    classes = cur.fetchall()
    
    print("CLASSES AND THEIR QUIZZES:\n")
    for c in classes:
        class_id = c['id']
        section = c['section_name']
        quizzes = pg_list_quizzes_for_class(str(class_id), limit=100)
        
        print(f"\n📚 {section}:")
        if quizzes:
            for q in quizzes:
                active_status = "✓ ACTIVE" if q.get('is_active') else "✗ INACTIVE"
                print(f"   - {q.get('title'):<20} ({active_status})")
        else:
            print("   (no quizzes)")
