#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from app import _build_quiz_cards_for_class, pg_list_quizzes_for_class
from datetime import datetime

# Get first class (should be the test one)
print("=" * 60)
print("DEBUGGING STUDENT VIEW - QUIZ IS_ACTIVE VALUES")
print("=" * 60)

class_id = "a82a99f8-2a3b-4af8-bb52-8de36bec7547"  # BM14 (with Math/Test quizzes)

# Raw query
print("\n1. RAW DATABASE QUERY:")
db_quizzes = pg_list_quizzes_for_class(class_id, limit=10)
for q in db_quizzes:
    print(f"  Quiz: {q.get('title')} | is_active: {q.get('is_active')} (type: {type(q.get('is_active'))})")

# Student mode (with session available)
print("\n2. STUDENT MODE (session available):")
quizzes_student = _build_quiz_cards_for_class(class_id, quiz_available=True, quiz_status="available")
for q in quizzes_student:
    print(f"  Quiz: {q['title']} | is_active: {q.get('is_active')} | is_available: {q.get('is_available')} | status: {q.get('availability_status')}")

# Instructor mode (no session)
print("\n3. INSTRUCTOR MODE (no session):")
quizzes_instructor = _build_quiz_cards_for_class(class_id)
for q in quizzes_instructor:
    print(f"  Quiz: {q['title']} | is_active: {q.get('is_active')} | is_available: {q.get('is_available')} | status: {q.get('availability_status')}")

print("\n" + "=" * 60)
