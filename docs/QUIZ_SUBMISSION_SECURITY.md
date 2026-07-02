# Quiz Submission Security - Implementation Guide

## Overview

ClassiFace now implements comprehensive server-side security for quiz submissions to prevent cheating, tampering, and unauthorized replay attacks. All quiz answers are validated and graded exclusively on the server.

---

## Security Features Implemented

### 1. **One-Time Submission Tokens** ✅
- **What**: Each quiz attempt receives a unique, one-time submission token
- **When**: Generated when the quiz starts (`/api/quiz-attempts/<attempt_id>/start`)
- **How**: Token is validated and consumed on submission
- **Why**: Prevents replay attacks - the same submission cannot be submitted twice

**Token Properties:**
- 48-character cryptographically random string (`secrets.token_urlsafe(48)`)
- Stored in `quiz_submission_tokens` table
- Marked as `used=TRUE` after consumption
- Expires after 1 hour of inactivity

### 2. **Server-Side Answer Validation** ✅
- **What**: All student answers are validated against quiz schema before grading
- **Checks**:
  - All question IDs match original quiz questions
  - No extra questions added by tampering
  - Answer format matches expected type (int for multiple-choice, float for numerical, etc.)
  - No injection attempts or malicious data

**Validation Details:**
```
- Multiple-choice: Answer must be valid integer index
- True/False: Answer must be "true" or "false"
- Matching: Answer must be dict mapping
- Numerical: Answer must be convertible to float
- Identification: Answer must be string
```

### 3. **Server-Side Grading** ✅
- **What**: ALL scoring is calculated entirely on the server
- **Never Trust**: Client-submitted scores are completely ignored
- **Process**:
  1. Retrieve quiz questions from database
  2. Extract server-side correct answers (not exposed to client)
  3. Grade each answer using server-side logic
  4. Store final score in database
  5. Client receives only the grade, not the calculation method

### 4. **Replay Attack Prevention** ✅
- **What**: Detects and blocks attempts to resubmit the same quiz
- **Check**: Before processing submission, query audit log for:
  - Same user_id + attempt_id combination
  - With `token_valid = TRUE` (successful submission)
- **Action**: Reject with "Duplicate submission detected" error
- **Audit**: Log all replay attempts with full details

### 5. **Answer Integrity Checks** ✅
- **What**: Detect if answers were modified between client generation and submission
- **Compute**: SHA256 hash of submitted answers JSON
- **Store**: Hash stored in database for forensic analysis
- **Detect**: 
  - Mismatched question count
  - Invalid question IDs
  - Answers outside expected format/range

### 6. **Comprehensive Audit Logging** ✅
- **What**: Every submission attempt is logged with full forensic details
- **Logged Data**:
  - `user_id`: Who submitted
  - `attempt_id`: Which attempt
  - `quiz_id`: Which quiz
  - `submission_timestamp`: When (server time)
  - `client_ip`: Where from (IP address)
  - `user_agent`: Which browser/client
  - `answers_hash`: SHA256 hash of answers
  - `score_awarded`: Final score given
  - `submission_token`: Token used
  - `token_valid`: Whether token was valid
  - `tamper_detected`: Whether tampering was detected
  - `tamper_reason`: Reason for tamper flag

**Audit Table:**
```
quiz_submission_audit (
  id UUID PRIMARY KEY,
  attempt_id UUID (indexed),
  user_id UUID (indexed),
  quiz_id UUID (indexed),
  submission_timestamp TIMESTAMP (indexed),
  client_ip VARCHAR(45),
  user_agent TEXT,
  answers_hash VARCHAR(64),
  score_awarded INT,
  tamper_detected BOOLEAN (indexed),
  tamper_reason TEXT,
  ...
)
```

---

## Database Schema

### New Tables Created

#### `quiz_submission_tokens`
One-time use tokens bound to quiz attempts
```sql
CREATE TABLE quiz_submission_tokens (
    id UUID PRIMARY KEY,
    attempt_id UUID NOT NULL REFERENCES quiz_attempts(attempt_id),
    token VARCHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT NOW(),
    used_at TIMESTAMP,
    used BOOLEAN DEFAULT FALSE,
    user_id UUID NOT NULL,
    quiz_id UUID NOT NULL
);
```

