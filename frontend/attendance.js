

// =============================================================
// attendance.js  —  EduTrack Pro  Role-Based Attendance v10.2
//
// FIXES v10.2:
//   1. Auth token auto-read from sessionStorage → no more 401
//   2. Uses /api/role/session/* (public endpoints, no auth)
//   3. Staff → dept + staff-member dropdown from API
//   4. HOD   → dept + HOD dropdown from API
//   5. Camera MJPEG stream works after session start
//   6. Model-missing shows clear toast + guidance
// =============================================================
'use strict';

/* ── Bootstrap helpers ──────────────────────────────────────── */
(function _bootstrap() {
  var isLiveServer = ['5500','5501','5502'].includes(location.port);
  var API_BASE     = isLiveServer ? 'http://127.0.0.1:8000' : '';

  // FIX 1: Always (re-)define apiFetch — never short-circuit with ||.
  // On SPA re-navigation the IIFE runs again; the old closure captured a
  // stale API_BASE. Unconditional assignment guarantees a fresh closure.
  window.apiFetch = async function apiFetch(path, opts) {
    opts = opts || {};
    var token = sessionStorage.getItem('_token') || '';
    var auth  = token ? { Authorization: 'Bearer ' + token } : {};
    var res   = await fetch(API_BASE + path, {
      headers: Object.assign({'Content-Type':'application/json'}, auth, opts.headers||{}),
      body: opts.body, method: opts.method || 'GET',
    });
    if (!res.ok) {
      var msg = res.statusText;
      try { var j = await res.json(); msg = j.detail || j.message || msg; } catch(_){}
      throw new Error(msg);
    }
    return res.json();
  };

  
  window.api = Object.assign(window.api || {}, {
    sessionStart:  function(p){ return apiFetch('/api/role/session/start',{method:'POST',body:JSON.stringify(p)}); },
    sessionStop:   function(){ return apiFetch('/api/role/session/stop',{method:'POST',body:'{}'}); },
    sessionStatus: function(){ return apiFetch('/api/role/session/status'); },
    staffByDept:   function(d){ return apiFetch('/api/staff/by-dept?dept='+encodeURIComponent(d)); },
    hodByDept:     function(d){ return apiFetch('/api/hod/by-dept?dept='+encodeURIComponent(d)); },
    enrollCounts:  function(){ return apiFetch('/api/enrollment/counts'); },
    timetable:     function(){ return apiFetch('/api/timetable'); },
    exportCsv:     function(){ return fetch(API_BASE+'/api/attendance/export/csv'); },
    deptCourses:   function(dept){ return apiFetch('/api/departments/'+encodeURIComponent(dept)+'/courses'); },
    courseYears:   function(dept,c){ return apiFetch('/api/departments/'+encodeURIComponent(dept)+'/courses/'+encodeURIComponent(c)+'/years'); },
    yearSections:  function(dept,c,yr){ return apiFetch('/api/departments/'+encodeURIComponent(dept)+'/courses/'+encodeURIComponent(c)+'/years/'+encodeURIComponent(yr)+'/sections'); },
  });

  window._ATT_API_BASE = API_BASE;
  if (typeof window.toast !== 'function') window.toast = function(m,t){ console.warn('[toast]',t,m); };
})();

/* ── State ───────────────────────────────────────────────────── */
var ATT = { role:null, active:false, pollTimer:null, stepTimer:null, stepIdx:0, seenIds:new Set(), _shownModelWarning:false, _lastDetectKey:null };

var ATT_DEPTS = [
  {key:'CSE',name:'CSE – Computer Science & Engineering'},
  {key:'AIDS',name:'AIDS – AI & Data Science'},
  {key:'IT',name:'IT – Information Technology'},
  {key:'CSBS',name:'CSBS – CS & Business Systems'},
  {key:'ECE',name:'ECE – Electronics & Communication'},
  {key:'EEE',name:'EEE – Electrical & Electronics'},
  {key:'BM',name:'BM – Bio Medical Engineering'},
  {key:'MECH',name:'MECH – Mechanical Engineering'},
  {key:'CIVIL',name:'CIVIL – Civil Engineering'},
];

var ROLE_CFG = {
  student:{label:'Student',icon:'fa-user-graduate',cardCls:'rc-active-student',iconCls:'rc-icon-student',
    idleHint:'Select Dept → Course → Year → Semester → Section, then Start',
    filters:['dept','course','year','semester','section']},
  staff:{label:'Staff / Faculty',icon:'fa-chalkboard-teacher',cardCls:'rc-active-staff',iconCls:'rc-icon-staff',
    idleHint:'Select Department → Staff Member, then click Start Session',
    filters:['dept','staff_member','semester']},
  hod:{label:'HOD',icon:'fa-user-tie',cardCls:'rc-active-hod',iconCls:'rc-icon-hod',
    idleHint:'Select Department → HOD, then click Start Session',
    filters:['dept','hod_member','semester']},
};

