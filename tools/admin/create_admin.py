#!/usr/bin/env python3
"""
Create admin accounts for ClassiFace.

Usage:
    python create_admin.py --default           # Create default admin
    python create_admin.py [first] [last] [email]   # Create custom admin

Example:
    python create_admin.py --default
    python create_admin.py John Doe admin@school.edu
"""

import os
import sys
import psycopg2
import uuid
from utils.configuration import load_yaml

def create_admin(first_name="Admin", last_name="User", email="admin@classiface.com"):
    """Create an admin account in PostgreSQL."""
    
    # Load database config
    config = load_yaml("configs/database.yaml")
    pg = config.get("postgres", {})
    
    # Get DB credentials from env or config
    host = os.environ.get("PGHOST", pg.get("host", "localhost"))
    port = int(os.environ.get("PGPORT", pg.get("port", 5432)))
    dbname = os.environ.get("PGDATABASE", pg.get("database"))
    user = os.environ.get("PGUSER", pg.get("user"))
    password = os.environ.get("PGPASSWORD", pg.get("password"))
    
    if not dbname or not user or not password:
        print("❌ Error: PostgreSQL config missing!")
        print("Set configs/database.yaml postgres section or env vars: PGDATABASE/PGUSER/PGPASSWORD")
        sys.exit(1)
    
    # Validate inputs
    first_name = first_name.strip()
    last_name = last_name.strip()
    email = email.strip().lower()
    
    if not first_name or not last_name:
        print("❌ First and last names are required")
        sys.exit(1)
    
    if not email or "@" not in email:
        print("❌ Valid email is required")
        sys.exit(1)
    
    try:
        # Connect to database
        conn = psycopg2.connect(
            host=host,
            port=port,
            dbname=dbname,
            user=user,
            password=password,
        )
        cursor = conn.cursor()
        
        # Check if user already exists
        cursor.execute("SELECT id FROM users WHERE email = %s", (email,))
        if cursor.fetchone():
            print(f"❌ Admin with email '{email}' already exists")
            cursor.close()
            conn.close()
            sys.exit(1)
        
        # Create admin user (Firebase UID will be set on first login)
        user_id = str(uuid.uuid4())
        full_name = f"{first_name} {last_name}"
        
        cursor.execute(
            """
            INSERT INTO users (id, firebase_uid, first_name, last_name, full_name, email, role)
            VALUES (%s::uuid, %s, %s, %s, %s, %s, %s)
            """,
            (user_id, None, first_name, last_name, full_name, email, "admin")
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        print("\n" + "="*70)
        print("✅ ADMIN ACCOUNT CREATED SUCCESSFULLY!")
        print("="*70)
        print(f"Name:        {full_name}")
        print(f"Email:       {email}")
        print(f"Role:        admin")
        print(f"User ID:     {user_id}")
        print("="*70)
        
        print("\n📝 NEXT STEPS:")
        print("1. Create a matching user in Firebase:")
        print("   - Go to: https://console.firebase.google.com")
        print("   - Project: face-attendance-system-c3043")
        print("   - Authentication → Users → Create user")
        print(f"   - Email: {email}")
        print("   - Set a secure password")
        print("\n2. Log in to the admin portal:")
        print(f"   - URL: http://localhost:5000/admin-login")
        print(f"   - Email: {email}")
        print("   - Password: (the one you just created in Firebase)")
        print("="*70 + "\n")
        
    except psycopg2.IntegrityError as e:
        print(f"❌ Database error: {e}")
        conn.close()
        sys.exit(1)
    except psycopg2.OperationalError as e:
        print(f"❌ Cannot connect to database: {e}")
        print("Make sure PostgreSQL is running and credentials are correct")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--default":
            create_admin()
        elif len(sys.argv) >= 4:
            first_name = sys.argv[1]
            last_name = sys.argv[2]
            email = sys.argv[3]
            create_admin(first_name, last_name, email)
        else:
            print(__doc__)
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(1)
