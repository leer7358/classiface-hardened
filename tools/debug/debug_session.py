#!/usr/bin/env python3
import sys
sys.path.insert(0, '.')

from app import pg_get_today_session, compute_quiz_availability
from datetime import datetime

class_id = "30acd460-60c2-4cff-a867-bd1a508df646"  # BM14

sess = pg_get_today_session(class_id)
print(f"Session for BM14: {sess}\n")

if sess:
    quiz_available, quiz_status = compute_quiz_availability(datetime.now(), sess)
    print(f"COMPUTED VALUES:")
    print(f"  quiz_available: {quiz_available}")
    print(f"  quiz_status: {quiz_status}")
else:
    print("NO SESSION SET for this class")