#### `quiz_submission_audit`
Forensic audit trail of all submissions
```sql
CREATE TABLE quiz_submission_audit (
    id UUID PRIMARY KEY,
    attempt_id UUID NOT NULL REFERENCES quiz_attempts(attempt_id),
    user_id UUID NOT NULL,
    quiz_id UUID NOT NULL,
    submission_token VARCHAR(64),
    token_valid BOOLEAN DEFAULT FALSE,
    submission_timestamp TIMESTAMP DEFAULT NOW(),
    client_ip VARCHAR(45),
    user_agent TEXT,
    answers_hash VARCHAR(64),
    score_awarded INT,
    total_points INT,
    tamper_detected BOOLEAN DEFAULT FALSE,
    tamper_reason TEXT,
    submission_attempt_number INT DEFAULT 1
);
```

### New Columns Added to `quiz_attempts`
```sql
ALTER TABLE quiz_attempts ADD COLUMN submitted_ip VARCHAR(45);
ALTER TABLE quiz_attempts ADD COLUMN submission_token_used BOOLEAN DEFAULT FALSE;
ALTER TABLE quiz_attempts ADD COLUMN tamper_detected BOOLEAN DEFAULT FALSE;
ALTER TABLE quiz_attempts ADD COLUMN correct_answers_hash VARCHAR(64);
```

---

## API Changes

### Start Quiz Endpoint
**Endpoint**: `POST /api/quiz-attempts/<attempt_id>/start`

**Request**:
```json
{
  "quizId": "quiz-uuid-here"
}
```

**Response** (NEW - includes submission token):
```json
{
  "started": true,
  "attempt_id": "attempt-uuid-here",
  "submission_token": "abcd1234...xyz789"
}
```

⚠️ **CRITICAL**: Client must store the `submission_token` and send it with submission

### Submit Quiz Endpoint
**Endpoint**: `POST /api/quiz-attempts/<attempt_id>/submit`

**Request** (UPDATED - now requires submission_token):
```json
{
  "quizId": "quiz-uuid-here",
  "submissionToken": "abcd1234...xyz789",
  "answers": {
    "question-id-1": "answer-value",
    "question-id-2": ["answer-1", "answer-2"]
  }
}
```

**Response**:
```json
{
  "attempt_id": "attempt-uuid-here",
  "quiz_id": "quiz-uuid-here",
  "score": 85,
  "total_points": 100,
  "saved": true
}
```

**Error Responses** (New security checks):
- `400`: Missing/invalid submission token
- `403`: Token already used (replay attack detected)
- `403`: Answer validation failed (tampering detected)
- `403`: Token expired (>1 hour old)

---

## Security Flow Diagram

