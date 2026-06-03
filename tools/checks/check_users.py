#!/usr/bin/env python3
import psycopg2
from utils.configuration import load_yaml

config = load_yaml('configs/database.yaml')
pg = config.get('postgres', {})

conn = psycopg2.connect(
    host=pg.get('host', 'localhost'),
    port=pg.get('port', 5432),
    dbname=pg.get('database'),
    user=pg.get('user'),
    password=pg.get('password')
)
cursor = conn.cursor()

print("👥 Current Users in Database:")
cursor.execute('SELECT id, email, role, first_name, last_name FROM users ORDER BY created_at DESC')
rows = cursor.fetchall()

if not rows:
    print("   (No users found)")
else:
    for row in rows:
        user_id, email, role, first_name, last_name = row
        print(f"   ✓ {first_name} {last_name}")
        print(f"     Email: {email}")
        print(f"     Role: {role}")
        print()

cursor.close()
conn.close()
