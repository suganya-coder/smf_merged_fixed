

# =============================================================
# api_override.py  —  Override Feature Routes  v11.0
#
# NEW in v11.0: STRICT HIERARCHICAL ROLE-BASED ACCESS CONTROL
#
# Override Access Matrix (enforced in BACKEND — cannot be bypassed):
#   Admin   → Can ONLY override HOD attendance records
#   HOD     → Can ONLY override Staff attendance records
#   Staff   → Can ONLY override Student attendance records
#   Student → NO override permissions whatsoever
#
# Security: JWT role is extracted server-side from the token.
#   Even direct Postman/curl requests are blocked if the JWT role
#   does not match the required hierarchy.
#
# Endpoints:
#   POST /api/attendance/override/new
#   GET  /api/attendance/override/history
#   GET  /api/attendance/override/filter
#   GET  /api/attendance/override/stats
#   GET  /api/override/role-matrix          ← NEW: returns allowed target for caller
#   GET  /api/staff/{staff_id}/permissions
#   PUT  /api/staff/{staff_id}/incharge
# =============================================================

from __future__ import annotations
import logging
import json
import sqlite3
from typing import Optional

log = logging.getLogger(__name__)

try:
    from fastapi import HTTPException, Depends, Request
    _fastapi_available = True
except ImportError:
    _fastapi_available = False

try:
    from pydantic import BaseModel

    class OverrideNewReq(BaseModel):
        # Who is performing the override (must be the logged-in user)
        staff_id:                str = ""
        staff_role:              str = "staff"
        # Target record details
        target_id:               str = ""   # NEW: ID of person whose record is modified
        target_role_hint:        str = ""   # optional client hint — validated server-side
        department:              str = ""
        year:                    str = ""
        semester:                str = ""
        section:                 str = ""
        student_register_number: str = ""
        student_name:            str = ""
        course_code:             str = ""
        course_name:             str = ""
        period:                  str = ""
        attendance_from:         str = "Absent"
        attendance_to:           str = "Present"
        reason:                  str = ""

    class StaffUpdateReq(BaseModel):
        is_class_incharge:   Optional[bool] = None
        incharge_department: Optional[str]  = None
        incharge_year:       Optional[str]  = None
        incharge_section:    Optional[str]  = None
        role:                Optional[str]  = None

    class OverrideRequestBody(BaseModel):
        student_id:  str = ""
        faculty_id:  str = ""
        subject_id:  str = ""
        old_status:  str = "Absent"
        new_status:  str = "Present"
        reason:      str = ""
        date:        str = ""   # YYYY-MM-DD
        course_code: str = ""
        period:      str = ""


except ImportError:
    pass