var STEP_IDS = ['step-camera','step-detect','step-match','step-db','step-mark'];

/* ═══════════════ ROLE SELECTION ════════════════════════════ */
function attSelectRole(role) {
  if (ATT.active) { attToast('Stop the active session first.','warn'); return; }
  ATT.role = role;
  ['student','staff','hod'].forEach(function(r){
    var c=document.getElementById('roleCard-'+r), k=document.getElementById('rcCheck-'+r);
    if(c) c.classList.remove('rc-active-student','rc-active-staff','rc-active-hod');
    if(k) k.classList.add('dn');
  });
  var card=document.getElementById('roleCard-'+role), chk=document.getElementById('rcCheck-'+role);
  if(card) card.classList.add(ROLE_CFG[role].cardCls);
  if(chk)  chk.classList.remove('dn');
  _show('sectionFilters');
  _setEl('filterSectionTitle', ROLE_CFG[role].label+' Attendance Filters');
  var fi=document.getElementById('frsIcon'), fl=document.getElementById('frsLabel');
  if(fi) fi.innerHTML='<i class="fa '+ROLE_CFG[role].icon+'"></i>';
  if(fl) fl.textContent=ROLE_CFG[role].label+' Attendance';
  _buildFilters(role);
  var hint=document.getElementById('camIdleHint'); if(hint) hint.textContent=ROLE_CFG[role].idleHint;
  _setEl('chipRole',ROLE_CFG[role].label);
  var sec=document.getElementById('sectionFilters'); if(sec) sec.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function attResetRole() {
  if (ATT.active) { attToast('Stop the active session first.','warn'); return; }
  ATT.role=null;
  ['student','staff','hod'].forEach(function(r){
    var c=document.getElementById('roleCard-'+r),k=document.getElementById('rcCheck-'+r);
    if(c) c.classList.remove('rc-active-student','rc-active-staff','rc-active-hod');
    if(k) k.classList.add('dn');
  });
  _hide('sectionFilters'); _setEl('chipRole','No role'); _setEl('chipDept','—');
}

/* ═══════════════ FILTER BUILDER ════════════════════════════ */
function _deptOpts(){
  return ATT_DEPTS.map(function(d){ return '<option value="'+d.key+'">'+d.name+'</option>'; }).join('');
}

function _buildFilters(role) {
  var container=document.getElementById('fpFields'); if(!container) return;
  container.innerHTML='';
  ROLE_CFG[role].filters.forEach(function(f){
    var div=document.createElement('div'); div.className='fp-field';
    if(f==='dept'){
      div.innerHTML='<label class="fp-label"><i class="fa fa-building-columns"></i> Department</label>'+
        '<select class="fp-select" id="attDept" onchange="attOnDeptChange()">'+
        '<option value="">— Select Department —</option>'+_deptOpts()+'</select>';
    } else if(f==='course'){
      div.innerHTML='<label class="fp-label"><i class="fa fa-book-open"></i> Course</label>'+
        '<select class="fp-select" id="attCourse" onchange="attOnCourseChange()" disabled>'+
        '<option value="">Select Department first</option></select>';
    } else if(f==='year'){
      div.innerHTML='<label class="fp-label"><i class="fa fa-calendar-days"></i> Year</label>'+
        '<select class="fp-select" id="attYear" onchange="attOnYearChange()" disabled>'+
        '<option value="">Select Course first</option></select>';
    } else if(f==='semester'){
      var so=[1,2,3,4,5,6,7,8].map(function(n){ return '<option value="'+n+'">Semester '+n+'</option>'; }).join('');
      div.innerHTML='<label class="fp-label"><i class="fa fa-layer-group"></i> Semester</label>'+
        '<select class="fp-select" id="attSemester"><option value="">— Select Semester —</option>'+so+'</select>';
    } else if(f==='section'){
      div.innerHTML='<label class="fp-label"><i class="fa fa-users"></i> Section</label>'+
        '<select class="fp-select" id="attSection" disabled>'+
        '<option value="">Select Year first</option></select>';
    } else if(f==='staff_member'){
      div.innerHTML='<label class="fp-label"><i class="fa fa-id-badge"></i> Staff Member '+
        '<span class="fp-optional">(optional — blank = all dept)</span></label>'+
        '<select class="fp-select" id="attStaffId">'+
        '<option value="">— Select Department first —</option></select>';
    } else if(f==='hod_member'){
      div.innerHTML='<label class="fp-label"><i class="fa fa-user-tie"></i> HOD</label>'+
        '<select class="fp-select" id="attHodId">'+
        '<option value="">— Select Department first —</option></select>';
    }
    container.appendChild(div);
  });
}

async function attOnDeptChange() {
  var dept=(document.getElementById('attDept')||{}).value||'';
  if(dept) _setEl('chipDept',dept);
  ['attCourse','attYear','attSection'].forEach(function(id){
    var el=document.getElementById(id); if(!el) return;
    el.innerHTML='<option value="">Select above first</option>'; el.disabled=true;
  });
  if(!dept) return;

  /* Student: courses */
  var cSel=document.getElementById('attCourse');
  if(cSel){
    cSel.innerHTML='<option value="">Loading…</option>'; cSel.disabled=true;
    try{
      var d=await api.deptCourses(dept);
      var cs=d.courses||[];
      cSel.innerHTML='<option value="">— Select Course —</option>'+
        cs.map(function(c){
          var k=c.key||c.course_key||c.course_code||c, n=c.name||c.course_name||k;
          return '<option value="'+_esc(k)+'">'+_esc(n+' ('+k+')')+'</option>';
        }).join('');
      cSel.disabled=false;
    } catch(e){ cSel.innerHTML='<option value="">Failed to load</option>'; attToast('Courses: '+e.message,'warn'); }
  }

  /* Staff dropdown */
  var sSel=document.getElementById('attStaffId');
  if(sSel){
    sSel.innerHTML='<option value="">Loading staff…</option>';
    try{
      var sd=await api.staffByDept(dept);
      var sl=sd.staff||[];
      sSel.innerHTML='<option value="">All Staff in '+_esc(dept)+'</option>'+
        sl.map(function(s){ return '<option value="'+_esc(s.fac_id)+'">'+_esc(s.name+' ('+s.fac_id+')')+'</option>'; }).join('');
      if(!sl.length){ sSel.innerHTML='<option value="">No staff enrolled in '+_esc(dept)+'</option>'; attToast('No staff enrolled in '+dept+'. Enrol staff first.','warn'); }
    } catch(e){ sSel.innerHTML='<option value="">Could not load staff</option>'; }
  }

  /* HOD dropdown */
  var hSel=document.getElementById('attHodId');
  if(hSel){
    hSel.innerHTML='<option value="">Loading HODs…</option>';
    try{
      var hd=await api.hodByDept(dept);
      var hl=hd.hods||[];
      hSel.innerHTML='<option value="">— Select HOD —</option>'+
        hl.map(function(h){ return '<option value="'+_esc(h.hod_id)+'">'+_esc(h.name+' ('+h.hod_id+')')+'</option>'; }).join('');
      if(!hl.length){ hSel.innerHTML='<option value="">No HODs enrolled in '+_esc(dept)+'</option>'; attToast('No HODs enrolled in '+dept+'. Enrol HOD first.','warn'); }
    } catch(e){ hSel.innerHTML='<option value="">Could not load HODs</option>'; }
  }
}

async function attOnCourseChange() {
  var dept=(document.getElementById('attDept')||{}).value||'';
  var course=(document.getElementById('attCourse')||{}).value||'';
  ['attYear','attSection'].forEach(function(id){ var el=document.getElementById(id); if(!el) return; el.innerHTML='<option value="">Select above first</option>'; el.disabled=true; });
  if(!dept||!course) return;
  var ySel=document.getElementById('attYear'); if(!ySel) return;
  ySel.innerHTML='<option value="">Loading years…</option>';
  try{
    var d=await api.courseYears(dept,course);
    var YL={I:'I Year',II:'II Year',III:'III Year',IV:'IV Year'};
    ySel.innerHTML='<option value="">— Select Year —</option>'+
      (d.years||[]).map(function(yr){ return '<option value="'+_esc(yr)+'">'+_esc(YL[yr]||yr+' Year')+'</option>'; }).join('');
    ySel.disabled=false;
  } catch(e){ ySel.innerHTML='<option value="">Failed to load years</option>'; attToast('Years: '+e.message,'warn'); }
}

async function attOnYearChange() {
  var dept=(document.getElementById('attDept')||{}).value||'';
  var course=(document.getElementById('attCourse')||{}).value||'';
  var year=(document.getElementById('attYear')||{}).value||'';
  var sSel=document.getElementById('attSection');
  if(sSel){ sSel.innerHTML='<option value="">Loading sections…</option>'; sSel.disabled=true; }
  if(!dept||!course||!year||!sSel) return;

  // FIX 3: Guard against yearSections being missing (script-load race / stale api object).
  // This was the visible symptom: "api.yearSections is not a function".
  if(typeof api.yearSections !== 'function'){
    console.error('[EduTrack] api.yearSections is not a function — api object:', api);
    sSel.innerHTML='<option value="">Error: api not ready. Reload the page.</option>';
    attToast('Sections: api not initialised. Please reload the page.','warn');
    return;
  }

  try{
    var d=await api.yearSections(dept,course,year);
    sSel.innerHTML='<option value="">— Select Section —</option>'+
      (d.sections||[]).map(function(s){ var sec=s.section||s; return '<option value="'+_esc(sec)+'">Section '+_esc(sec)+'</option>'; }).join('');
    sSel.disabled=false;
  } catch(e){ sSel.innerHTML='<option value="">Failed to load sections</option>'; attToast('Sections: '+e.message,'warn'); }
}

/* ═══════════════ VALIDATION ════════════════════════════════ */
function _validateAndBuildPayload() {
  var role=ATT.role;
  if(!role) return {ok:false,err:'Please select a role first.'};
  var dept=(document.getElementById('attDept')||{}).value||'';
  var course=(document.getElementById('attCourse')||{}).value||'';
  var year=(document.getElementById('attYear')||{}).value||'';
  var semester=(document.getElementById('attSemester')||{}).value||'';
  var section=(document.getElementById('attSection')||{}).value||'';
  var staffId=(document.getElementById('attStaffId')||{}).value||'';
  var hodId=(document.getElementById('attHodId')||{}).value||'';
  if(!dept) return {ok:false,err:'Please select a Department.'};
  if(role==='student'){
    if(!course) return {ok:false,err:'Please select a Course.'};
    if(!year)   return {ok:false,err:'Please select a Year.'};
    if(!section) return {ok:false,err:'Please select a Section.'};
  }
  if(role==='hod'&&!hodId) return {ok:false,err:'Please select the HOD.'};
  return {ok:true,dept:dept,payload:{role:role,dept:dept,course:course,year:year,semester:semester,section:section,staff_id:staffId,hod_id:hodId,period:''}};
}

/* ═══════════════ START / STOP ══════════════════════════════ */
async function startAttendance() {
  var res=_validateAndBuildPayload();
  if(!res.ok){ attToast(res.err,'warn'); return; }
  var btnStart=document.getElementById('btnStart'), btnStop=document.getElementById('btnStop');
  if(btnStart){ btnStart.disabled=true; var l=btnStart.querySelector('.scb-label'),i=btnStart.querySelector('.scb-icon-wrap'); if(l) l.textContent='Starting…'; if(i) i.innerHTML='<i class="fa fa-spinner fa-spin"></i>'; }
  try{
    await api.sessionStart(res.payload);
    var vsrc=(window._ATT_API_BASE||'')+'/video_feed?'+Date.now();
    var img=document.getElementById('mjpegImg');
    var cw=document.getElementById('camWrap'), ci=document.getElementById('camIdle'), co=document.getElementById('camOverlay');
    if(cw) cw.classList.add('cam-live'); if(ci) ci.classList.add('dn'); if(co) co.classList.remove('dn');
    if(img){
      // Keep img hidden until the first frame arrives to avoid a blank-frame onerror flash.
      img.classList.add('dn');
      img.onload = function(){
        img.classList.remove('dn'); // reveal only once the stream delivers a frame
        img.onload = null;          // one-time handler
      };
      img.src = vsrc;
    }
    var cs=document.getElementById('camScanLabel'); if(cs) cs.textContent='Scanning…';
    _show('camBadgeLive'); _show('camBadgeRole');
    var rb=document.getElementById('camBadgeRole'); if(rb) rb.textContent=ROLE_CFG[ATT.role].label;
    _hide('camBadgeError'); _show('attLiveBadge');
    _setEl('attLiveBadgeText',ROLE_CFG[ATT.role].label+' Session Active');
    if(btnStart){ btnStart.disabled=true; var l2=btnStart.querySelector('.scb-label'),i2=btnStart.querySelector('.scb-icon-wrap'); if(l2) l2.textContent='Start Session'; if(i2) i2.innerHTML='<i class="fa fa-play"></i>'; }
    if(btnStop) btnStop.disabled=false;
    _setEl('chipRole',    ROLE_CFG[ATT.role].label);
    _setEl('chipDept',    res.dept   || '—');
    _setEl('chipCourse',  res.payload.course  || '—');
    _setEl('chipSection', res.payload.section || '—');
    ATT._sessionStart = Date.now();
    if(ATT._timerInterval) clearInterval(ATT._timerInterval);
    ATT._timerInterval = setInterval(function(){
      if(!ATT.active){ clearInterval(ATT._timerInterval); return; }
      var elapsed = Math.floor((Date.now() - ATT._sessionStart) / 1000);
      var mm = String(Math.floor(elapsed / 60)).padStart(2, '0');
      var ss = String(elapsed % 60).padStart(2, '0');
      _setEl('chipTimer', mm + ':' + ss);
    }, 1000);
    ATT.active=true; ATT.seenIds=new Set(); ATT._shownModelWarning=false; ATT._lastDetectKey=null; _resetLdcList();
    if(typeof APP!=='undefined') APP._streamLive=true;
    clearInterval(ATT.pollTimer); ATT.pollTimer=setInterval(_poll,1000);
    attToast('✓ '+ROLE_CFG[ATT.role].label+' session started','success');
  } catch(e){
    ATT.active=false;
    if(typeof APP!=='undefined') APP._streamLive=false;
    if(btnStart){ btnStart.disabled=false; var l3=btnStart.querySelector('.scb-label'),i3=btnStart.querySelector('.scb-icon-wrap'); if(l3) l3.textContent='Start Session'; if(i3) i3.innerHTML='<i class="fa fa-play"></i>'; }
    var msg=e.message||'Unknown error';
    if(msg.toLowerCase().includes('model')||msg.toLowerCase().includes('train')){
      attToast('⚠ Model not trained for '+ROLE_CFG[ATT.role].label+'. Go to Main Menu → Train All Models first.','error');
    } else if(msg.toLowerCase().includes('camera')||msg.toLowerCase().includes('cap')){
      attToast('⚠ Camera error: '+msg+'. Close Teams/Zoom and retry.','error');
    } else { attToast('Start failed: '+msg,'error'); }
  }
}

async function stopAttendance() {
  ATT.active=false;
  if(ATT._timerInterval){ clearInterval(ATT._timerInterval); ATT._timerInterval=null; }
  _setEl('chipTimer', '00:00');
  _setEl('chipCourse', '—');
  _setEl('chipSection', '—');
  if(typeof APP!=='undefined') APP._streamLive=false;
  clearInterval(ATT.pollTimer); clearInterval(ATT.stepTimer);
  ATT.pollTimer=ATT.stepTimer=null;
  if(_mjpegRetryTimer){ clearTimeout(_mjpegRetryTimer); _mjpegRetryTimer=null; }
  try{ await api.sessionStop(); } catch(_){}
  var img=document.getElementById('mjpegImg'); if(img){ img.src=''; img.classList.add('dn'); }
  var cw=document.getElementById('camWrap'); if(cw) cw.classList.remove('cam-live');
  var ci=document.getElementById('camIdle');
  if(ci){
    ci.classList.remove('dn');
    var icon=ci.querySelector('.cam-offline-icon');
    var title=document.getElementById('camIdleTitle');
    var hint=document.getElementById('camIdleHint');
    if(icon) icon.innerHTML='<i class="fa fa-video-slash"></i>';
    if(title){ title.textContent='Session Stopped'; title.style.color='#cbd5e1'; }
    if(hint){ hint.textContent='Camera turned off'; hint.style.color='#64748b'; }
  }
  var co=document.getElementById('camOverlay'); if(co) co.classList.add('dn');
  _hide('camBadgeLive'); _hide('camBadgeRole'); _hide('camBadgeError'); _hide('attLiveBadge');
  var bs=document.getElementById('btnStart'),bst=document.getElementById('btnStop');
  if(bs) bs.disabled=false; if(bst) bst.disabled=true;
  attToast('Session stopped','info'); _poll();
}

function resetAttSession(){ _poll(); }

var _mjpegRetryTimer = null;
function handleMjpegError(img){
  // Only retry when session is truly active and not already retrying.
  // Do NOT hide/show the img — just silently swap the src after a brief pause.
  // This prevents the infinite flicker loop caused by repeated onerror → src reset cycles.
  if(!ATT.active) return;
  if(_mjpegRetryTimer) return; // already scheduled — ignore duplicate onerror fires
  _mjpegRetryTimer = setTimeout(function(){
    _mjpegRetryTimer = null;
    if(ATT.active){
      // Swap src without touching visibility classes — avoids the black flash
      img.src = (window._ATT_API_BASE||'') + '/video_feed?' + Date.now();
    }
  }, 1500);
}

/* ═══════════════ POLLING ═══════════════════════════════════ */
async function _poll() {
  try{
    var s=await api.sessionStatus();
    if(ATT.active&&!s.running){
      ATT.active=false; if(typeof APP!=='undefined') APP._streamLive=false;
      clearInterval(ATT.pollTimer); clearInterval(ATT.stepTimer); ATT.pollTimer=ATT.stepTimer=null;
      var img2=document.getElementById('mjpegImg'),idle2=document.getElementById('camIdle'),wrap2=document.getElementById('camWrap');
      if(img2){ img2.src=''; img2.classList.add('dn'); } if(wrap2) wrap2.classList.remove('cam-live');
      if(idle2){ idle2.classList.remove('dn'); idle2.innerHTML='<div class="cam-rings"><div class="cr cr1"></div><div class="cr cr2"></div><div class="cr cr3"></div></div><div class="cam-offline-icon"><i class="fa fa-triangle-exclamation" style="color:#EF4444"></i></div><div class="cam-offline-title" style="color:#EF4444">Session Ended</div><div class="cam-offline-hint">'+_esc(s.error||'Session stopped.')+'</div>'; }
      var co2=document.getElementById('camOverlay'); if(co2) co2.classList.add('dn');
      _hide('camBadgeLive'); _hide('camBadgeRole'); _hide('attLiveBadge');
      var be=document.getElementById('camBadgeError'); if(be) be.classList.remove('dn');
      var bs2=document.getElementById('btnStart'); if(bs2) bs2.disabled=false;
      var bst2=document.getElementById('btnStop'); if(bst2) bst2.disabled=true;
      if(s.error) attToast(s.error,'warn'); return;
    }
    var marked=s.marked_count||0,absent=s.absent_count||0,total=s.total_students||0;
    _setEl('chipPresent',marked+' present'); _setEl('chipAbsent',absent+' absent');
    _setEl('ltPresent',marked); _setEl('ltAbsent',absent); _setEl('ltTotal',total);
    var pct=total>0?Math.min(marked/total*100,100):0;
    var fill=document.getElementById('ltProgressFill'); if(fill) fill.style.width=pct+'%';
    _updateTable(s.already_marked||[],s.role||ATT.role,s.period||'');
    _setEl('latCountPill',(s.already_marked||[]).length+' records');
    // ── ROOT CAUSE FIX: attShowResult was NEVER called during an active session.
    // The backend correctly marks attendance but the frontend "Last Detection"
    // card (section 06) never updated — it stayed on "Awaiting face detection..."
    // and the camera feed showed "?" (red box) because last_detection was null.
    // Fix: read last_detection from the status response and call attShowResult
    // whenever a new detection lands (keyed by time to avoid re-triggering).
    if(s.last_detection){
      var ld=s.last_detection;
      var ldKey=(ld.person_id||'')+'@'+(ld.time||'');
      if(ldKey !== ATT._lastDetectKey){
        ATT._lastDetectKey = ldKey;
        attShowResult(ld);
      }
    }
    // BUG-5 FIX: surface model_warning from backend (invalid student labels)
    if(s.model_warning && !ATT._shownModelWarning){
      ATT._shownModelWarning=true;
      attToast('⚠ Model Warning: '+s.model_warning,'warn');
    }
  } catch(_){}
}

/* ═══════════════ LIVE TABLE ════════════════════════════════ */
function _updateTable(records,role,period){
  var tbody=document.getElementById('liveTableBody'); if(!tbody) return;
  var rl=role?(ROLE_CFG[role]?ROLE_CFG[role].label:role):(ATT.role?ROLE_CFG[ATT.role].label:'Student');
  if(!rl&&period){ if(period.indexOf('student')!==-1) rl='Student'; else if(period.indexOf('staff')!==-1) rl='Staff'; else if(period.indexOf('hod')!==-1) rl='HOD'; }
  var rc='lt-role-'+(rl||'student').toLowerCase().replace(/[^a-z]/g,'');
  var today=new Date().toLocaleDateString('en-IN',{day:'2-digit',month:'short',year:'numeric'});
  var html='';
  records.forEach(function(r,i){
    var uid=r.uid||r.student_id||r.staff_id||r.hod_id||'—',name=r.name||'—',dept=r.department||'—',time=r.time||'—',date=r.date||today,status=r.status||'Present';
    var isNew=!ATT.seenIds.has(uid)?' lt-row-new':''; ATT.seenIds.add(uid);
    var stCls=status.toLowerCase()==='absent'?'lt-status-absent':'lt-status-present';
    html+='<tr class="'+isNew+'"><td class="lt-num">'+(i+1)+'</td><td class="lt-id">'+_esc(uid)+'</td><td class="lt-name">'+_esc(name)+'</td><td><span class="lt-role-pill '+rc+'">'+_esc(rl)+'</span></td><td>'+_esc(dept)+'</td><td class="lt-date">'+_esc(date)+'</td><td class="lt-time">'+_esc(time)+'</td><td><span class="lt-status-pill '+stCls+'">'+_esc(status)+'</span></td></tr>';
  });
  tbody.innerHTML=html||'<tr class="lt-empty-row"><td colspan="8"><div class="lt-empty-state"><i class="fa fa-inbox"></i><p>No records yet — session running, waiting for faces</p></div></td></tr>';
}

/* ═══════════════ STEP ANIMATION ════════════════════════════ */
function _startStepAnim(){
  _resetSteps(); ATT.stepIdx=0; clearInterval(ATT.stepTimer);
  ATT.stepTimer=setInterval(function(){
    if(!ATT.active){ clearInterval(ATT.stepTimer); _resetSteps(); return; }
    STEP_IDS.forEach(function(id,i){ var el=document.getElementById(id); if(!el) return; el.classList.remove('step-active','step-done'); if(i<ATT.stepIdx) el.classList.add('step-done'); if(i===ATT.stepIdx) el.classList.add('step-active'); });
    ATT.stepIdx++; if(ATT.stepIdx>=STEP_IDS.length+2) ATT.stepIdx=0;
  },650);
}
function _resetSteps(){ STEP_IDS.forEach(function(id){ var el=document.getElementById(id); if(el) el.classList.remove('step-active','step-done'); }); }

/* ═══════════════ RESULT CARD ═══════════════════════════════ */
/* ─── Last Detection ordered list ─────────────────────────── */
/* Keyed by person_id to avoid duplicates; insertion order = attendance order */
var _ldcMap = new Map(); // person_id -> {name, time, idx}
var _ldcCounter = 0;

function attShowResult(response){
  if(!response || response.status !== 'success') return;
  var name = response.name || '—';
  var pid  = response.person_id || name; // unique key
  var time = response.time || new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});

  // Only add new entries (don't re-add duplicates)
  if(!_ldcMap.has(pid)){
    _ldcCounter++;
    _ldcMap.set(pid, {name:name, time:time, idx:_ldcCounter});
    _renderLdcList();
  }

  // Show brief green banner overlay on camera
  _showLdcBanner(name);
  // Also show "Detecting & recognizing..." on the scan label
  var sl=document.getElementById('camScanLabel'); if(sl) sl.textContent='Detecting & recognizing...';
}

