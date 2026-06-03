import psycopg2
from utils.configuration import load_yaml

config = load_yaml('configs/database.yaml')
pg_config = config['postgres']

try:
    conn = psycopg2.connect(
        host=pg_config['host'],
        port=pg_config['port'],
        database=pg_config['database'],
        user=pg_config['user'],
        password=pg_config['password']
    )
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role = 'admin' WHERE email = %s", ('admin@classiface.com',))
    conn.commit()
    
    cursor.execute("SELECT first_name, last_name, email, role FROM users WHERE email = %s", ('admin@classiface.com',))
    result = cursor.fetchone()
    
    if result:
        print(f'✓ User updated successfully!')
        print(f'  Name: {result[0]} {result[1]}')
        print(f'  Email: {result[2]}')
        print(f'  Role: {result[3]}')
    else:
        print('✗ User not found')
    
    cursor.close()
    conn.close()
except Exception as e:
    print(f'✗ Error: {e}')
