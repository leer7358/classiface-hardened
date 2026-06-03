#!/usr/bin/env python3
import psycopg2
from utils.configuration import load_yaml

config = load_yaml('configs/database.yaml')
pg = config.get('postgres', {})

host = pg.get('host', 'localhost')
port = pg.get('port', 5432)
dbname = pg.get('database')
user = pg.get('user')
password = pg.get('password')

print("🔗 Database Connection Info:")
print(f"   Host: {host}")
print(f"   Port: {port}")
print(f"   Database: {dbname}")
print(f"   User: {user}")

try:
    conn = psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        connect_timeout=5
    )
    cursor = conn.cursor()
    
    # Check current database
    cursor.execute('SELECT current_database()')
    current_db = cursor.fetchone()[0]
    print(f"   Connected to: {current_db}")
    
    # Check user count
    cursor.execute('SELECT COUNT(*) FROM users')
    count = cursor.fetchone()[0]
    print(f"\n👥 Users in {current_db}: {count}")
    
    cursor.close()
    conn.close()
    
except Exception as e:
    print(f"❌ Connection error: {e}")
