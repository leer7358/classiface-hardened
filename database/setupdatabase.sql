SELECT column_name
FROM information_schema.columns
WHERE table_name = 'attendance_records'
ORDER BY ordinal_position;