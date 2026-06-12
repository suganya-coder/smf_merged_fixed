


/**
 * enroll_roles.js  —  EduTrack Pro  v9.6
 * =========================================================================
 * Role-aware enrollment helpers that extend the core app.js enrollment UI.
 *
 * Responsibilities
 * ────────────────
 * 1. _existCheckTimers  — debounce registry for checkEnrollExists() calls
 *                         (shared across Student / Faculty / HOD panels).
 * 2. validateDobInput   — Date-of-Birth range guard used by all three
 *                         enrollment forms before the submit hits the API.
 * 3. validateNameInput  — Name field guard (letters + single spaces only).
 * 4. validateMobileFull — Async 10-digit Indian-mobile + duplicate check.
 * 5. validateEmailFull  — Async email-format + duplicate check (skips the
 *                         student email field that is handled by smfOtp).
 *
 * NOTE: openFaceCaptureModal is intentionally NOT defined here.
 *       The real implementation lives in the IIFE at the bottom of app.js
 *       (window.openFaceCaptureModal / window._fcStartCapture).
 *       Defining it here would overwrite the working camera implementation
 *       because enroll_roles.js loads AFTER app.js.
 *
 * Loading order (index.html):
 *   app.js  →  features.js  →  enroll_roles.js  →  attendance.js
 *   →  frontend_otp_patch.js
 *
 * All functions defined here are already called from app.js — this file
 * merely provides the implementations that were split out to keep app.js
 * below the browser's eager-parse threshold.
 * =========================================================================
 */

