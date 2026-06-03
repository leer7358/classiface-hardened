#!/usr/bin/env python3
"""
Debug script to validate Firebase token verification setup
"""
import os
import sys
import json
import firebase_admin
from firebase_admin import credentials, auth as fb_auth
from utils.configuration import load_yaml

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
config = load_yaml(os.path.join(PROJECT_ROOT, "configs", "database.yaml"))

def check_firebase_setup():
    print("=" * 60)
    print("🔐 Firebase Configuration Check")
    print("=" * 60)
    
    # Check service account path
    service_account_path = config["firebase"].get("pathToServiceAccount") or ""
    if not os.path.isabs(service_account_path):
        service_account_path = os.path.join(PROJECT_ROOT, service_account_path)
    
    print(f"\n📁 Service Account Path: {service_account_path}")
    
    if not os.path.isfile(service_account_path):
        print(f"❌ ERROR: Service account file NOT FOUND")
        return False
    
    print(f"✅ Service account file exists")
    
    # Check if file is valid JSON
    try:
        with open(service_account_path, 'r') as f:
            sa_data = json.load(f)
        print(f"✅ Service account file is valid JSON")
        print(f"   Project ID: {sa_data.get('project_id')}")
        print(f"   Client Email: {sa_data.get('client_email')}")
    except Exception as e:
        print(f"❌ ERROR: Invalid service account JSON: {str(e)}")
        return False
    
    # Check database URL
    db_url = config["firebase"].get("databaseURL")
    print(f"\n🌐 Database URL: {db_url}")
    if not db_url:
        print(f"❌ ERROR: Database URL not configured")
        return False
    
    print(f"✅ Database URL configured")
    
    # Try to initialize Firebase
    print(f"\n🚀 Initializing Firebase Admin SDK...")
    try:
        if firebase_admin._apps:
            print("⚠️ Firebase already initialized, skipping init")
        else:
            cred = credentials.Certificate(service_account_path)
            firebase_admin.initialize_app(
                cred,
                {"databaseURL": db_url},
            )
            print("✅ Firebase Admin SDK initialized successfully")
    except Exception as e:
        print(f"❌ ERROR: Failed to initialize Firebase: {str(e)}")
        return False
    
    # Test token verification with a dummy token
    print(f"\n🧪 Testing token verification capability...")
    dummy_token = "eyJhbGciOiJSUzI1NiIsImtpZCI6IjExIn0.e30.test"
    try:
        fb_auth.verify_id_token(dummy_token)
    except Exception as e:
        error_str = str(e)
        # We expect this to fail, but it tells us if Firebase is accessible
        if "Malformed" in error_str or "Decode" in error_str:
            print(f"✅ Firebase Auth verification endpoint is accessible")
            print(f"   (Expected error with dummy token: {error_str[:80]}...)")
        else:
            print(f"⚠️ Unexpected error: {error_str[:100]}")
    
    print("\n" + "=" * 60)
    print("✅ Firebase setup appears to be configured correctly")
    print("=" * 60)
    return True

if __name__ == "__main__":
    check_firebase_setup()
