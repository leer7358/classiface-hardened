#!/usr/bin/env python3
"""
Migration script to add start_date and end_date columns to class_sessions table
"""
import sys
import os

# Suppress Flask warnings
os.environ['FLASK_ENV'] = 'production'

try:
    import psycopg2
    from psycopg2 import sql

    pg_password = os.environ.get("PGPASSWORD")
    if not pg_password:
        raise RuntimeError("Set PGPASSWORD before running this migration.")
    
    # Direct database connection (without Flask)
    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        database=os.environ.get("PGDATABASE", "setupdatabase"),
        user=os.environ.get("PGUSER", "postgres"),
        password=pg_password
    )
    
    with conn.cursor() as cur:
        print("Adding start_date column if it doesn't exist...")
        cur.execute('''
            ALTER TABLE public.class_sessions
            ADD COLUMN IF NOT EXISTS start_date date;
        ''')
        
        print("Adding end_date column if it doesn't exist...")
        cur.execute('''
            ALTER TABLE public.class_sessions
            ADD COLUMN IF NOT EXISTS end_date date;
        ''')
        
        conn.commit()
        
        # Verify columns were added
        cur.execute('''
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name='class_sessions' 
            ORDER BY ordinal_position;
        ''')
        
        print("\n✅ Columns added successfully!")
        print("\nClass Sessions Table Structure:")
        print("-" * 50)
        for column_name, data_type in cur.fetchall():
            print(f"  {column_name:20} {data_type}")
        
    conn.close()
    print("\n✅ Migration completed!")
    sys.exit(0)
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