def register_override_routes(app, get_current_user, teacher_required,
                              admin_required, uname_fn):
    """Called from api.create_app() to attach override endpoints."""
    if not _fastapi_available:
        log.warning("override routes not registered: fastapi not available")
        return
    try:
        import database as db
        import database_override as ov_db
    except ImportError as exc:
        log.warning("override routes not registered: %s", exc)
        return

    # ── Shared helpers ──────────────────────────────────────
    def _resolve_student_name(register_number: str, fallback: str) -> str:
        if fallback.strip():
            return fallback.strip()
        try:
            conn = sqlite3.connect(ov_db.DB_PATH, timeout=10,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT name FROM students WHERE register_number=? AND active=1",
                (register_number,)
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT name FROM students WHERE (student_id=? OR roll_number=?) AND active=1 LIMIT 1",
                    (register_number, register_number)
                ).fetchone()
            conn.close()
            return row["name"] if row else register_number
        except Exception:
            return register_number

    def _get_actor_role_from_jwt(user: dict) -> str:
        """Extract and normalise the actor's role from the validated JWT payload."""
        raw = (user.get("role") or "").strip().lower()
        return ov_db._normalise_role(raw)

    def _display_role(role: str) -> str:
        return ov_db.ROLE_DISPLAY.get(role, role.title())

    def _auto_staff_role(req_role: str, staff) -> str:
        if not staff:
            return req_role or "Staff"
        stored = (staff.get("role") or "").lower()
        is_ic  = bool(staff.get("is_class_incharge") or staff.get("class_incharge_dept"))
        if stored in ("admin", "hod", "administrator"):
            return "Admin/HOD"
        if is_ic or stored == "classincharge":
            return "Class Incharge"
        return "Subject Staff"

    # ===========================================================
    # GET /api/override/role-matrix
    # Returns the allowed override target for the currently logged-in user.
    # Used by the frontend to dynamically show/hide override buttons.
    # ===========================================================
    @app.get("/api/override/role-matrix")
    def override_role_matrix(user: dict = Depends(get_current_user)):
        """
        Returns what the current user is permitted to override.
        Frontend uses this to show/hide UI controls.
        """
        actor = _get_actor_role_from_jwt(user)
        allowed_target = ov_db.ROLE_CAN_OVERRIDE.get(actor)
        return {
            "actor_role":        actor,
            "actor_display":     _display_role(actor),
            "can_override":      allowed_target is not None,
            "allowed_target":    allowed_target or "",
            "allowed_target_display": _display_role(allowed_target) if allowed_target else "None",
            "matrix": {
                "admin":   "hod",
                "hod":     "staff",
                "staff":   "student",
                "student": None,
            }
        }

    # ===========================================================
    # POST /api/attendance/override/new
    # ===========================================================
    @app.post("/api/attendance/override/new")
    def new_override(req: OverrideNewReq, request: Request,
                     user: dict = Depends(teacher_required)):
        """
        Submit an attendance override.

        SECURITY:
          1. JWT is validated by teacher_required dependency.
          2. Actor role is extracted from JWT (server-side) — not from request body.
          3. Target role is resolved from DB — not trusted from client.
          4. Hierarchy is checked: actor may only modify allowed_target role.
        """

        # ── Step 1: Extract actor role from JWT (cannot be spoofed) ──
        actor_role = _get_actor_role_from_jwt(user)
        actor_username = uname_fn(user)

        # Students cannot use this endpoint at all
        if actor_role == "student":
            log.warning(
                "SECURITY: Student '%s' attempted attendance override — blocked.",
                actor_username
            )
            raise HTTPException(
                status_code=403,
                detail="Access Denied: Students are not permitted to override attendance records."
            )

        # ── Step 2: Basic field validation ───────────────────────────
        # Determine the target ID: for student overrides it's the register number;
        # for HOD/staff overrides it may be target_id or staff_id field.
        target_id = (req.target_id or req.student_register_number or "").strip()

        missing = []
        if not req.staff_id.strip():   missing.append("Modifier ID (staff_id)")
        if not target_id:              missing.append("Target ID (target_id or student_register_number)")
        if not req.course_code.strip():missing.append("Course Code")
        if not req.period.strip():     missing.append("Period")
        if not req.reason.strip():     missing.append("Reason")
        if missing:
            raise HTTPException(400, f"Required fields missing: {', '.join(missing)}")

        # ── Step 3: Hierarchical permission check ────────────────────
        allowed, perm_msg, resolved_target_role = ov_db.check_hierarchical_permission(
            actor_role=actor_role,
            target_id=target_id,
            target_role_hint=req.target_role_hint,
        )

        if not allowed:
            log.warning(
                "SECURITY: Role '%s' (user=%s) attempted to override target '%s' "
                "(resolved role='%s') — DENIED. Reason: %s",
                actor_role, actor_username, target_id, resolved_target_role, perm_msg
            )
            raise HTTPException(status_code=403, detail=perm_msg)

        log.info(
            "Override authorised: actor=%s (role=%s) → target=%s (role=%s)",
            actor_username, actor_role, target_id, resolved_target_role
        )

        # ── Step 4: Resolve names and save ───────────────────────────
        student_name  = _resolve_student_name(target_id, req.student_name)
        staff         = ov_db.get_staff_by_id(req.staff_id)
        overridden_by = (staff.get("name") if staff else None) or req.staff_id
        staff_role    = _auto_staff_role(req.staff_role, staff)

        row_id = ov_db.add_attendance_override(
            department=req.department, year=req.year,
            semester=req.semester, section=req.section,
            student_register_number=target_id,
            student_name=student_name,
            course_code=req.course_code, course_name=req.course_name,
            period=req.period,
            attendance_from=req.attendance_from, attendance_to=req.attendance_to,
            reason=req.reason, overridden_by=overridden_by,
            staff_id=req.staff_id, staff_role=staff_role,
            target_role=resolved_target_role,
        )

        # Mirror into legacy override_log
        try:
            action = ("mark_present"
                      if req.attendance_to.lower() in ("present", "od", "late", "medical")
                      else "mark_absent")
            db.teacher_override(
                student_id=target_id,
                period=req.period, action=action,
                note=(
                    f"[{staff_role}→{resolved_target_role}] {req.reason} | "
                    f"{req.attendance_from}→{req.attendance_to} | "
                    f"Course:{req.course_code} (by {req.staff_id})"
                ),
                teacher=overridden_by,
            )
        except Exception as e:
            log.warning("legacy mirror failed (non-fatal): %s", e)

        db.log_audit(
            actor_username, "attendance_override", target_id,
            (
                f"actor_role={actor_role} target_role={resolved_target_role} "
                f"{req.attendance_from}→{req.attendance_to} | "
                f"{req.course_code} P:{req.period} | {req.reason}"
            ),
            request.client.host if request.client else "?",
        )
        return {
            "status":  "ok",
            "id":      row_id,
            "message": f"Override saved successfully (id={row_id}). {perm_msg}",
            "actor_role":   actor_role,
            "target_role":  resolved_target_role,
        }

    # ===========================================================
    # GET /api/attendance/override/history
    # ===========================================================
    @app.get("/api/attendance/override/history")
    def override_history(
        department: str = "", year: str = "", semester: str = "",
        section: str = "", staff_id: str = "", course_code: str = "",
        limit: int = 500, _: dict = Depends(get_current_user),
    ):
        try:
            return ov_db.get_override_history_filtered(
                department=department, year=year, semester=semester,
                section=section, staff_id=staff_id,
                course_code=course_code, limit=limit,
            )
        except Exception as exc:
            raise HTTPException(500, str(exc))

    # ===========================================================
    # GET /api/attendance/override/filter  (alias)
    # ===========================================================
    @app.get("/api/attendance/override/filter")
    def override_filter(
        department: str = "", year: str = "", semester: str = "",
        section: str = "", staff_id: str = "", course_code: str = "",
        limit: int = 500, _: dict = Depends(get_current_user),
    ):
        try:
            return ov_db.get_override_history_filtered(
                department=department, year=year, semester=semester,
                section=section, staff_id=staff_id,
                course_code=course_code, limit=limit,
            )
        except Exception as exc:
            raise HTTPException(500, str(exc))

    # ===========================================================
    # GET /api/attendance/override/stats
    # ===========================================================
    @app.get("/api/attendance/override/stats")
    def override_stats(_: dict = Depends(get_current_user)):
        try:
            return ov_db.get_override_stats()
        except Exception as exc:
            raise HTTPException(500, str(exc))

    # ===========================================================
    # GET /api/staff/{staff_id}/permissions
    # ===========================================================
    @app.get("/api/staff/{staff_id}/permissions")
    def staff_permissions(staff_id: str, user: dict = Depends(teacher_required)):
        """
        Returns what override target this staff member is allowed to modify.
        Now uses the hierarchical role matrix.
        """
        actor_role    = _get_actor_role_from_jwt(user)
        allowed_target = ov_db.ROLE_CAN_OVERRIDE.get(actor_role)

        staff = ov_db.get_staff_by_id(staff_id)
        if not staff:
            raise HTTPException(404, f"Staff ID '{staff_id}' not found in faculty table")

        subjects_raw = staff.get("subjects") or "[]"
        try:
            subjects = json.loads(subjects_raw) if isinstance(subjects_raw, str) else subjects_raw
        except Exception:
            subjects = []

        is_ic = bool(staff.get("is_class_incharge") or staff.get("class_incharge_dept"))
        role  = ov_db._normalise_role(staff.get("role") or "staff")

        return {
            "staff_id":              staff_id,
            "name":                  staff.get("name") or staff_id,
            "role":                  role,
            "role_display":          _display_role(role),
            "is_class_incharge":     is_ic,
            "incharge_department":   staff.get("incharge_department") or staff.get("class_incharge_dept") or "",
            "incharge_year":         str(staff.get("incharge_year") or staff.get("class_incharge_year") or ""),
            "incharge_section":      staff.get("incharge_section") or staff.get("class_incharge_section") or "",
            "assigned_subjects":     subjects,
            # Hierarchical info — what can the LOGGED-IN user override?
            "actor_role":            actor_role,
            "actor_allowed_target":  allowed_target or "",
            "can_override_all":      allowed_target is not None,
        }

    # ===========================================================
    # PUT /api/staff/{staff_id}/incharge  (admin only)
    # ===========================================================
    @app.put("/api/staff/{staff_id}/incharge")
    def update_staff_incharge(staff_id: str, req: StaffUpdateReq,
                               user: dict = Depends(admin_required)):
        # Extra guard: only admin can call this
        actor_role = _get_actor_role_from_jwt(user)
        if actor_role != "admin":
            raise HTTPException(403, "Access Denied: Only Admin can update staff incharge settings.")

        updates, vals = [], []
        if req.is_class_incharge is not None:
            updates.append("is_class_incharge=?"); vals.append(1 if req.is_class_incharge else 0)
        if req.incharge_department is not None:
            updates.append("incharge_department=?"); vals.append(req.incharge_department)
        if req.incharge_year is not None:
            updates.append("incharge_year=?"); vals.append(req.incharge_year)
        if req.incharge_section is not None:
            updates.append("incharge_section=?"); vals.append(req.incharge_section)
        if req.role is not None:
            # Prevent escalating a staff member to admin via this endpoint
            new_role = ov_db._normalise_role(req.role)
            if new_role == "admin":
                raise HTTPException(403, "Access Denied: Cannot assign Admin role via this endpoint.")
            updates.append("role=?"); vals.append(req.role)
        if not updates:
            raise HTTPException(400, "Nothing to update")
        vals.append(staff_id)
        try:
            conn = sqlite3.connect(ov_db.DB_PATH, timeout=15, check_same_thread=False)
            conn.execute(f"UPDATE faculty SET {','.join(updates)} WHERE fac_id=?", vals)
            conn.commit(); conn.close()
        except Exception as exc:
            raise HTTPException(500, str(exc))
        return {"status": "ok", "message": f"Staff {staff_id} updated successfully"}


    # ===========================================================
    # POST /api/attendance/override/request
    # Student or Faculty raises a correction request
    # ===========================================================
    @app.post("/api/attendance/override/request")
    def create_override_request(req: OverrideRequestBody, request: Request,
                                user: dict = Depends(get_current_user)):
        from datetime import date as _date

        actor_role = _get_actor_role_from_jwt(user)
        actor_id   = user.get("sub") or user.get("username") or ""

        # Validate required fields
        missing = []
        if not req.student_id.strip(): missing.append("student_id")
        if not req.faculty_id.strip(): missing.append("faculty_id")
        if not req.old_status.strip(): missing.append("old_status")
        if not req.new_status.strip(): missing.append("new_status")
        if not req.reason.strip():     missing.append("reason")
        if not req.date.strip():       missing.append("date")
        if not req.course_code.strip():missing.append("course_code")
        if not req.period.strip():     missing.append("period")
        if missing:
            raise HTTPException(400, f"Required fields missing: {', '.join(missing)}")

        # Students can only request for themselves
        if actor_role == "student" and req.student_id.strip() != actor_id:
            raise HTTPException(
                403,
                "Access Denied: Students may only raise correction requests for their own records."
            )

        # Determine if HOD approval is needed (any date before today)
        try:
            rec_date   = _date.fromisoformat(req.date.strip())
            today      = _date.today()
            hod_needed = 1 if rec_date < today else 0
        except ValueError:
            raise HTTPException(400, "Invalid date format. Use YYYY-MM-DD.")

        row_id = ov_db.add_override_request(
            student_id=req.student_id.strip(),
            faculty_id=req.faculty_id.strip(),
            subject_id=req.subject_id.strip(),
            old_status=req.old_status.strip(),
            new_status=req.new_status.strip(),
            requested_by=actor_id,
            requested_role=actor_role,
            reason=req.reason.strip(),
            date=req.date.strip(),
            course_code=req.course_code.strip(),
            period=req.period.strip(),
            hod_required=hod_needed,
        )

        ov_db.add_audit_log(
            attendance_id=req.student_id.strip(),
            old_value=req.old_status,
            new_value=req.new_status,
            changed_by=actor_id,
            changed_role=actor_role,
            reason=req.reason.strip(),
            action_type="correction_request",
        )

        try:
            db.log_audit(
                actor_id, "correction_request", req.student_id,
                f"role={actor_role} {req.old_status}→{req.new_status} | "
                f"{req.course_code} P:{req.period} date:{req.date} | {req.reason}",
                request.client.host if request.client else "?",
            )
        except Exception as e:
            log.warning("log_audit failed (non-fatal): %s", e)

        return {
            "status":       "ok",
            "id":           row_id,
            "hod_required": bool(hod_needed),
            "message": (
                "Request submitted. HOD approval required (old record)."
                if hod_needed else
                "Request submitted. Faculty will review."
            ),
        }

    # ===========================================================
    # GET /api/attendance/override/pending
    # Faculty sees their own queue; HOD/Admin see hod_required queue
    # ===========================================================
    @app.get("/api/attendance/override/pending")
    def get_pending_override_requests(
        faculty_id: str = "",
        hod_only: bool = False,
        user: dict = Depends(teacher_required),
    ):
        actor_role = _get_actor_role_from_jwt(user)

        if actor_role == "student":
            raise HTTPException(403, "Access Denied: Students cannot view pending requests.")

        if actor_role in ("hod", "admin"):
            # HOD/Admin see all HOD-required pending requests (or everything if hod_only=False)
            hod_filter = 1 if hod_only else None
            rows = ov_db.get_pending_requests(faculty_id=None, hod_required=hod_filter)
        else:
            # Faculty only see their own non-HOD queue
            fid = faculty_id or user.get("sub") or ""
            rows = ov_db.get_pending_requests(faculty_id=fid, hod_required=0)

        return {"status": "ok", "count": len(rows), "requests": rows}

    # ===========================================================
    # POST /api/attendance/override/{request_id}/approve
    # ===========================================================
    @app.post("/api/attendance/override/{request_id}/approve")
    def approve_override(request_id: int, body: dict,
                         request: Request,
                         user: dict = Depends(teacher_required)):
        actor_role = _get_actor_role_from_jwt(user)
        actor_id   = user.get("sub") or user.get("username") or ""

        if actor_role == "student":
            raise HTTPException(403, "Access Denied.")

        # Fetch the request to check hod_required and ownership
        rows = ov_db.get_pending_requests()
        target = next((r for r in rows if r["id"] == request_id), None)

        # Also check non-pending (may have already been actioned)
        if not target:
            try:
                import sqlite3 as _sq
                conn = _sq.connect(ov_db.DB_PATH, timeout=10, check_same_thread=False)
                conn.row_factory = _sq.Row
                row = conn.execute(
                    "SELECT * FROM attendance_override_requests WHERE id=?",
                    (request_id,)
                ).fetchone()
                conn.close()
                target = dict(row) if row else None
            except Exception:
                pass

        if not target:
            raise HTTPException(404, f"Override request #{request_id} not found.")

        if target["approval_status"] != "PENDING":
            raise HTTPException(409, f"Request #{request_id} is already {target['approval_status']}.")

        # Faculty can only approve their own same-day requests
        if actor_role not in ("hod", "admin"):
            if target.get("hod_required"):
                raise HTTPException(
                    403,
                    "Access Denied: This record requires HOD approval. Please escalate to HOD."
                )
            if target.get("faculty_id") != actor_id:
                raise HTTPException(
                    403,
                    "Access Denied: You can only approve requests assigned to you."
                )

        ov_db.approve_override_request(request_id, actor_id, actor_role)

        ov_db.add_audit_log(
            attendance_id=target.get("student_id", ""),
            old_value=target.get("old_status", ""),
            new_value=target.get("new_status", ""),
            changed_by=actor_id,
            changed_role=actor_role,
            reason=body.get("reason", "Approved"),
            action_type="approval",
        )

        try:
            db.log_audit(
                actor_id, "override_approved", target.get("student_id", ""),
                f"request_id={request_id} role={actor_role} "
                f"{target.get('old_status')}→{target.get('new_status')} | "
                f"{target.get('course_code')} P:{target.get('period')}",
                request.client.host if request.client else "?",
            )
        except Exception as e:
            log.warning("log_audit failed (non-fatal): %s", e)

        return {"status": "ok", "message": f"Request #{request_id} approved successfully."}

    # ===========================================================
    # POST /api/attendance/override/{request_id}/reject
    # ===========================================================
    @app.post("/api/attendance/override/{request_id}/reject")
    def reject_override(request_id: int, body: dict,
                        request: Request,
                        user: dict = Depends(teacher_required)):
        actor_role = _get_actor_role_from_jwt(user)
        actor_id   = user.get("sub") or user.get("username") or ""

        if actor_role == "student":
            raise HTTPException(403, "Access Denied.")

        reason = (body.get("reason") or "").strip()
        if len(reason) < 5:
            raise HTTPException(400, "Rejection reason must be at least 5 characters.")

        # Fetch request for audit details
        try:
            import sqlite3 as _sq
            conn = _sq.connect(ov_db.DB_PATH, timeout=10, check_same_thread=False)
            conn.row_factory = _sq.Row
            row = conn.execute(
                "SELECT * FROM attendance_override_requests WHERE id=?",
                (request_id,)
            ).fetchone()
            conn.close()
            target = dict(row) if row else None
        except Exception:
            target = None

        if not target:
            raise HTTPException(404, f"Override request #{request_id} not found.")

        if target["approval_status"] != "PENDING":
            raise HTTPException(409, f"Request #{request_id} is already {target['approval_status']}.")

        ov_db.reject_override_request(request_id, actor_id, actor_role, reason)

        ov_db.add_audit_log(
            attendance_id=target.get("student_id", ""),
            old_value=target.get("old_status", ""),
            new_value=target.get("new_status", ""),
            changed_by=actor_id,
            changed_role=actor_role,
            reason=reason,
            action_type="rejection",
        )

        try:
            db.log_audit(
                actor_id, "override_rejected", target.get("student_id", ""),
                f"request_id={request_id} reason={reason}",
                request.client.host if request.client else "?",
            )
        except Exception as e:
            log.warning("log_audit failed (non-fatal): %s", e)

        return {"status": "ok", "message": f"Request #{request_id} rejected."}

    # ===========================================================
    # GET /api/attendance/audit-log
    # ===========================================================
    @app.get("/api/attendance/audit-log")
    def get_attendance_audit_log(
        limit: int = 500,
        user: dict = Depends(teacher_required),
    ):
        actor_role = _get_actor_role_from_jwt(user)
        if actor_role == "student":
            raise HTTPException(403, "Access Denied: Students cannot view the audit log.")
        try:
            logs = ov_db.get_audit_log(limit=limit)
            return {"status": "ok", "count": len(logs), "logs": logs}
        except Exception as exc:
            raise HTTPException(500, str(exc))


    log.info("Override routes registered (v11.0 — hierarchical RBAC) ✓")