```
┌──────────────────────────────────────────────────────────────┐
│ 1. QUIZ START                                                │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ POST /api/quiz-attempts/<attempt_id>/start             │  │
│ └────────────────────────────────────────────────────────┘  │
│   ↓                                                           │
│ GENERATE ONE-TIME TOKEN                                      │
│   ↓                                                           │
│ Store in quiz_submission_tokens table                        │
│ Token.used = FALSE                                           │
│   ↓                                                           │
│ Response includes submission_token                           │
│ Client MUST store this token                                 │
└──────────────────────────────────────────────────────────────┘

                        [Student Takes Quiz]

┌──────────────────────────────────────────────────────────────┐
│ 2. QUIZ SUBMISSION (SECURE)                                  │
│ ┌────────────────────────────────────────────────────────┐  │
│ │ POST /api/quiz-attempts/<attempt_id>/submit            │  │
│ │ + submissionToken (from step 1)                        │  │
│ │ + answers JSON                                         │  │
│ └────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
                         ↓
         ┌───────────────────────────────────┐
         │ STEP 3: REPLAY ATTACK CHECK       │
         ├───────────────────────────────────┤
         │ Query audit log for:              │
         │ - Same user_id + attempt_id      │
         │ - With token_valid = TRUE        │
         │ - Same submission_token?         │
         └───────────────────────────────────┘
         ↓ (If found)
         ❌ REJECT: "Duplicate submission"
         └─→ Log with tamper_reason
                         ↓ (If not found, continue)
         ┌───────────────────────────────────┐
         │ STEP 4: TOKEN VALIDATION          │
         ├───────────────────────────────────┤
         │ Lookup token in database:         │
         │ - Token exists?                   │
         │ - Token.used = FALSE?             │
         │ - Token age < 1 hour?             │
         │ - Token matches attempt_id?      │
         └───────────────────────────────────┘
         ↓ (If invalid)
         ❌ REJECT: "Invalid token"
         └─→ Log tamper attempt
                         ↓ (If valid, continue)
         ┌───────────────────────────────────┐
         │ STEP 5: LOAD QUIZ (SERVER-SIDE)   │
         ├───────────────────────────────────┤
         │ Fetch quiz from database          │
         │ Extract questions                 │
         │ Extract CORRECT ANSWERS           │
         │ (Never expose to client)          │
         └───────────────────────────────────┘
                         ↓
         ┌───────────────────────────────────┐
         │ STEP 6: ANSWER INTEGRITY          │
         ├───────────────────────────────────┤
         │ Validate submission format:       │
         │ - All question IDs valid?         │
         │ - No extra questions?             │
         │ - Answer formats correct?         │
         │ - Answer values in range?         │
         └───────────────────────────────────┘
         ↓ (If invalid)
         ❌ REJECT: "Answer validation failed"
         └─→ Log tamper_reason with details
                         ↓ (If valid, continue)
         ┌───────────────────────────────────┐
         │ STEP 7: SERVER-SIDE GRADING       │
         ├───────────────────────────────────┤
         │ For each question:                │
         │ - Retrieve stored correct answer  │
         │ - Compare with student answer     │
         │ - Award points if correct         │
         │ - Total score calculated HERE     │
         │ (Client score IGNORED)            │
         └───────────────────────────────────┘
                         ↓
         ┌───────────────────────────────────┐
         │ STEP 8: MARK TOKEN AS USED        │
         ├───────────────────────────────────┤
         │ UPDATE quiz_submission_tokens     │
         │ SET used = TRUE                   │
         │ SET used_at = NOW()               │
         │ WHERE token = %s                  │
         └───────────────────────────────────┘
                         ↓
         ┌───────────────────────────────────┐
         │ STEP 9: PERSIST SUBMISSION        │
         ├───────────────────────────────────┤
         │ UPDATE quiz_attempts              │
         │ - score = calculated_score        │
         │ - submitted_at = NOW()            │
         │ - submitted_ip = client_ip        │
         │ - answers_json = answers          │
         │ - submission_token_used = TRUE    │
         └───────────────────────────────────┘
                         ↓
         ┌───────────────────────────────────┐
         │ STEP 10: AUDIT LOGGING            │
         ├───────────────────────────────────┤
         │ INSERT INTO audit log:            │
         │ - attempt_id, user_id, quiz_id   │
         │ - client_ip, user_agent          │
         │ - submission_timestamp (server)   │
         │ - answers_hash (SHA256)          │
         │ - score_awarded                   │
         │ - token_valid = TRUE              │
         │ - tamper_detected = FALSE         │
         └───────────────────────────────────┘
                         ↓
         ┌───────────────────────────────────┐
         │ STEP 11: RESPONSE                 │
         ├───────────────────────────────────┤
         │ ✅ Return success with:           │
         │ - score (server-calculated)       │
         │ - attempt_id                      │
         │ - saved: true                     │
         └───────────────────────────────────┘
```

---

## Tamper Detection Scenarios

### Scenario 1: Client Modifies Answers After Hashing
**Detected By**: Answer validation + integrity checks
**Action**: Reject with "Answer validation failed"
**Audit**: Log tamper_detected=TRUE, tamper_reason="Invalid answer format"

### Scenario 2: Client Adds Extra Questions
**Detected By**: Answer integrity validation
**Action**: Reject with "Invalid question IDs in submission"
**Audit**: Log tamper_detected=TRUE, tamper_reason="Invalid question IDs"