function _renderLdcList(){
  var list = document.getElementById('ldcList');
  var empty= document.getElementById('ldcEmpty');
  var footer= document.getElementById('ldcFooter');
  if(!list) return;
  if(_ldcMap.size === 0){
    list.innerHTML='';
    if(empty) empty.style.display='flex';
    if(footer) footer.style.display='none';
    _setEl('ldcTotalCount','0');
    return;
  }
  if(empty) empty.style.display='none';
  if(footer) footer.style.display='flex';
  _setEl('ldcTotalCount', String(_ldcMap.size));

  var html='';
  _ldcMap.forEach(function(v){
    var initials = v.name.split(' ').map(function(w){return w[0]||'';}).join('').toUpperCase().slice(0,2);
    html += '<div class="ldc-row">' +
      '<div class="ldc-row-num">'+v.idx+'</div>' +
      '<div class="ldc-avatar">'+_esc(initials)+'</div>' +
      '<div class="ldc-row-name">'+_esc(v.name)+'</div>' +
      '<div class="ldc-row-time">'+_esc(v.time)+'</div>' +
    '</div>';
  });
  list.innerHTML=html;
}

var _bannerTimer=null;
function _showLdcBanner(name){
  var banner=document.getElementById('ldcBanner');
  var bname=document.getElementById('ldcBannerName');
  if(!banner) return;
  if(bname) bname.textContent=name;
  banner.classList.remove('dn');
  clearTimeout(_bannerTimer);
  _bannerTimer=setTimeout(function(){ if(banner) banner.classList.add('dn'); },2500);
}

