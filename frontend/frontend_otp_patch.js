


/**
 * frontend_otp_patch.js  —  Smart Attendance System v9.6 (E Auth Merge)
 * ========================================================================
 * Adds to the SMF frontend:
 *   1. Failed-login counter → shows "Forgot Password?" after 3 failures
 *      (separately tracked for Admin portal AND Faculty portal)
 *   2. OTP modal (email → send OTP → verify code → new password)
 *      Faculty can enter their Faculty ID OR registered email
 *   3. First-login forced password change modal
 *   4. Eye toggle (show/hide) on all password fields in the modal
 *
 * ROOT CAUSE FIXES for Faculty "Forgot Password?" not showing:
 *   FIX-A: Two separate forgot-links injected — one under Admin Sign In,
 *          one under Faculty Sign In.  Each is shown/hidden independently.
 *   FIX-B: Two separate fail counters — adminFailCount / facFailCount —
 *          so faculty failures only reveal the faculty link.
 *   FIX-C: Faculty forgot-password modal accepts Faculty ID (e.g. FAC001)
 *          OR registered email. The backend /api/auth/forgot-password only
 *          accepts email, so when a Faculty ID is entered the JS first
 *          resolves it to an email via GET /api/faculty/<id> then proceeds.
 *
 * HOW TO USE:
 *   This file is already at frontend/frontend_otp_patch.js.
 *   Make sure index.html loads it AFTER app.js:
 *     <script src="/frontend_otp_patch.js"></script>
 * ========================================================================
 */

