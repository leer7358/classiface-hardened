#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from app import pg_conn, pg_list_quizzes_for_class

# Find BM14
with pg_conn() as conn, conn.cursor() as cur:
    cur.execute("SELECT id FROM classes WHERE section_name = 'BM14' LIMIT 1;")
    row = cur.fetchone()
    if row:
        bm14_id = str(row['id'])
        print(f"BM14 Class ID: {bm14_id}\n")
        
        # Get quizzes
        from app import _build_quiz_cards_for_class
        
        print("RAW DATABASE:")
        db_quizzes = pg_list_quizzes_for_class(bm14_id, limit=100)
        for q in db_quizzes:
            print(f"  {q.get('title'):<20} | is_active: {q.get('is_active')}")
        
        print("\nSTUDENT VIEW (session available):")
        student_quizzes = _build_quiz_cards_for_class(bm14_id, quiz_available=True, quiz_status="available")
        for q in student_quizzes:
            print(f"  {q['title']:<20} | is_active: {q.get('is_active')}")