function _resetLdcList(){
  _ldcMap.clear(); _ldcCounter=0;
  var list=document.getElementById('ldcList');
  var empty=document.getElementById('ldcEmpty');
  var footer=document.getElementById('ldcFooter');
  if(list) list.innerHTML='';
  if(empty){ empty.style.display='flex'; }
  if(footer) footer.style.display='none';
  _setEl('ldcTotalCount','0');
}

/* ═══════════════ PAGE INIT ═════════════════════════════════ */
function initAttendancePageRoleBased(){
  if(typeof APP!=='undefined'&&APP._streamLive){
    ATT.active=true;
    var bs=document.getElementById('btnStart'); if(bs) bs.disabled=true;
    var bst=document.getElementById('btnStop'); if(bst) bst.disabled=false;
    var vs=(window._ATT_API_BASE||'')+'/video_feed?'+Date.now();
    var img=document.getElementById('mjpegImg'); if(img){ img.src=vs; img.classList.remove('dn'); }
    var ci=document.getElementById('camIdle'); if(ci) ci.classList.add('dn');
    var cw=document.getElementById('camWrap'); if(cw) cw.classList.add('cam-live');
    var co=document.getElementById('camOverlay'); if(co) co.classList.remove('dn');
    _show('camBadgeLive'); _show('camBadgeRole'); _startStepAnim();
    clearInterval(ATT.pollTimer); ATT.pollTimer=setInterval(_poll,1000); _poll(); return;
  }
  ATT.active=false; ATT.seenIds=new Set(); _resetLdcList(); _poll(); _loadCounts();
}