### Scenario 3: Client Reuses Same Submission Token
**Detected By**: Token already marked used=TRUE
**Action**: Reject with "Submission token already used"
**Audit**: Log tamper_detected=TRUE, tamper_reason="Replay attack"

### Scenario 4: Client Fabricates New Token
**Detected By**: Token not found in database
**Action**: Reject with "Invalid submission token"
**Audit**: Log tamper_detected=TRUE, tamper_reason="Invalid token"

### Scenario 5: Student Tries to Submit Twice
**Detected By**: Replay attack check (audit log query)
**Action**: Reject with "Duplicate submission detected"
**Audit**: Log tamper_detected=TRUE, tamper_reason="Replay attack"

### Scenario 6: Man-in-the-Middle Intercepts Submission
**Detected By**: IP address mismatch (audit log shows different IPs)
**Action**: Submission accepted BUT flagged in audit log
**Investigation**: Instructor can see suspicious pattern in audit logs
**Mitigation**: HTTPS + HSTS prevents interception

---

## Instructor Audit Trail

### View Submission Attempts
Query the `quiz_submission_audit` table to analyze attempts:

```sql
-- All submissions for a quiz
SELECT * FROM quiz_submission_audit
WHERE quiz_id = 'quiz-uuid'
ORDER BY submission_timestamp DESC;

-- Suspicious submissions
SELECT * FROM quiz_submission_audit
WHERE tamper_detected = TRUE
ORDER BY submission_timestamp DESC;

-- Submissions from different IPs
SELECT DISTINCT client_ip, submission_timestamp, user_id
FROM quiz_submission_audit
WHERE quiz_id = 'quiz-uuid'
ORDER BY client_ip;

-- Timing analysis (potential collusion)
SELECT user_id, submission_timestamp, 
       LEAD(submission_timestamp) OVER (ORDER BY submission_timestamp) as next_submission
FROM quiz_submission_audit
WHERE quiz_id = 'quiz-uuid'
ORDER BY submission_timestamp;
```

---

## Client-Side Implementation

### JavaScript Requirements

When submitting quiz, client must:

1. **Store submission token**:
```javascript
// When quiz starts
const response = await fetch('/api/quiz-attempts/' + attemptId + '/start', {...});
const data = await response.json();
const submissionToken = data.submission_token;  // MUST STORE THIS
```

2. **Include token in submission**:
```javascript
// When submitting answers
const submitData = {
  quizId: quizId,
  submissionToken: submissionToken,  // REQUIRED
  answers: answers
};

const response = await fetch(
  '/api/quiz-attempts/' + attemptId + '/submit',
  {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(submitData)
  }
);
```

3. **Don't calculate scores**:
```javascript
// ❌ WRONG - Never do this
const clientScore = calculateScore(answers, correctAnswers);
submitData.score = clientScore;  // Server will ignore this!

// ✅ RIGHT - Let server calculate
// Server receives answers, validates, grades on server only
```

---

## Security Best Practices

### For Students
- ✅ Never refresh/reload page during submission
- ✅ Ensure stable internet connection before submitting
- ✅ Submit in the designated time window
- ❌ Don't attempt multiple simultaneous submissions
- ❌ Don't try to intercept or modify network requests

### For Instructors
- ✅ Monitor audit logs regularly
- ✅ Review `tamper_detected` flag on suspicious scores
- ✅ Cross-reference IP addresses with known lab locations
- ✅ Analyze submission timing for patterns
- ✅ Store audit logs for compliance/investigation

