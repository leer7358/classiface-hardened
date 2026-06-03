import psycopg2
from utils.configuration import load_yaml

config = load_yaml('configs/database.yaml')
db_config = config['postgres']

conn = psycopg2.connect(
    host=db_config['host'],
    port=db_config['port'],
    database=db_config['database'],
    user=db_config['user'],
    password=db_config['password']
)

with conn.cursor() as cur:
    cur.execute("""
        SELECT column_name, data_type 
        FROM information_schema.columns 
        WHERE table_name = 'class_sessions'
        ORDER BY ordinal_position
    """)
    columns = cur.fetchall()
    print('📋 class_sessions columns:')
    if columns:
        for col in columns:
            print(f'   - {col[0]}: {col[1]}')
    else:
        print('   (table not found)')

conn.close()
