import psycopg2
import yaml
import os

with open('configs/database.yaml', 'r') as f:
    config = yaml.safe_load(f)

pg_conf = config['postgres']
conn = psycopg2.connect(
    host=pg_conf['host'],
    port=pg_conf['port'],
    database=pg_conf['database'],
    user=pg_conf['user'],
    password=pg_conf['password']
)
cur = conn.cursor()

try:
    # Add is_active field if it doesn't exist
    cur.execute("""
        ALTER TABLE quizzes ADD COLUMN is_active BOOLEAN DEFAULT FALSE;
    """)
    conn.commit()
    print('✓ Added is_active field to quizzes table')
except psycopg2.errors.DuplicateColumn:
    print('✓ is_active field already exists')
    conn.rollback()
except Exception as e:
    print(f'✗ Error: {e}')
    conn.rollback()
finally:
    cur.close()
    conn.close()
