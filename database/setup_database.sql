-- ========================================
-- Face Recognition Attendance System
-- PostgreSQL Database Schema Setup
-- ========================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ========================================
-- 1. USERS TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    firebase_uid TEXT UNIQUE,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT DEFAULT 'student', -- 'student', 'instructor', 'admin'
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_firebase_uid ON users(firebase_uid);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ========================================
-- 2. CLASSES TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS classes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    class_code TEXT NOT NULL,
    section_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    description TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_classes_section ON classes(section_name);

-- ========================================
-- 3. CLASS STUDENTS TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS class_students (
    student_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    enrolled_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (student_id, class_id)
);

CREATE INDEX IF NOT EXISTS idx_class_students_class_id ON class_students(class_id);
CREATE INDEX IF NOT EXISTS idx_class_students_student_id ON class_students(student_id);

-- ========================================
-- 4. CLASS INSTRUCTORS TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS class_instructors (
    instructor_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    assigned_at TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (instructor_id, class_id)
);

CREATE INDEX IF NOT EXISTS idx_class_instructors_class_id ON class_instructors(class_id);
CREATE INDEX IF NOT EXISTS idx_class_instructors_instructor_id ON class_instructors(instructor_id);

-- ========================================
-- 5. CLASS SESSIONS TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS class_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    session_date DATE NOT NULL,
    present_start TIME DEFAULT '00:00:00',
    present_until TIME NOT NULL,
    late_start TIME,
    late_until TIME NOT NULL,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(class_id, session_date)
);

CREATE INDEX IF NOT EXISTS idx_class_sessions_class_id ON class_sessions(class_id);
CREATE INDEX IF NOT EXISTS idx_class_sessions_date ON class_sessions(session_date);

-- ========================================
-- 6. ATTENDANCE RECORDS TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS attendance_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    student_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    session_id UUID REFERENCES class_sessions(id) ON DELETE SET NULL,
    attendance_date DATE NOT NULL,
    attendance_time TIME NOT NULL,
    status TEXT DEFAULT 'present', -- 'present', 'late', 'absent'
    verified_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(student_id, class_id, attendance_date)
);

CREATE INDEX IF NOT EXISTS idx_attendance_student_id ON attendance_records(student_id);
CREATE INDEX IF NOT EXISTS idx_attendance_class_id ON attendance_records(class_id);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance_records(attendance_date);

-- ========================================
-- 7. QUIZZES TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS quizzes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    class_id UUID NOT NULL REFERENCES classes(id) ON DELETE CASCADE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT,
    total_points INT NOT NULL DEFAULT 100,
    question_count INT DEFAULT 0,
    questions_json JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_quizzes_class_id ON quizzes(class_id);

-- ========================================
-- 8. QUIZ ATTEMPTS TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS quiz_attempts (
    attempt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    quiz_id UUID NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    quiz_title TEXT,
    score DECIMAL(5, 2) DEFAULT 0,
    total_points INT,
    submitted_at TIMESTAMP,
    answers_json JSONB DEFAULT '{}'::jsonb,
    started_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quiz_attempts_user_id ON quiz_attempts(user_id);
CREATE INDEX IF NOT EXISTS idx_quiz_attempts_quiz_id ON quiz_attempts(quiz_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quiz_attempts_unique ON quiz_attempts(user_id, quiz_id);

-- ========================================
-- 9. QUIZ ATTEMPT VIOLATIONS TABLE
-- ========================================
CREATE TABLE IF NOT EXISTS quiz_attempt_violations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    attempt_id UUID NOT NULL REFERENCES quiz_attempts(attempt_id) ON DELETE CASCADE,
    violation_type TEXT NOT NULL, -- e.g., 'face_not_detected', 'multiple_faces', 'person_changed'
    violation_time TIMESTAMP DEFAULT NOW(),
    details JSONB
);

CREATE INDEX IF NOT EXISTS idx_violations_attempt_id ON quiz_attempt_violations(attempt_id);

-- ========================================
-- 10. SAMPLE DATA (Optional - Comment out if not needed)
-- ========================================
-- Insert a sample admin user (password: "admin123" hashed)
-- INSERT INTO users (firebase_uid, first_name, last_name, full_name, email, role)
-- VALUES ('admin_firebase_uid', 'Admin', 'User', 'Admin User', 'admin@example.com', 'admin')
-- ON CONFLICT DO NOTHING;

-- ========================================
-- DONE
-- ========================================