;(function () {
  'use strict';

  // ── Separate fail counters per portal (FIX-B) ─────────────
  let _adminFailCount = 0;
  let _facFailCount   = 0;
  const API = '';   // same origin

  // ── Inject CSS ────────────────────────────────────────────
  const style = document.createElement('style');
  style.textContent = `
    .otp-modal-overlay {
      position: fixed; inset: 0; background: rgba(0,0,0,0.5);
      display: flex; align-items: center; justify-content: center;
      z-index: 9999; font-family: 'Plus Jakarta Sans', sans-serif;
    }
    .otp-modal {
      background: #fff; border-radius: 16px; padding: 32px;
      width: 420px; max-width: 95vw;
      box-shadow: 0 20px 60px rgba(0,0,0,0.2);
      animation: otpSlideIn .22s ease;
    }
    @keyframes otpSlideIn {
      from { opacity:0; transform:translateY(-18px) scale(.97); }
      to   { opacity:1; transform:translateY(0)     scale(1);   }
    }
    .otp-modal h3 { margin: 0 0 6px; font-size: 20px; color: #0f172a; }
    .otp-modal > p { margin: 0 0 20px; font-size: 14px; color: #64748b; }

    /* Labelled password wrapper — matches lf-wrap style of index.html */
    .otp-pw-wrap {
      display: flex; align-items: center;
      border: 1.5px solid #e2e8f0; border-radius: 8px;
      padding: 2px 10px 2px 12px; margin-bottom: 12px;
      background: #fff; transition: border-color .2s;
    }
    .otp-pw-wrap:focus-within { border-color: #06b6d4; }
    .otp-pw-wrap i.fa-lock { color: #4f8ef7; margin-right: 8px; font-size: 14px; flex-shrink:0; }
    .otp-pw-wrap input {
      flex:1; border:none; outline:none; padding:8px 0;
      font-size:14px; background:none; color:#0f172a;
      letter-spacing:.06em; margin-bottom:0;
    }
    .otp-eye-btn {
      background:none; border:none; cursor:pointer;
      padding:0 4px; color:#aaa; flex-shrink:0;
      display:flex; align-items:center; transition:color .2s;
    }
    .otp-eye-btn:hover { color:#06b6d4; }

    /* Plain inputs (email/ID, OTP code) */
    .otp-plain {
      width:100%; padding:10px 14px; border:1.5px solid #e2e8f0;
      border-radius:8px; font-size:14px; margin-bottom:12px;
      box-sizing:border-box; outline:none; color:#0f172a;
    }
    .otp-plain:focus { border-color:#06b6d4; }

    /* Buttons */
    .otp-btn-primary, .otp-btn-secondary {
      width:100%; padding:11px; border-radius:8px; border:none;
      cursor:pointer; font-size:14px; font-weight:600; margin-bottom:8px;
      transition: background .15s;
    }
    .otp-btn-primary  { background:#06b6d4; color:#fff; }
    .otp-btn-primary:hover  { background:#0891b2; }
    .otp-btn-secondary { background:#f1f5f9; color:#475569; }
    .otp-btn-secondary:hover { background:#e2e8f0; }

    .otp-error   { color:#dc2626; font-size:13px; margin-bottom:10px; display:none; }
    .otp-success { color:#16a34a; font-size:13px; margin-bottom:10px; display:none; }
    .otp-hint    {
      font-size:12px; color:#94a3b8; margin:0 0 12px; line-height:1.5;
    }
    .otp-field-label {
      font-size:13px; color:#475569; font-weight:600;
      display:block; margin-bottom:5px;
    }

    /* Forgot-password link — one per portal, hidden until 3 failures */
    .forgot-pwd-link {
      display:none; color:#06b6d4; font-size:13px; cursor:pointer;
      text-decoration:underline; margin-top:8px; text-align:center;
      padding: 4px 0;
    }
    .forgot-pwd-link:hover { color:#0891b2; }

    #employee-id-row { display: none; }
  `;
  document.head.appendChild(style);

  // ── Helper: password field with eye toggle ────────────────
  function buildPasswordField(id, placeholder) {
    const wrapper = document.createElement('div');
    wrapper.className = 'otp-pw-wrap';

    const icon = document.createElement('i');
    icon.className = 'fa fa-lock';
    wrapper.appendChild(icon);

    const input = document.createElement('input');
    input.id           = id;
    input.type         = 'password';
    input.placeholder  = placeholder;
    input.autocomplete = 'new-password';
    wrapper.appendChild(input);

    const eyeBtn = document.createElement('button');
    eyeBtn.type      = 'button';
    eyeBtn.className = 'otp-eye-btn';
    eyeBtn.title     = 'Toggle password visibility';
    eyeBtn.innerHTML = '<i class="fa fa-eye"></i>';
    eyeBtn.addEventListener('click', function () {
      const hidden = input.type === 'password';
      input.type = hidden ? 'text' : 'password';
      eyeBtn.querySelector('i').className = hidden ? 'fa fa-eye-slash' : 'fa fa-eye';
    });
    wrapper.appendChild(eyeBtn);

    return { wrapper, input };
  }

  // ── Helper: show message ──────────────────────────────────
  function showMsg(el, text, type) {
    el.textContent   = text;
    el.className     = type === 'error' ? 'otp-error' : 'otp-success';
    el.style.display = 'block';
  }

  // ── Patch fetch to count login failures per portal ────────
  const _origFetch = window.fetch.bind(window);
  window.fetch = async function (url, opts) {
    const res = await _origFetch(url, opts);

    if (typeof url === 'string' && url.includes('/api/login')) {
      // Detect which portal is currently active
      const facPortalVisible = !document.getElementById('facultyPortal')
                                         ?.classList.contains('dn');

      if (res.status === 401) {
        if (facPortalVisible) {
          // FIX-B: count faculty failures separately
          _facFailCount++;
          if (_facFailCount >= 3) {
            const lnk = document.getElementById('forgot-pwd-link-fac');
            if (lnk) lnk.style.display = 'block';
          }
        } else {
          _adminFailCount++;
          if (_adminFailCount >= 3) {
            const lnk = document.getElementById('forgot-pwd-link-admin');
            if (lnk) lnk.style.display = 'block';
          }
        }
      }

      if (res.ok) {
        if (facPortalVisible) { _facFailCount = 0; }
        else                  { _adminFailCount = 0; }

        const clone = res.clone();
        const data  = await clone.json().catch(() => ({}));
        if (data.must_reset_pwd) {
          setTimeout(() => showForceChangeModal(data.token), 300);
        }
      }
    }
    return res;
  };

  // ── Inject forgot-password links on DOM ready ─────────────
  // FIX-A: inject ONE link per portal, each independently shown/hidden.
  document.addEventListener('DOMContentLoaded', () => {

    // --- Admin portal forgot link ---
    const adminSignInBtn = document.querySelector(
      '#adminPortal .btn-signin, #adminPortal button[onclick*="loginAdmin"]'
    );
    if (adminSignInBtn) {
      const lnk = document.createElement('div');
      lnk.id        = 'forgot-pwd-link-admin';
      lnk.className = 'forgot-pwd-link';
      lnk.innerHTML = '<i class="fa fa-key"></i> Forgot Password?';
      lnk.addEventListener('click', () => showOTPModal('admin'));
      adminSignInBtn.parentNode.insertBefore(lnk, adminSignInBtn.nextSibling);
    }

    // --- Faculty portal forgot link ---
    const facSignInBtn = document.querySelector(
      '#facultyPortal .btn-signin, #facultyPortal button[onclick*="loginFaculty"]'
    );
    if (facSignInBtn) {
      const lnk = document.createElement('div');
      lnk.id        = 'forgot-pwd-link-fac';
      lnk.className = 'forgot-pwd-link';
      lnk.innerHTML = '<i class="fa fa-key"></i> Forgot Password?';
      lnk.addEventListener('click', () => showOTPModal('faculty'));
      facSignInBtn.parentNode.insertBefore(lnk, facSignInBtn.nextSibling);
    }

    // Legacy single-id support (if patch is used elsewhere)
    const legacyLink = document.getElementById('forgot-pwd-link');
    if (legacyLink) {
      legacyLink.addEventListener('click', () => showOTPModal('admin'));
    }
  });

  // ── Resolve Faculty ID → email via backend ────────────────
  // FIX-C: faculty uses fac_id for login but forgot-password needs email.
  // GET /api/faculty/<fac_id> returns the faculty record including email.
  async function resolveFacultyEmail(facIdOrEmail) {
    const val = facIdOrEmail.trim();

    // If it already looks like an email just return it
    if (val.includes('@')) return { ok: true, email: val.toLowerCase() };

    // Otherwise treat as faculty ID
    const id = val.toUpperCase();
    try {
      const res  = await _origFetch(`${API}/api/faculty/${id}`);
      if (!res.ok) return { ok: false, error: `Faculty ID "${id}" not found. Check your ID or enter your registered email.` };
      const data = await res.json();
      const email = (data.email || data.fac_email || '').trim().toLowerCase();
      if (!email) return { ok: false, error: `No email address is registered for Faculty ID "${id}". Contact Admin to update your email first.` };
      return { ok: true, email };
    } catch (e) {
      return { ok: false, error: 'Network error while looking up Faculty ID.' };
    }
  }

  // ── OTP Modal entry point ─────────────────────────────────
  function showOTPModal(portal) {
    const overlay = document.createElement('div');
    overlay.className = 'otp-modal-overlay';
    const modal = document.createElement('div');
    modal.className = 'otp-modal';
    overlay.appendChild(modal);
    document.body.appendChild(overlay);

    // Close on backdrop click
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) overlay.remove();
    });

    showStep1(modal, overlay, portal || 'admin');
  }

  // ── Step 1: Enter email / Faculty ID ─────────────────────
  function showStep1(modal, overlay, portal) {
    const isFaculty  = portal === 'faculty';
    const inputLabel = isFaculty
      ? 'Faculty ID or Registered Email'
      : 'Registered Email Address';
    const placeholder = isFaculty
      ? 'e.g. FAC001  or  your@email.com'
      : 'your@email.com';
    const hint = isFaculty
      ? '<i class="fa fa-circle-info"></i> Enter your Faculty ID (e.g. FAC001) or your registered email'
      : '<i class="fa fa-circle-info"></i> Enter the email address linked to your account';

    modal.innerHTML = `
      <h3><i class="fa fa-key" style="color:#06b6d4;margin-right:8px;"></i>Reset Password</h3>
      <p>We'll send a 6-digit OTP to your registered email.</p>
      <label class="otp-field-label">${inputLabel}</label>
      <input id="otp-email-input" class="otp-plain"
             type="text" placeholder="${placeholder}" autocomplete="email" />
      <p class="otp-hint">${hint}</p>
      <div id="otp-msg" class="otp-error"></div>
      <button class="otp-btn-primary"   id="otp-send-btn">
        <i class="fa fa-paper-plane"></i> Send OTP
      </button>
      <button class="otp-btn-secondary" id="otp-cancel-btn">Cancel</button>
    `;

    modal.querySelector('#otp-cancel-btn').addEventListener('click', () => overlay.remove());

    modal.querySelector('#otp-send-btn').addEventListener('click', async () => {
      const raw = modal.querySelector('#otp-email-input').value.trim();
      const msg = modal.querySelector('#otp-msg');
      if (!raw) { showMsg(msg, 'Please enter your ' + (isFaculty ? 'Faculty ID or email.' : 'email address.'), 'error'); return; }

      const btn = modal.querySelector('#otp-send-btn');
      btn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Sending…'; btn.disabled = true;

      // FIX-C: resolve Faculty ID → email before hitting the API
      let email = raw;
      if (isFaculty) {
        const resolved = await resolveFacultyEmail(raw);
        if (!resolved.ok) {
          showMsg(msg, resolved.error, 'error');
          btn.innerHTML = '<i class="fa fa-paper-plane"></i> Send OTP'; btn.disabled = false;
          return;
        }
        email = resolved.email;
      }

      try {
        const res  = await _origFetch(`${API}/api/auth/forgot-password`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ email }),
        });
        const data = await res.json();
        if (!res.ok) { showMsg(msg, data.detail || 'Failed. Please try again.', 'error'); }
        else         { showStep2(modal, overlay, email); }
      } catch (e) {
        showMsg(msg, 'Network error. Check your connection.', 'error');
      } finally {
        const b = modal.querySelector('#otp-send-btn');
        if (b) { b.innerHTML = '<i class="fa fa-paper-plane"></i> Send OTP'; b.disabled = false; }
      }
    });
  }

  // ── Step 2: Enter OTP ─────────────────────────────────────
  function showStep2(modal, overlay, email) {
    const masked = email.replace(/(.{2}).+(@.+)/, '$1…$2');
    modal.innerHTML = `
      <h3><i class="fa fa-envelope-open-text" style="color:#06b6d4;margin-right:8px;"></i>Enter OTP</h3>
      <p>A 6-digit code was sent to <strong>${masked}</strong>.<br>It expires in <strong>10 minutes</strong>.</p>
      <label class="otp-field-label">6-Digit OTP Code</label>
      <input id="otp-code" class="otp-plain" type="text" maxlength="6"
             placeholder="1 2 3 4 5 6"
             inputmode="numeric" pattern="[0-9]{6}"
             autocomplete="one-time-code"
             style="font-size:22px;letter-spacing:10px;text-align:center;font-weight:700;" />
      <div id="otp-msg" class="otp-error"></div>
      <button class="otp-btn-primary"   id="otp-verify-btn">
        <i class="fa fa-check"></i> Verify OTP
      </button>
      <button class="otp-btn-secondary" id="otp-resend-btn">
        <i class="fa fa-rotate-right"></i> Resend OTP
      </button>
      <button class="otp-btn-secondary" id="otp-back-btn">
        <i class="fa fa-arrow-left"></i> Back
      </button>
    `;

    // Auto-focus OTP field
    setTimeout(() => modal.querySelector('#otp-code')?.focus(), 80);

    // Only allow digits
    modal.querySelector('#otp-code').addEventListener('input', function () {
      this.value = this.value.replace(/\D/g, '').slice(0, 6);
    });

    modal.querySelector('#otp-back-btn').addEventListener('click',
      () => showStep1(modal, overlay, email.includes('@') ? 'admin' : 'faculty'));
    modal.querySelector('#otp-resend-btn').addEventListener('click',
      () => showStep1(modal, overlay, email.includes('@') ? 'admin' : 'faculty'));

    modal.querySelector('#otp-verify-btn').addEventListener('click', async () => {
      const code = modal.querySelector('#otp-code').value.trim();
      const msg  = modal.querySelector('#otp-msg');
      if (!/^\d{6}$/.test(code)) { showMsg(msg, 'Please enter the 6-digit code.', 'error'); return; }

      const btn = modal.querySelector('#otp-verify-btn');
      btn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Verifying…'; btn.disabled = true;

      try {
        const res  = await _origFetch(`${API}/api/auth/verify-otp`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ email, otp: code }),
        });
        const data = await res.json();
        if (!res.ok) { showMsg(msg, data.detail || 'Invalid OTP.', 'error'); }
        else         { showStep3(modal, overlay, email); }
      } catch (e) {
        showMsg(msg, 'Network error.', 'error');
      } finally {
        const b = modal.querySelector('#otp-verify-btn');
        if (b) { b.innerHTML = '<i class="fa fa-check"></i> Verify OTP'; b.disabled = false; }
      }
    });
  }

  // ── Step 3: Set new password with eye toggles ─────────────
  function showStep3(modal, overlay, email) {
    modal.innerHTML = `
      <h3><i class="fa fa-lock" style="color:#06b6d4;margin-right:8px;"></i>Set New Password</h3>
      <p>OTP verified ✓ &nbsp;Choose a strong new password.</p>
    `;

    const lbl1 = document.createElement('label');
    lbl1.className = 'otp-field-label'; lbl1.textContent = 'New Password';
    modal.appendChild(lbl1);
    const { wrapper: w1, input: i1 } = buildPasswordField('otp-newpwd', 'Min 8 chars, upper, number, symbol');
    modal.appendChild(w1);

    const lbl2 = document.createElement('label');
    lbl2.className = 'otp-field-label'; lbl2.textContent = 'Confirm Password';
    modal.appendChild(lbl2);
    const { wrapper: w2, input: i2 } = buildPasswordField('otp-newpwd2', 'Re-enter new password');
    modal.appendChild(w2);

    const hint = document.createElement('p');
    hint.className = 'otp-hint';
    hint.innerHTML = '<i class="fa fa-circle-info"></i> Must have: uppercase · lowercase · number · special character';
    modal.appendChild(hint);

    const msgDiv = document.createElement('div');
    msgDiv.id = 'otp-msg'; msgDiv.className = 'otp-error';
    modal.appendChild(msgDiv);

    const resetBtn = document.createElement('button');
    resetBtn.className = 'otp-btn-primary'; resetBtn.id = 'otp-reset-btn';
    resetBtn.innerHTML = '<i class="fa fa-floppy-disk"></i> Reset Password';
    modal.appendChild(resetBtn);

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'otp-btn-secondary';
    cancelBtn.innerHTML = '<i class="fa fa-xmark"></i> Cancel';
    modal.appendChild(cancelBtn);

    cancelBtn.addEventListener('click', () => overlay.remove());

    resetBtn.addEventListener('click', async () => {
      const p1  = i1.value;
      const p2  = i2.value;
      const msg = msgDiv;

      if (!p1)              { showMsg(msg, 'Please enter a new password.', 'error'); return; }
      if (p1 !== p2)        { showMsg(msg, 'Passwords do not match.', 'error'); return; }
      if (p1.length < 8)    { showMsg(msg, 'Password must be at least 8 characters.', 'error'); return; }
      if (!/[A-Z]/.test(p1)){ showMsg(msg, 'Must contain at least one uppercase letter (A-Z).', 'error'); return; }
      if (!/[a-z]/.test(p1)){ showMsg(msg, 'Must contain at least one lowercase letter (a-z).', 'error'); return; }
      if (!/\d/.test(p1))   { showMsg(msg, 'Must contain at least one number (0-9).', 'error'); return; }
      if (!/[!@#$%^&*()\-_=+\[\]{};:'",.<>/?\\|`~^]/.test(p1)) {
        showMsg(msg, 'Must contain at least one special character (e.g. @, #, !).', 'error'); return;
      }

      resetBtn.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Resetting…'; resetBtn.disabled = true;

      try {
        const res  = await _origFetch(`${API}/api/auth/reset-password`, {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({ email, new_password: p1 }),
        });
        const data = await res.json();
        if (!res.ok) { showMsg(msg, data.detail || 'Reset failed. Please try again.', 'error'); }
        else         { showSuccess(modal, overlay, data.message || 'Password reset successfully!'); }
      } catch (e) {
        showMsg(msg, 'Network error. Check your connection.', 'error');
      } finally {
        const b = modal.querySelector('#otp-reset-btn');
        if (b) { b.innerHTML = '<i class="fa fa-floppy-disk"></i> Reset Password'; b.disabled = false; }
      }
    });
  }

  // ── Success screen ────────────────────────────────────────
  function showSuccess(modal, overlay, message) {
    modal.innerHTML = `
      <h3 style="color:#16a34a;">
        <i class="fa fa-circle-check" style="margin-right:8px;"></i>Done!
      </h3>
      <p>${message}</p>
      <p>You can now log in with your new password.</p>
      <button class="otp-btn-primary" id="otp-done-btn">
        <i class="fa fa-right-to-bracket"></i> Go to Login
      </button>
    `;
    modal.querySelector('#otp-done-btn').addEventListener('click', () => {
      overlay.remove();
      _adminFailCount = 0; _facFailCount = 0;
      const la = document.getElementById('forgot-pwd-link-admin');
      const lf = document.getElementById('forgot-pwd-link-fac');
      if (la) la.style.display = 'none';
      if (lf) lf.style.display = 'none';
    });
  }

  // ── Force-change modal (first login) with eye toggles ─────
  function showForceChangeModal(token) {
    const overlay = document.createElement('div');
    overlay.className = 'otp-modal-overlay';
    const modal = document.createElement('div');
    modal.className = 'otp-modal';
    overlay.appendChild(modal); document.body.appendChild(overlay);

    const h3 = document.createElement('h3');
    h3.innerHTML = '<i class="fa fa-lock" style="color:#06b6d4;margin-right:8px;"></i>Change Your Password';
    modal.appendChild(h3);

    const desc = document.createElement('p');
    desc.textContent = 'Your account requires a password change before you can continue.';
    modal.appendChild(desc);

    const lbl1 = document.createElement('label');
    lbl1.className = 'otp-field-label'; lbl1.textContent = 'New Password';
    modal.appendChild(lbl1);
    const { wrapper: fw1, input: fi1 } = buildPasswordField('fc-pwd', 'New password');
    modal.appendChild(fw1);

    const lbl2 = document.createElement('label');
    lbl2.className = 'otp-field-label'; lbl2.textContent = 'Confirm Password';
    modal.appendChild(lbl2);
    const { wrapper: fw2, input: fi2 } = buildPasswordField('fc-pwd2', 'Confirm new password');
    modal.appendChild(fw2);

    const fhint = document.createElement('p');
    fhint.className = 'otp-hint';
    fhint.innerHTML = '<i class="fa fa-circle-info"></i> Must have: uppercase · lowercase · number · special character';
    modal.appendChild(fhint);

    const fmsg = document.createElement('div');
    fmsg.id = 'fc-msg'; fmsg.className = 'otp-error';
    modal.appendChild(fmsg);

    const fsubmit = document.createElement('button');
    fsubmit.className = 'otp-btn-primary'; fsubmit.id = 'fc-submit-btn';
    fsubmit.innerHTML = '<i class="fa fa-floppy-disk"></i> Update Password';
    modal.appendChild(fsubmit);

    fsubmit.addEventListener('click', async () => {
      const p1 = fi1.value, p2 = fi2.value, msg = fmsg;
      if (!p1)              { showMsg(msg, 'Please enter a new password.', 'error'); return; }
      if (p1 !== p2)        { showMsg(msg, 'Passwords do not match.', 'error'); return; }
      if (p1.length < 8)    { showMsg(msg, 'Minimum 8 characters required.', 'error'); return; }
      if (!/[A-Z]/.test(p1)){ showMsg(msg, 'Must contain at least one uppercase letter.', 'error'); return; }
      if (!/[a-z]/.test(p1)){ showMsg(msg, 'Must contain at least one lowercase letter.', 'error'); return; }
      if (!/\d/.test(p1))   { showMsg(msg, 'Must contain at least one number.', 'error'); return; }
      if (!/[!@#$%^&*()\-_=+\[\]{};:'",.<>/?\\|`~^]/.test(p1)) {
        showMsg(msg, 'Must contain at least one special character (e.g. @, #, !).', 'error'); return;
      }
      fsubmit.innerHTML = '<i class="fa fa-spinner fa-spin"></i> Updating…'; fsubmit.disabled = true;
      try {
        const res = await _origFetch(`${API}/api/auth/change-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
          body: JSON.stringify({ new_password: p1 }),
        });
        const data = await res.json();
        if (!res.ok) { showMsg(msg, data.detail || 'Failed. Please try again.', 'error'); }
        else         { overlay.remove(); }
      } catch (e) {
        showMsg(msg, 'Network error.', 'error');
      } finally {
        fsubmit.innerHTML = '<i class="fa fa-floppy-disk"></i> Update Password'; fsubmit.disabled = false;
      }
    });
  }

  // Expose globally
  window.showForgotPasswordModal        = showOTPModal;
  window.showForgotPasswordModalFaculty = () => showOTPModal('faculty');
  window.showForgotPasswordModalAdmin   = () => showOTPModal('admin');

  console.log('[E Auth Merge] OTP patch loaded ✓ (faculty forgot-password fix)');
})();