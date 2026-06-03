#!/usr/bin/env python3
import psycopg2
from psycopg2.extras import RealDictCursor
import yaml
import sys

# Load database config
with open('configs/database.yaml', 'r') as f:
    db_config = yaml.safe_load(f)['postgres']

# Connect to database
conn = psycopg2.connect(
    host=db_config['host'],
    port=db_config.get('port', 5432),
    database=db_config['database'],
    user=db_config['user'],
    password=db_config['password']
)

cur = conn.cursor(cursor_factory=RealDictCursor)

print("=" * 80)
print("INSTRUCTOR TO CLASS MAPPING")
print("=" * 80)

# Get instructors with their classes
cur.execute("""
    SELECT u.id, u.first_name, u.last_name, u.email,
           json_agg(c.id) as class_ids,
           json_agg(c.section_name) as class_names
    FROM users u
    LEFT JOIN class_instructors ci ON u.id = ci.instructor_id
    LEFT JOIN classes c ON ci.class_id = c.id
    WHERE u.role = 'instructor'
    GROUP BY u.id, u.first_name, u.last_name, u.email
""")

instructors = cur.fetchall()
for instr in instructors:
    print(f"\nInstructor: {instr['first_name']} {instr['last_name']} (ID: {instr['id']})")
    print(f"Email: {instr['email']}")
    if instr['class_ids'][0] is not None:
        for i, cid in enumerate(instr['class_ids']):
            print(f"  - Class: {instr['class_names'][i]} (ID: {cid})")
    else:
        print("  - No classes assigned")

print("\n" + "=" * 80)
print("QUIZZES IN DATABASE")
print("=" * 80)

cur.execute("""
    SELECT q.id, q.class_id, q.title, q.created_by, u.first_name, u.last_name, c.section_name
    FROM quizzes q
    LEFT JOIN users u ON q.created_by = u.id
    LEFT JOIN classes c ON q.class_id = c.id
    ORDER BY c.section_name, q.title
""")

quizzes = cur.fetchall()
if quizzes:
    for quiz in quizzes:
        print(f"\nQuiz: {quiz['title']} (ID: {quiz['id']})")
        print(f"  Class: {quiz['section_name']} (ID: {quiz['class_id']})")
        print(f"  Created by: {quiz['first_name']} {quiz['last_name']} (ID: {quiz['created_by']})")
else:
    print("No quizzes found in database!")

print("\n" + "=" * 80)
print("QUIZ COUNTS BY INSTRUCTOR & CLASS")
print("=" * 80)

cur.execute("""
    SELECT 
        u.id as instructor_id,
        u.first_name,
        u.last_name,
        c.id as class_id,
        c.section_name,
        COUNT(q.id) as quiz_count
    FROM users u
    INNER JOIN class_instructors ci ON u.id = ci.instructor_id
    INNER JOIN classes c ON ci.class_id = c.id
    LEFT JOIN quizzes q ON q.class_id = c.id AND q.created_by = u.id
    WHERE u.role = 'instructor'
    GROUP BY u.id, u.first_name, u.last_name, c.id, c.section_name
    ORDER BY u.first_name, c.section_name
""")

results = cur.fetchall()
for row in results:
    print(f"\n{row['first_name']} {row['last_name']} → {row['section_name']}: {row['quiz_count']} quiz(zes)")

conn.close()
print("\n" + "=" * 80)