(function () {
  'use strict';

  // ── 1. Debounce registry ──────────────────────────────────────────────
  // app.js references window._existCheckTimers inside checkEnrollExists().
  // Expose it on the global object so both scripts share the same map.
  if (!window._existCheckTimers) {
    window._existCheckTimers = { student: null, faculty: null, hod: null };
  }


  // ── 2. DOB validation ─────────────────────────────────────────────────
  /**
   * validateDobInput(fieldId, role, designationFieldId?)
   *
   * Validates a date-of-birth <input type="date"> field.
   * Rules:
   *   • Student  : age 10–40 years
   *   • Faculty  : age 18–70  (if designation contains "professor" → 21–70)
   *   • HOD      : age 25–70
   *
   * Shows an inline error message below the field when invalid.
   * Returns true if valid, false otherwise.
   */
  window.validateDobInput = function validateDobInput(fieldId, role, designationFieldId) {
    const field = document.getElementById(fieldId);
    if (!field) return true;   // field not present on this panel — skip

    const val = (field.value || '').trim();

    // Remove any previous error
    const errId  = fieldId + '_dob_err';
    let   errEl  = document.getElementById(errId);
    if (errEl) errEl.remove();

    if (!val) {
      // Required check is handled elsewhere; just clear
      return true;
    }

    const dob  = new Date(val);
    const now  = new Date();
    let   ageY = now.getFullYear() - dob.getFullYear();
    const m    = now.getMonth() - dob.getMonth();
    if (m < 0 || (m === 0 && now.getDate() < dob.getDate())) ageY--;

    let minAge = 18, maxAge = 70, label = 'Person';
    if (role === 'student') {
      minAge = 10; maxAge = 40; label = 'Student';
    } else if (role === 'hod') {
      minAge = 25; maxAge = 70; label = 'HOD';
    } else {
      // Faculty — check if professor-level role
      if (designationFieldId) {
        const desig = (document.getElementById(designationFieldId)?.value || '').toLowerCase();
        if (desig.includes('professor') || desig.includes('prof')) minAge = 21;
      }
      label = 'Faculty';
    }

    if (ageY < minAge || ageY > maxAge) {
      errEl           = document.createElement('div');
      errEl.id        = errId;
      errEl.style.cssText = 'color:#dc2626;font-size:.75rem;margin-top:3px;';
      errEl.textContent   = `${label} age must be between ${minAge} and ${maxAge} years (current: ${ageY}).`;
      field.parentNode.appendChild(errEl);
      field.style.borderColor = '#dc2626';
      return false;
    }

    field.style.borderColor = '';
    return true;
  };


  // ── 3. Name validation ────────────────────────────────────────────────
  /**
   * validateNameInput(fieldId, label)
   *
   * Ensures a name field contains only letters, spaces, dots, hyphens.
   * Returns true if valid.
   */
  window.validateNameInput = function validateNameInput(fieldId, label) {
    const field = document.getElementById(fieldId);
    if (!field) return true;

    const val    = (field.value || '').trim();
    const errId  = fieldId + '_name_err';
    let   errEl  = document.getElementById(errId);
    if (errEl) errEl.remove();
    field.style.borderColor = '';

    if (!val) return true;   // Required check done elsewhere

    // Allow letters, single spaces, dots, hyphens (for "Dr. Smith-Jones")
    if (!/^[A-Za-z][A-Za-z .'-]{0,49}$/.test(val)) {
      errEl               = document.createElement('div');
      errEl.id            = errId;
      errEl.style.cssText = 'color:#dc2626;font-size:.75rem;margin-top:3px;';
      errEl.textContent   = `${label || 'Name'} must contain only letters (and spaces / dots / hyphens).`;
      field.parentNode.appendChild(errEl);
      field.style.borderColor = '#dc2626';
      return false;
    }
    return true;
  };


  // ── 4. Mobile validation (async — duplicate check) ────────────────────
  /**
   * validateMobileFull(fieldId, label, excludeId?)
   *
   * 1. Format check: 10 digits, starts with 6-9.
   * 2. Async GET /api/check/mobile/<number>?exclude_id=<id> duplicate check.
   *
   * Returns Promise<boolean>.
   */
  window.validateMobileFull = async function validateMobileFull(fieldId, label, excludeId) {
    const field = document.getElementById(fieldId);
    if (!field) return true;

    const val   = (field.value || '').trim();
    const errId = fieldId + '_mob_err';
    let   errEl = document.getElementById(errId);
    if (errEl) errEl.remove();
    field.style.borderColor = '';

    if (!val) return true;   // Optional field — skip

    // Format check
    if (!/^[6-9][0-9]{9}$/.test(val)) {
      errEl               = document.createElement('div');
      errEl.id            = errId;
      errEl.style.cssText = 'color:#dc2626;font-size:.75rem;margin-top:3px;';
      errEl.textContent   = `${label || 'Mobile'}: must be a valid 10-digit Indian number (starts with 6-9).`;
      field.parentNode.appendChild(errEl);
      field.style.borderColor = '#dc2626';
      return false;
    }

    // Duplicate check
    try {
      const qs  = excludeId ? `?exclude_id=${encodeURIComponent(excludeId)}` : '';
      const res = await apiFetch(`/api/check/mobile/${encodeURIComponent(val)}${qs}`);
      if (res && res.exists) {
        const who = res.name ? ` (registered to ${res.name})` : '';
        errEl               = document.createElement('div');
        errEl.id            = errId;
        errEl.style.cssText = 'color:#dc2626;font-size:.75rem;margin-top:3px;';
        errEl.textContent   = `${label || 'Mobile'}${who} is already registered.`;
        field.parentNode.appendChild(errEl);
        field.style.borderColor = '#dc2626';
        return false;
      }
    } catch (_) {
      // Network error — allow through; server will do a final duplicate check
    }

    return true;
  };


  // ── 5. Email validation (async — duplicate check) ─────────────────────
  /**
   * validateEmailFull(fieldId, label, excludeId?)
   *
   * 1. Basic format check.
   * 2. Async GET /api/check/email/<addr>?exclude_id=<id> duplicate check.
   *
   * NOTE: The student enrollment email (en_semail) is intentionally excluded
   * from this path — it is controlled by the smfOtp controller in app.js.
   *
   * Returns Promise<boolean>.
   */
  window.validateEmailFull = async function validateEmailFull(fieldId, label, excludeId) {
    const field = document.getElementById(fieldId);
    if (!field) return true;

    const val   = (field.value || '').trim().toLowerCase();
    const errId = fieldId + '_email_err';
    let   errEl = document.getElementById(errId);
    if (errEl) errEl.remove();
    field.style.borderColor = '';

    if (!val) return true;   // Optional field — skip

    // Basic format
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(val)) {
      errEl               = document.createElement('div');
      errEl.id            = errId;
      errEl.style.cssText = 'color:#dc2626;font-size:.75rem;margin-top:3px;';
      errEl.textContent   = `${label || 'Email'}: please enter a valid email address.`;
      field.parentNode.appendChild(errEl);
      field.style.borderColor = '#dc2626';
      return false;
    }

    // Duplicate check
    try {
      const qs  = excludeId ? `?exclude_id=${encodeURIComponent(excludeId)}` : '';
      const res = await apiFetch(`/api/check/email/${encodeURIComponent(val)}${qs}`);
      if (res && res.exists) {
        const who  = res.name ? ` (${res.name})` : '';
        const role = res.role ? ` as ${res.role}` : '';
        errEl               = document.createElement('div');
        errEl.id            = errId;
        errEl.style.cssText = 'color:#dc2626;font-size:.75rem;margin-top:3px;';
        errEl.textContent   = `${label || 'Email'}${who} is already registered${role}.`;
        field.parentNode.appendChild(errEl);
        field.style.borderColor = '#dc2626';
        return false;
      }
    } catch (_) {
      // Network error — allow through; server will do a final check
    }

    return true;
  };


  // ── 6. Graceful guard: smfOtp stub ────────────────────────────────────
  // If app.js loaded before smfOtp was fully initialised (very unlikely but
  // safe to guard), provide a no-op stub so doEnrollStudent() won't throw.
  if (typeof window.smfOtp === 'undefined') {
    window.smfOtp = {
      onEmailInput: function () {},
      onEmailBlur:  function () {},
      sendOtp:      function () {},
      resendOtp:    function () {},
      onOtpInput:   function () {},
      isVerified:   function () { return false; },
      reset:        function () {},
    };
    console.warn('[enroll_roles] smfOtp not yet defined — stub installed. ' +
                 'Ensure app.js is loaded before enroll_roles.js.');
  }


  console.log('[enroll_roles] Enrollment role helpers loaded ✓');

})();