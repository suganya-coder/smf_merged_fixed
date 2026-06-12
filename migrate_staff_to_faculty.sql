-- =============================================================
-- migrate_staff_to_faculty.sql  —  EduTrack Pro v10.1
-- One-shot migration: merge staff table into faculty, then drop it.
-- Run this ONLY on databases that still have the old staff table.
-- The Python init_db() now does this automatically on startup.
-- =============================================================

-- STEP 1: Add extended columns to faculty (skip if already present)
-- Note: SQLite does not support IF NOT EXISTS on ALTER TABLE.
-- Run each line individually and ignore "duplicate column" errors.
-- ALTER TABLE faculty ADD COLUMN first_name    TEXT DEFAULT '';
-- ALTER TABLE faculty ADD COLUMN last_name     TEXT DEFAULT '';
-- ALTER TABLE faculty ADD COLUMN department    TEXT DEFAULT '';
-- ALTER TABLE faculty ADD COLUMN employee_code TEXT DEFAULT '';
-- ALTER TABLE faculty ADD COLUMN joining_date  TEXT DEFAULT '';
-- ALTER TABLE faculty ADD COLUMN enrolled_on   TEXT DEFAULT '';
-- ALTER TABLE faculty ADD COLUMN status        TEXT DEFAULT 'Active';
-- ALTER TABLE faculty ADD COLUMN dob           TEXT DEFAULT '';

-- STEP 2: Migrate staff rows that don't exist in faculty yet
INSERT OR IGNORE INTO faculty (
    fac_id, name, gender, dept, department, designation,
    email, mobile, date_of_birth, dob, role, active,
    first_name, last_name, employee_code,
    joining_date, status, enrolled_on, created_at
)
SELECT
    staff_id,
    TRIM(COALESCE(first_name,'')||' '||COALESCE(last_name,'')),
    gender, department, department, designation,
    email, mobile, date_of_birth, date_of_birth,
    COALESCE(role,'Faculty'), active,
    first_name, COALESCE(last_name,''), COALESCE(employee_code,''),
    COALESCE(joining_date,''), COALESCE(status,'Active'),
    COALESCE(enrolled_on,''), datetime('now','localtime')
FROM staff
WHERE staff_id NOT IN (SELECT fac_id FROM faculty);

-- STEP 3: Update existing faculty rows with staff data for empty fields
UPDATE faculty SET
    employee_code = COALESCE(NULLIF(employee_code,''),
                    (SELECT employee_code FROM staff WHERE staff_id=faculty.fac_id)),
    joining_date  = COALESCE(NULLIF(joining_date,''),
                    (SELECT joining_date  FROM staff WHERE staff_id=faculty.fac_id)),
    enrolled_on   = COALESCE(NULLIF(enrolled_on,''),
                    (SELECT enrolled_on   FROM staff WHERE staff_id=faculty.fac_id)),
    email         = COALESCE(NULLIF(email,''),
                    (SELECT email         FROM staff WHERE staff_id=faculty.fac_id)),
    mobile        = COALESCE(NULLIF(mobile,''),
                    (SELECT mobile        FROM staff WHERE staff_id=faculty.fac_id))
WHERE fac_id IN (SELECT staff_id FROM staff);

-- STEP 4: Drop staff table
DROP TABLE IF EXISTS staff;

-- STEP 5: Verify
SELECT 'faculty_rows' AS check_name, COUNT(*) AS result FROM faculty;
