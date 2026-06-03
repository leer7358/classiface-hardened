#!/usr/bin/env python3
"""
Enhanced Firebase token diagnostics
Decodes and validates ID tokens
"""
import sys
import json
import base64
from datetime import datetime

def decode_jwt_payload(token: str):
    """Decode JWT payload without verification (for inspection only)"""
    try:
        # JWT format: header.payload.signature
        parts = token.split('.')
        if len(parts) != 3:
            return None, "Invalid JWT format (expected 3 parts)"
        
        # Add padding if needed
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded), None
    except Exception as e:
        return None, str(e)

def validate_token_contents(token: str):
    """Validate token structure and claims"""
    print("🔍 Token Analysis")
    print("-" * 60)
    
    if not token or token.strip() == "":
        print("❌ ERROR: Empty token")
        return False
    
    payload, error = decode_jwt_payload(token)
    
    if error:
        print(f"❌ Cannot decode token: {error}")
        return False
    
    if not payload:
        print("❌ Token payload is empty")
        return False
    
    print(f"✅ Token decodes successfully\n")
    
    # Check required claims
    required_claims = ['aud', 'iss', 'uid', 'email', 'iat', 'exp']
    print("📋 Token Claims:")
    for claim in required_claims:
        value = payload.get(claim, "❌ MISSING")
        
        # Special handling for timestamps
        if claim in ['iat', 'exp']:
            if isinstance(value, int):
                dt = datetime.utcfromtimestamp(value)
                print(f"   {claim:8} = {value} ({dt})")
            else:
                print(f"   {claim:8} = {value}")
        else:
            print(f"   {claim:8} = {value}")
    
    # Validate critical claims
    print("\n✓ Validation Results:")
    issues = []
    
    if payload.get('aud') != 'classiface-1a8ca':
        issues.append(f"⚠️ Audience mismatch: got '{payload.get('aud')}', expected 'classiface-1a8ca'")
    
    if not payload.get('uid'):
        issues.append("⚠️ Missing 'uid' (Firebase UID)")
    
    if not payload.get('email'):
        issues.append("⚠️ Missing 'email'")
    
    if not payload.get('iss'):
        issues.append("⚠️ Missing 'iss' (Issuer)")
    
    # Check expiration
    if payload.get('exp'):
        exp_time = datetime.utcfromtimestamp(payload['exp'])
        now = datetime.utcnow()
        if now > exp_time:
            issues.append(f"⚠️ Token EXPIRED (expired at {exp_time})")
        else:
            remaining = (exp_time - now).total_seconds()
            print(f"   ✅ Token valid for {remaining:.0f} more seconds")
    
    if issues:
        for issue in issues:
            print(f"   {issue}")
        return False
    else:
        print("   ✅ All required claims present and valid")
        return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python check_token.py <idToken>")
        print("\nExample:")
        print("  python check_token.py eyJhbGciOiJSUzI1NiIsImtpZCI6IjQ1...\"")
        print("\nTo get a token for testing:")
        print("  1. Open browser DevTools (F12) during signup")
        print("  2. Go to Network tab")
        print("  3. Find request to /api/auth/register-profile")
        print("  4. In Request body, copy the 'idToken' value")
        print("  5. Run: python check_token.py <idToken>")
        sys.exit(1)
    
    token = sys.argv[1].strip()
    print("=" * 60)
    print("Firebase ID Token Validator")
    print("=" * 60 + "\n")
    
    valid = validate_token_contents(token)
    
    print("\n" + "=" * 60)
    if valid:
        print("✅ Token structure is correct")
    else:
        print("❌ Token has issues - see above")
    print("=" * 60)