### For System Administrators
- ✅ Ensure database backup/recovery procedures
- ✅ Maintain audit logs (don't delete for legal hold)
- ✅ Monitor database performance (audit table can grow large)
- ✅ Secure database access (restrict to authorized users)
- ✅ Enable database audit logging if possible

---

## Forensic Analysis Guide

### Common Tampering Patterns

**Pattern 1: Multiple Failed Token Validations**
```
SELECT user_id, COUNT(*) as failed_attempts
FROM quiz_submission_audit
WHERE tamper_detected = TRUE
  AND tamper_reason LIKE 'Invalid%token%'
GROUP BY user_id
HAVING COUNT(*) > 3;
```
**Indicates**: Likely attempted to bypass token validation

**Pattern 2: Rapid Multiple Submissions**
```
SELECT user_id, MIN(submission_timestamp), MAX(submission_timestamp),
       COUNT(*) as attempt_count,
       EXTRACT(EPOCH FROM (MAX(submission_timestamp) - MIN(submission_timestamp))) as time_span_seconds
FROM quiz_submission_audit
WHERE quiz_id = 'quiz-uuid'
GROUP BY user_id
HAVING COUNT(*) > 1 AND 
       EXTRACT(EPOCH FROM (MAX(submission_timestamp) - MIN(submission_timestamp))) < 60;
```
**Indicates**: Very rapid resubmissions, possible replay attempt

**Pattern 3: Different IPs Same User**
```
SELECT user_id, COUNT(DISTINCT client_ip) as different_ips,
       ARRAY_AGG(DISTINCT client_ip) as ip_list,
       ARRAY_AGG(DISTINCT submission_timestamp) as timestamps
FROM quiz_submission_audit
WHERE quiz_id = 'quiz-uuid'
GROUP BY user_id
HAVING COUNT(DISTINCT client_ip) > 1;
```
**Indicates**: Student submitted from different locations (could be legitimate or suspicious)

---

## Monitoring & Alerts

### Recommended Alerts to Set Up

1. **High Tamper Rate**
   - Alert if > 5% of submissions have tamper_detected = TRUE
   
2. **Replay Attack Attempts**
   - Alert on any `tamper_reason LIKE 'Replay%'`
   
3. **Token Validation Failures**
   - Alert if 10+ token validation failures for same user
   
4. **Unusual Submission Patterns**
   - Alert if user submits from >5 different IPs same day
   - Alert if submission time is milliseconds after quiz opens

---

## Performance Considerations

### Database Indexes
All critical queries are indexed:
- `quiz_submission_tokens(attempt_id)`
- `quiz_submission_tokens(token)` - UNIQUE
- `quiz_submission_audit(attempt_id)`
- `quiz_submission_audit(user_id)`
- `quiz_submission_audit(quiz_id)`
- `quiz_submission_audit(submission_timestamp)`
- `quiz_submission_audit(tamper_detected)`

### Cleanup Policy
- Submission tokens: Delete after 7 days (token.used=TRUE)
- Audit logs: Retain for minimum 1 year (for investigation/compliance)

---

## Troubleshooting

### Issue: "Invalid submission token" Error
**Causes**:
1. Token not stored/passed from start endpoint
2. Token expired (>1 hour old)
3. Browser cleared session/cookies between start and submit
4. Different device/browser used

**Solution**: Restart quiz to generate new token

### Issue: "Duplicate submission detected" Error
**Causes**:
1. Student already successfully submitted this attempt
2. Trying to resubmit same attempt

**Solution**: Start a new attempt or contact instructor if unintentional

### Issue: "Answer validation failed" Error
**Causes**:
1. Client tried to add extra questions not in quiz
2. Answer format doesn't match question type
3. Answer value outside expected range

**Solution**: Verify internet connection is stable, reload quiz, try again

---

## Compliance & Legal

### Data Retention
- Quiz submission audit logs are RETAINED per institutional policy
- 1-year minimum retention recommended
- May be required for academic integrity investigations
- Do NOT delete audit logs without approval from legal/compliance

### Privacy
- IP addresses logged for security/audit purposes
- IP addresses not shared with students
- IP addresses may be used for investigation
- Comply with FERPA/GDPR as applicable

### Evidence Handling
- Audit logs are considered authoritative evidence
- Database backups should be maintained
- Chain of custody should be documented if investigation occurs

---

## Additional Resources

- [OWASP - Cheating Prevention](https://owasp.org/www-community/attacks/Abuse_of_Functionality)
- [Academic Integrity - Best Practices](https://www.educause.edu/research-and-publications)
- [Database Forensics](https://en.wikipedia.org/wiki/Computer_forensics)