async function _loadCounts(){
  try{ var d=await api.enrollCounts(); if(d.students!==undefined) _setEl('cntStudents',d.students); if(d.faculty!==undefined) _setEl('cntFaculty',d.faculty); if(d.hods!==undefined) _setEl('cntHods',d.hods); } catch(_){}
}

/* ═══════════════ CSV EXPORT ════════════════════════════════ */
async function exportTodayCSVAtt(){
  try{
    if(typeof exportTodayCSV==='function'){ exportTodayCSV(); return; }
    var r=await api.exportCsv(); var b=await r.blob(); var u=URL.createObjectURL(b);
    var a=document.createElement('a'); a.href=u; a.download='attendance_'+new Date().toISOString().slice(0,10)+'.csv';
    document.body.appendChild(a); a.click(); document.body.removeChild(a); URL.revokeObjectURL(u);
    attToast('CSV exported!','success');
  } catch(e){ attToast('Export failed: '+e.message,'error'); }
}

/* ═══════════════ TOAST ═════════════════════════════════════ */
function attToast(msg,type){
  if(typeof window._appToast==='function'){ window._appToast(msg,type); return; }
  var el=document.createElement('div');
  var c={success:'#10B981',warn:'#F59E0B',error:'#EF4444',info:'#4F46E5'}[type]||'#4F46E5';
  el.style.cssText='position:fixed;bottom:28px;right:28px;z-index:9999;padding:14px 20px;border-radius:12px;background:#0F172A;color:#fff;font-size:.84rem;font-weight:600;border-left:4px solid '+c+';box-shadow:0 8px 32px rgba(15,23,42,.3);animation:attToastIn .3s cubic-bezier(.4,0,.2,1);font-family:"Plus Jakarta Sans",sans-serif;max-width:360px;line-height:1.5;';
  el.textContent=msg;
  if(!document.getElementById('attToastKF')){ var s=document.createElement('style'); s.id='attToastKF'; s.textContent='@keyframes attToastIn{from{opacity:0;transform:translateY(14px)}to{opacity:1;transform:translateY(0)}}'; document.head.appendChild(s); }
  document.body.appendChild(el); setTimeout(function(){ if(el.parentNode) el.parentNode.removeChild(el); },4000);
}
(function(){ if(typeof window.toast==='function'&&window.toast!==attToast) window._appToast=window.toast; window.toast=attToast; })();

/* ═══════════════ HELPERS ═══════════════════════════════════ */
function _setEl(id,val){ var el=document.getElementById(id); if(el) el.textContent=String(val); }
function _show(id){ var el=document.getElementById(id); if(el) el.classList.remove('dn'); }
function _hide(id){ var el=document.getElementById(id); if(el) el.classList.add('dn'); }
function _esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ═══════════════ DOMContentLoaded ═════════════════════════ */
document.addEventListener('DOMContentLoaded',function(){
  var clockEl=document.getElementById('attHeaderTime');
  if(clockEl){ function tick(){ clockEl.textContent=new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}); } tick(); setInterval(tick,1000); }
  var isInsideSPA=!!document.getElementById('pg-attendance');
  if(!isInsideSPA) initAttendancePageRoleBased();
});