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

# Check all tables
cursor.execute("""
    SELECT table_name FROM information_schema.tables 
    WHERE table_schema = 'public' ORDER BY table_name
""")
tables = cursor.fetchall()
print('📊 Database Tables:')
for (table,) in tables:
    cursor.execute(f'SELECT COUNT(*) FROM "{table}"')
    count = cursor.fetchone()[0]
    print(f'   {table}: {count} rows')

print('\n📋 Users table details:')
cursor.execute('SELECT * FROM users LIMIT 5')
columns = [desc[0] for desc in cursor.description]
rows = cursor.fetchall()
print(f'   Columns: {columns}')
print(f'   Total rows: {len(rows)}')

cursor.close()
conn.close()
