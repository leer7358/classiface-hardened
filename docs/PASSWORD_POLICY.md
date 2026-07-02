# Firebase Password Policy Implementation

## Overview

SecureTest has implemented a comprehensive password security policy aligned with Firebase Authentication best practices. The system enforces password strength requirements at both server-side and client-side levels.

## Password Requirements

All passwords must meet the following criteria:

1. **Minimum Length**: 8 characters
   - Prevents weak short passwords

2. **Uppercase Letter**: At least 1 uppercase letter (A-Z)
   - Increases complexity

3. **Number**: At least 1 numeric digit (0-9)
   - Prevents dictionary-based attacks

4. **Special Character**: At least 1 special character from the set:
   - `! @ # $ % ^ & * ( ) _ + - = [ ] { } ; : ' " , . < > ? / \ | ` ~`

## Implementation Details

### Server-Side Validation

The `validate_password_strength()` function in `app.py` enforces all password requirements:

```python
def validate_password_strength(password: str) -> tuple:
    """
    Validate password meets Firebase authentication policy requirements:
    - Minimum 8 characters
    - At least 1 uppercase letter
    - At least 1 number
    - At least 1 special character
    
    Returns: (is_valid: bool, error_message: str, strength_score: int)
    """
```

**Location**: `app.py`, lines 177-227

**Usage Example**:
```python
is_valid, error_message, strength_score = validate_password_strength(new_password)
if not is_valid:
    return fail(error_message, 400)
```

### Client-Side Strength Indicator

The system includes a real-time password strength checker accessible via the API endpoint:

**Endpoint**: `POST /api/password/check-strength`

**Request**:
```json
{
    "password": "MyPassword123!"
}
```

**Response**:
```json
{
    "success": true,
    "valid": true,
    "errors": [],
    "strength": 3,
    "message": ""
}
```

**Strength Score** (0-4 scale via zxcvbn library):
- 0: Very Weak - Very guessable
- 1: Weak - Guessable
- 2: Fair - Somewhat guessable
- 3: Good - Safely unguessable
- 4: Very Strong - Very unguessable

### Integration Points

Password validation is applied at the following endpoints:

1. **Admin Password Management** (planned):
   - `POST /api/admin/password/change`

2. **Instructor Password Change**:
   - `POST /api/instructor/password/change`
   - File: `controllers/settings_controller.py`
   - Validation applied before Firebase update

3. **Student Password Change**:
   - `POST /api/student/password/change`
   - File: `controllers/settings_controller.py`
   - Validation applied before Firebase update

4. **New User Creation**:
   - Admin creates instructors/students with initial passwords
   - Passwords enforced in:
     - `POST /admin/instructors/create`
     - `POST /admin/students/edit/<student_id>`
     - `POST /admin/settings/create-admin`

## Logging and Monitoring

All password validation attempts are logged for security monitoring:

```python
app.logger.warning(f"Password validation failed for instructor: {_mask_identifier(user_id)}")
```

Logs include:
- User role and masked identifier
- Timestamp
- Validation failure reason
- Log level: WARNING (visible in production)

See [Structured Logging Guide](./SETUP_GUIDE.md#Logging) for details on accessing logs.

## Firebase Console Configuration

### Step 1: Enable Password Authentication

1. Go to [Firebase Console](https://console.firebase.google.com)
2. Select your project: `classiface-1a8ca`
3. Navigate to **Authentication** > **Sign-in method**
4. Ensure **Email/Password** is **Enabled**
5. Scroll to **Password requirements**

### Step 2: Configure Password Policy

1. In **Password requirements** section, click **Edit**
2. Set minimum password length: **8 characters**
3. Enable the following requirements:
   - ✅ Uppercase letters (A-Z)
   - ✅ Lowercase letters (a-z) - *optional, already satisfied by number/special char*
   - ✅ Numbers (0-9)
   - ✅ Special characters (!@#$%^&*...)

4. Click **Save**

### Step 3: Update Security Rules (if using Firebase Realtime Database)

If your project uses Realtime Database, update security rules to require strong passwords:

```json
{
  "rules": {
    ".read": false,
    ".write": false,
    "users": {
      "$uid": {
        ".validate": "root.child('users').child($uid).hasChildren(['email', 'password_strength_enforced'])",
        ".write": "auth.uid === $uid"
      }
    }
  }
}
```

## Client-Side Implementation (Optional)

To add a visual password strength indicator in HTML forms:

```html
<div class="form-group">
    <label for="new_password">New Password</label>
    <input 
        type="password" 
        id="new_password" 
        name="new_password" 
        required
        placeholder="Min 8 chars, 1 uppercase, 1 number, 1 special char"
    >
    <div id="password-strength" style="display:none; margin-top:8px;">
        <div id="strength-bar" style="height:4px; width:100%; background:#ddd; border-radius:2px;">
            <div id="strength-fill" style="height:100%; width:0%; transition:width 0.3s; border-radius:2px;"></div>
        </div>
        <div id="strength-text" style="font-size:12px; margin-top:4px;"></div>
        <div id="strength-errors" style="font-size:12px; color:#ff6b84; margin-top:4px;"></div>
    </div>
</div>

