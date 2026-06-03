#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from app import pg_list_quizzes_for_class

class_id = "aa712fd7-144e-4a75-9c56-61339cc8b53c"  # BM12

db_quizzes = pg_list_quizzes_for_class(class_id, limit=100)
print(f"Total quizzes in class: {len(db_quizzes)}\n")

for q in db_quizzes:
    print(f"Title: {q.get('title'):<20} | is_active: {q.get('is_active')}")
