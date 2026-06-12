# =============================================================
# api_extras.py  —  Smart Attendance System  v10.0
#
# Routes for:
#   GET/POST /api/courses            - course catalogue
#   GET/POST /api/electives          - elective pool + assignments
#   GET/POST /api/student-extended   - enrollment with email/year/section
#   GET/POST /api/timetable/student  - student timetable
#   GET/POST /api/timetable/staff    - staff timetable
#   GET      /api/period-slots       - the 7 period timings
# =============================================================
import logging
log = logging.getLogger(__name__)


def register_extra_routes(app, get_current_user, teacher_required, admin_required):
    from fastapi import Depends, HTTPException, Query
    import database_extras as dbe

    # ── Period Slots ────────────────────────────────────────
    @app.get("/api/period-slots")
    def get_period_slots(_: dict = Depends(get_current_user)):
        return dbe.get_period_slots()

    # ── Courses ─────────────────────────────────────────────
    @app.get("/api/courses")
    def list_courses(dept: str, semester: int = None,
                     course_type: str = None,
                     _: dict = Depends(get_current_user)):
        return dbe.get_courses(dept, semester, course_type)

    @app.get("/api/courses/by-year")
    def courses_by_year(dept: str, year: int,
                        _: dict = Depends(get_current_user)):
        return dbe.get_courses_by_year(dept, year)

    @app.get("/api/courses/elective-pool")
    def elective_pool(dept: str, _: dict = Depends(get_current_user)):
        return dbe.get_elective_pool(dept)

    @app.post("/api/courses")
    def save_course(data: dict, user: dict = Depends(admin_required)):
        return dbe.upsert_course(
            dept        = data.get("dept",""),
            year        = int(data.get("year",1)),
            semester    = int(data.get("semester",1)),
            course_code = data.get("course_code","").upper().strip(),
            course_name = data.get("course_name","").strip(),
            course_type = data.get("course_type","core"),
            credits     = int(data.get("credits",3)),
            created_by  = user.get("username","admin"),
        )

    # ── Elective Assignments ────────────────────────────────
    @app.get("/api/electives")
    def get_electives(dept: str, semester: int, section: str = None,
                      _: dict = Depends(get_current_user)):
        return dbe.get_elective_selections(dept, semester, section)

    @app.post("/api/electives/assign")
    def assign_elective(data: dict, user: dict = Depends(teacher_required)):
        return dbe.assign_elective(
            student_id  = data.get("student_id",""),
            dept        = data.get("dept",""),
            year        = int(data.get("year",1)),
            semester    = int(data.get("semester",1)),
            section     = data.get("section","A"),
            course_code = data.get("course_code","").upper().strip(),
            selected_by = user.get("username","admin"),
        )

    # ── Semester ────────────────────────────────────────────
    @app.get("/api/semesters")
    def get_semesters(dept: str, _: dict = Depends(get_current_user)):
        return dbe.get_all_semesters(dept)

    @app.get("/api/semesters/current")
    def current_semester(dept: str, year: int,
                         _: dict = Depends(get_current_user)):
        return dbe.get_current_semester(dept, year)

    # ── Student Extended ────────────────────────────────────
    @app.get("/api/students/extended")
    def student_ext(student_id: str, _: dict = Depends(get_current_user)):
        return dbe.get_student_extended(student_id)

    @app.get("/api/students/section")
    def section_students(dept: str, year: int, section: str,
                         _: dict = Depends(get_current_user)):
        return dbe.get_section_students_extended(dept, year, section)

    @app.post("/api/students/extended")
    def save_student_ext(data: dict, _: dict = Depends(get_current_user)):
        return dbe.upsert_student_extended(
            student_id    = data.get("student_id",""),
            dept          = data.get("dept",""),
            year          = int(data.get("year",1)),
            section       = data.get("section","A"),
            semester      = int(data.get("semester",1)),
            student_email = data.get("student_email",""),
            parent_email  = data.get("parent_email",""),
            staff_email   = data.get("staff_email",""),
            hod_email     = data.get("hod_email",""),
        )

    # ── Student Timetable ───────────────────────────────────
    @app.get("/api/timetable/student")
    def student_tt(dept: str, year: int, section: str,
                   semester: int = None,
                   _: dict = Depends(get_current_user)):
        return dbe.get_student_timetable(dept, year, section, semester)

    @app.post("/api/timetable/student")
    def save_student_tt(data: dict, user: dict = Depends(teacher_required)):
        return dbe.upsert_student_timetable(
            dept        = data.get("dept",""),
            year        = int(data.get("year",1)),
            section     = data.get("section","A"),
            day_of_week = data.get("day_of_week","MON"),
            period_no   = int(data.get("period_no",1)),
            course_code = data.get("course_code",""),
            course_name = data.get("course_name",""),
            faculty_id  = data.get("faculty_id",""),
            faculty_name= data.get("faculty_name",""),
            room        = data.get("room",""),
            semester    = int(data.get("semester",1)),
        )

    @app.delete("/api/timetable/student")
    def del_student_tt(dept: str, year: int, section: str,
                       day_of_week: str, period_no: int,
                       semester: int = 1,
                       user: dict = Depends(teacher_required)):
        return dbe.delete_timetable_slot(dept, year, section, day_of_week, period_no, semester)

    # ── Staff Timetable ─────────────────────────────────────
    @app.get("/api/timetable/staff")
    def staff_tt(faculty_id: str,
                 dept: str = Query(None),
                 semester: int = Query(None),
                 _: dict = Depends(get_current_user)):
        return dbe.get_staff_timetable(faculty_id, dept=dept, semester=semester)

    # ── Timetable: POST bulk upsert ──────────────────────────
    @app.post("/api/timetable/student/bulk")
    def bulk_student_tt(data: list, user: dict = Depends(teacher_required)):
        """Bulk upsert timetable slots. Body: list of slot objects."""
        results = []
        for slot in data:
            r = dbe.upsert_student_timetable(
                dept        = slot.get("dept",""),
                year        = int(slot.get("year",1)),
                section     = slot.get("section","A"),
                day_of_week = slot.get("day_of_week","MON"),
                period_no   = int(slot.get("period_no",1)),
                course_code = slot.get("course_code",""),
                course_name = slot.get("course_name",""),
                faculty_id  = slot.get("faculty_id",""),
                faculty_name= slot.get("faculty_name",""),
                room        = slot.get("room",""),
                semester    = int(slot.get("semester",1)),
            )
            results.append(r)
        return {"inserted": len(results)}

    # ── Admin seed: re-run Vinoth Kumar seed ─────────────────
    @app.post("/api/admin/seed-vinoth-kumar")
    def api_seed_vinoth(user: dict = Depends(admin_required)):
        """Re-run the Vinoth Kumar faculty + timetable seed (idempotent)."""
        return dbe.seed_vinoth_kumar()

    # ── Full Timetable Seed (all depts / all sems / 50 faculty) ──
    @app.post("/api/admin/seed-timetable")
    def api_seed_all_timetables(data: dict = None, user: dict = Depends(admin_required)):
        """Seed complete timetable for all 8 departments, all semesters, sections A/B/C."""
        try:
            from timetable_seed import seed_all_timetables
            force = (data or {}).get("force", False)
            seed_all_timetables(force=force)
            return {"status": "ok", "message": "Full timetable seeded successfully."}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Timetable: GET by dept/year/section (from seed data) ──
    @app.get("/api/timetable/full")
    def full_student_tt(dept: str, year: int, section: str,
                        semester: int = None,
                        _: dict = Depends(get_current_user)):
        """Returns timetable from the seeded database (all depts/sems)."""
        try:
            from timetable_seed import get_student_timetable as _gst
            return _gst(dept, year, section, semester)
        except Exception:
            return dbe.get_student_timetable(dept, year, section, semester)

    # ── Staff Timetable from seed data ─────────────────────
    @app.get("/api/timetable/staff/full")
    def full_staff_tt(faculty_id: str,
                      dept: str = Query(None),
                      semester: int = Query(None),
                      _: dict = Depends(get_current_user)):
        """Returns staff timetable from seeded database, filtered by dept and semester."""
        try:
            from timetable_seed import get_staff_timetable as _gftt
            return _gftt(faculty_id, dept=dept, semester=semester)
        except Exception:
            return dbe.get_staff_timetable(faculty_id, dept=dept, semester=semester)

    # ── Faculty list (from seed, with fallback) ─────────────
    @app.get("/api/faculty/all")
    def get_all_faculty_seed(_: dict = Depends(get_current_user)):
        """Returns all faculty from seed DB with fallback to faculty table."""
        fac_list = []
        try:
            from timetable_seed import get_all_faculty as _gaf
            fac_list = _gaf()
        except Exception:
            pass
        if not fac_list:
            try:
                from api_features import get_all_faculty as _gaf2
                fac_list = _gaf2()
            except Exception:
                pass
        return {"faculty": fac_list}

    # ── Timetable Status — check if seeded ────────────────────
    @app.get("/api/timetable/status")
    def timetable_status(_: dict = Depends(get_current_user)):
        """Check seed status: faculty count, student slots, staff slots."""
        try:
            import sqlite3, os, config as _cfg
            db_path = os.path.join(_cfg.BASE_DIR, "attendance.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            def safe_count(sql):
                try: return conn.execute(sql).fetchone()[0]
                except: return 0
            fac   = safe_count("SELECT COUNT(*) FROM faculty WHERE active=1")
            stud  = safe_count("SELECT COUNT(*) FROM student_timetable")
            staff = safe_count("SELECT COUNT(*) FROM staff_timetable")
            conn.close()
            return {"faculty": fac, "student_slots": stud, "staff_slots": staff,
                    "seeded": fac > 0 and staff > 0}
        except Exception as e:
            return {"faculty": 0, "student_slots": 0, "staff_slots": 0,
                    "seeded": False, "error": str(e)}

    log.info("Extra routes registered (courses, electives, timetable, full-seed)")