<script>
document.getElementById('new_password').addEventListener('input', async (e) => {
    const password = e.target.value;
    if (!password) {
        document.getElementById('password-strength').style.display = 'none';
        return;
    }
    
    try {
        const response = await fetch('/api/password/check-strength', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({password})
        });
        
        const data = await response.json();
        document.getElementById('password-strength').style.display = 'block';
        
        // Update strength bar color and width
        const strengthColors = ['#ff6b84', '#ff9f43', '#ffc107', '#26de81', '#2bcf6b'];
        const strengthTexts = ['Very Weak', 'Weak', 'Fair', 'Good', 'Very Strong'];
        
        document.getElementById('strength-fill').style.width = ((data.strength + 1) * 20) + '%';
        document.getElementById('strength-fill').style.backgroundColor = strengthColors[data.strength];
        document.getElementById('strength-text').textContent = strengthTexts[data.strength];
        
        // Show validation errors
        if (data.errors.length > 0) {
            document.getElementById('strength-errors').innerHTML = 
                '❌ ' + data.errors.join('<br>❌ ');
        } else {
            document.getElementById('strength-errors').innerHTML = '✓ Password meets all requirements';
            document.getElementById('strength-errors').style.color = '#26de81';
        }
    } catch (err) {
        console.error('Password check failed:', err);
    }
});
</script>
```

## Testing Password Validation

### Valid Passwords Examples

```
MyPassword123!
Admin@2024Pass
SecureP@ss01
Test#1234Abc
```

### Invalid Passwords Examples

```
pass123!       # No uppercase letter
PASSWORD123    # No special character
Pass@1         # Less than 8 characters
password123!   # No uppercase letter
Pass@123word   # Too long but valid
```

### Test Endpoint

```bash
# Test valid password
curl -X POST http://localhost:5000/api/password/check-strength \
  -H "Content-Type: application/json" \
  -d '{"password":"MyPassword123!"}'

# Test invalid password
curl -X POST http://localhost:5000/api/password/check-strength \
  -H "Content-Type: application/json" \
  -d '{"password":"weak"}'
```

## Error Messages

Users receive clear, actionable error messages when passwords don't meet requirements:

```
Password must be at least 8 characters; 
Password must contain at least 1 uppercase letter; 
Password must contain at least 1 number; 
Password must contain at least 1 special character (!@#$%^&*)
```

Each missing requirement is listed, allowing users to understand exactly what to fix.

## Security Considerations

### Server-Side Only Validation

While client-side indicators improve UX, **never rely solely on client-side validation**. All endpoints verify password strength before updating Firebase credentials.

### Masked Logging

Password validation failures are logged with masked user identifiers to protect privacy:

```python
app.logger.warning(f"Password validation failed: {_mask_identifier(uid)}")
# Output: "Password validation failed: abc12345***"
```

### No Password Storage

SecureTest does not store passwords. All authentication is delegated to Firebase Authentication:
- Passwords updated via Firebase Admin SDK
- Verified via Firebase REST API
- Cryptographically secured by Google

### Entropy Measurement

The zxcvbn library provides realistic password strength estimates by:
- Checking against common passwords
- Analyzing pattern frequency
- Avoiding overly strict rules that encourage weak patterns

## Dependencies

Password policy implementation depends on:

- **zxcvbn-python** (4.4.28): Password strength estimation
  - Install: `pip install zxcvbn-python`
  - Located: `requirements.txt`

- **Firebase Admin SDK** (7.1.0): Password management
  - Already installed

## Troubleshooting

### Issue: "zxcvbn module not found"

**Solution**: Install the required package:
```bash
pip install zxcvbn-python==4.4.28
# Or if using the project requirements
pip install -r requirements.txt
```

### Issue: Password validation endpoint returns 500 error

**Solution**: Check logs for details:
```bash
tail -f logs/app.log | grep "password strength check"
```

Most common causes:
- zxcvbn not installed
- Invalid JSON in request body
- Server experiencing high load

### Issue: Firebase Console password policy conflicts with app validation

**Solution**: Ensure Firebase policy is more permissive than app validation:
- App requires: 8 chars, 1 uppercase, 1 number, 1 special char
- Firebase should require: at least 8 characters (or same)
- Never require less in Firebase than in the app

## Future Enhancements

Potential improvements for future versions:

1. **Breach Database Integration**
   - Check passwords against known breach databases (HaveIBeenPwned)

2. **Pattern Analysis**
   - Detect keyboard patterns, sequential numbers, etc.

3. **Multi-Language Support**
   - Localize error messages

4. **Admin Policy Override**
   - Allow admins to set different policies for different user roles

5. **Password History**
   - Prevent reuse of recent passwords (requires database schema change)

6. **Two-Factor Authentication (2FA)**
   - Add TOTP or SMS-based 2FA as additional layer

## References

- [Firebase Authentication - Password Requirements](https://firebase.google.com/docs/auth/passwords)
- [NIST Password Recommendations](https://pages.nist.gov/800-63-3/sp800-63b.html)
- [zxcvbn: Realistic Password Strength Estimation](https://github.com/dropbox/zxcvbn)
- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)

---

**Last Updated**: 2024
**Maintained By**: SecureTest Development Team
