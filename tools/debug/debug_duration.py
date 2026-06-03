import yaml
import psycopg2
import psycopg2.extras

# Load database config
with open('configs/database.yaml', 'r') as f:
    config = yaml.safe_load(f)
    db_config = config['postgres']

# Connect to database
conn = psycopg2.connect(
    host=db_config['host'],
    port=db_config['port'],
    user=db_config['user'],
    password=db_config['password'],
    database=db_config['database'],
    cursor_factory=psycopg2.extras.RealDictCursor
)

# Test the query for a specific user/class
user_id = "73382ab1-888e-4448-a9f0-b8434f418d6a"
class_id = "30acd460-60c2-4cff-a867-bd1a508df646"

with conn.cursor() as cur:
    cur.execute(
        """
        SELECT 
          ar.attendance_date, 
          ar.verified_at as time_in,
          qa.submitted_at as time_out,
          qa.started_at,
          ar.status,
          qa.attempt_id,
          qa.user_id as qa_user_id
        FROM attendance_records ar
        LEFT JOIN quiz_attempts qa ON (
          qa.user_id = ar.student_id
          AND qa.submitted_at >= ar.verified_at
          AND DATE(qa.submitted_at) = DATE(ar.attendance_date)
          AND qa.attempt_id = (
            SELECT attempt_id FROM quiz_attempts qa2
            WHERE qa2.user_id = ar.student_id
              AND qa2.submitted_at >= ar.verified_at
              AND DATE(qa2.submitted_at) = DATE(ar.attendance_date)
            ORDER BY qa2.submitted_at DESC
            LIMIT 1
          )
        )
        WHERE ar.student_id=%s
          AND ar.class_id=%s
        ORDER BY ar.attendance_date DESC, ar.verified_at DESC NULLS LAST
        LIMIT 5;
        """,
        (str(user_id), str(class_id)),
    )
    rows = cur.fetchall() or []
    
    print(f"\n✅ Query returned {len(rows)} rows\n")
    for i, r in enumerate(rows):
        print(f"Row {i+1}:")
        print(f"  attendance_date: {r.get('attendance_date')}")
        print(f"  time_in (verified_at): {r.get('time_in')}")
        print(f"  time_out (submitted_at): {r.get('time_out')}")
        print(f"  started_at: {r.get('started_at')}")
        print(f"  status: {r.get('status')}")
        print(f"  attempt_id: {r.get('attempt_id')}")
        print(f"  qa_user_id: {r.get('qa_user_id')}")
        
        # Check duration calculation
        started_at = r.get('started_at')
        time_out = r.get('time_out')
        if started_at and time_out:
            duration = (time_out - started_at).total_seconds()
            print(f"  Duration (seconds): {duration}")
            if duration < 60:
                print(f"  Should display as: {int(duration)}s ✅")
            else:
                minutes = int(duration / 60)
                print(f"  Should display as: {minutes}m")
        else:
            print(f"  No quiz attempt found (time_out is None)")
        print()

conn.close()

# Test the query for a specific user/class
user_id = "73382ab1-888e-4448-a9f0-b8434f418d6a"
class_id = "30acd460-60c2-4cff-a867-bd1a508df646"

with conn.cursor() as cur:
    cur.execute(
        """
        SELECT 
          ar.attendance_date, 
          ar.verified_at as time_in,
          qa.submitted_at as time_out,
          qa.started_at,
          ar.status,
          qa.attempt_id,
          qa.user_id as qa_user_id
        FROM attendance_records ar
        LEFT JOIN quiz_attempts qa ON qa.user_id = ar.student_id
        WHERE ar.student_id=%s
          AND ar.class_id=%s
        ORDER BY ar.attendance_date DESC, ar.verified_at DESC NULLS LAST
        LIMIT 5;
        """,
        (str(user_id), str(class_id)),
    )
    rows = cur.fetchall() or []
    
    print(f"\n✅ Query returned {len(rows)} rows\n")
    for i, r in enumerate(rows):
        print(f"Row {i+1}:")
        print(f"  attendance_date: {r.get('attendance_date')}")
        print(f"  time_in (verified_at): {r.get('time_in')}")
        print(f"  time_out (submitted_at): {r.get('time_out')}")
        print(f"  started_at: {r.get('started_at')}")
        print(f"  status: {r.get('status')}")
        print(f"  attempt_id: {r.get('attempt_id')}")
        print(f"  qa_user_id: {r.get('qa_user_id')}")
        
        # Check duration calculation
        started_at = r.get('started_at')
        time_out = r.get('time_out')
        if started_at and time_out:
            duration = (time_out - started_at).total_seconds()
            print(f"  Duration (seconds): {duration}")
            if duration < 60:
                print(f"  Should display as: {int(duration)}s")
            else:
                minutes = int(duration / 60)
                print(f"  Should display as: {minutes}m")
        else:
            print(f"  No quiz attempt found (time_out is None)")
        print()

conn.close()
