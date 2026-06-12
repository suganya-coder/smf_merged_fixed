

/* ══════════════════════════════════════════════════════════════
   EduTrack Pro — features.js  v11.0  (CLEAN — no legacy comments)
   Department Drill-Down (9-level) + Faculty Management
   ══════════════════════════════════════════════════════════════ */

'use strict';

/* ── Extended API methods ─────────────────────────────────── */
Object.assign(api, {
  v2Categories:      ()                         => apiFetch('/api/v2/categories'),
  v2Departments:     (cat)                      => apiFetch('/api/v2/departments' + (cat ? '?category=' + cat : '')),
  v2Years:           (dept)                     => apiFetch('/api/v2/departments/' + dept + '/years'),
  v2Semesters:       (dept, yr)                 => apiFetch('/api/v2/departments/' + dept + '/years/' + yr + '/semesters'),
  v2Classes:         (dept, yr, sem)            => apiFetch('/api/v2/departments/' + dept + '/years/' + yr + '/semesters/' + sem + '/classes'),
  v2Subjects:        (dept, yr, sem, cls)       => apiFetch('/api/v2/departments/' + dept + '/years/' + yr + '/semesters/' + sem + '/classes/' + cls + '/subjects'),
  v2SubjectStudents: (dept, yr, sem, cls, subj) => apiFetch('/api/v2/departments/' + dept + '/years/' + yr + '/semesters/' + sem + '/classes/' + cls + '/subjects/' + encodeURIComponent(subj) + '/students'),
  v2StudentDetail:   (sid, subj)                => apiFetch('/api/v2/students/' + sid + '/subjects/' + encodeURIComponent(subj) + '/detail'),
  departments:       ()                         => apiFetch('/api/departments'),
  deptCourses:       (dk)                       => apiFetch('/api/departments/' + dk + '/courses'),
  courseSections:    (dk, ck)                   => apiFetch('/api/departments/' + dk + '/courses/' + ck + '/sections'),
  sectionStudents:   (dk, ck, sec)              => apiFetch('/api/departments/' + dk + '/courses/' + ck + '/sections/' + sec + '/students'),
  faculty: function(dept, search, date) {
    var p = new URLSearchParams();
    if (dept)   p.set('dept', dept);
    if (search) p.set('search', search);
    if (date)   p.set('att_date', date);
    return apiFetch('/api/faculty' + (p.toString() ? '?' + p : ''));
  },
  facultyAnalytics: ()              => apiFetch('/api/faculty/analytics/summary'),
  facultyDetail:    (id)            => apiFetch('/api/faculty/' + id + '?days=30'),
  markFacAtt:       (data)          => apiFetch('/api/faculty/attendance',             { method: 'POST', body: JSON.stringify(data) }),
  editFacAttApi:    (id, lid, data) => apiFetch('/api/faculty/' + id + '/attendance/' + lid, { method: 'PUT',  body: JSON.stringify(data) }),
  exportFacultyCSV: (dept)          => apiFetch('/api/faculty/export/csv' + (dept ? '?dept=' + dept : '')),
});

/* ══════════════════════════════════════════════════════════════
   DRILL STATE
   ══════════════════════════════════════════════════════════════ */
var DRILL = {
  category:  null,
  dept:      null,
  deptName:  null,
  deptColor: '#4ecba8',
  year:      null,
  yearLabel: null,
  sem:       null,
  semLabel:  null,
  cls:       null,
  subject:   null,
  _taxonomy: {},
};

/* ══════════════════════════════════════════════════════════════
   BREADCRUMB — named global calls only, never .toString()
   ══════════════════════════════════════════════════════════════ */
function updateBreadcrumb(crumbs) {
  var el = document.getElementById('breadTrail');
  if (!el) return;
  var html = '';
  for (var i = 0; i < crumbs.length; i++) {
    var c = crumbs[i], isLast = (i === crumbs.length - 1);
    if (isLast) {
      html += '<span class="bc-item active">' + c.label + '</span>';
    } else {
      html += '<button class="bc-item" onclick="' + c.call + '">' + c.label + '</button>';
      html += '<span class="bc-sep"><i class="fa fa-chevron-right"></i></span>';
    }
  }
  el.innerHTML = html;
}

/* ── Tiny helpers ─────────────────────────────────────────── */
function _esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function _loading(msg) {
  return '<div class="empty-msg"><i class="fa fa-spinner fa-spin"></i><p>' + msg + '</p></div>';
}
function _err(msg, call) {
  return '<div class="empty-msg" style="color:var(--coral-d)">' +
    '<i class="fa fa-triangle-exclamation"></i><p>' + _esc(msg) + '</p>' +
    '<button class="btn-primary" onclick="' + call + '"><i class="fa fa-rotate-right"></i> Retry</button></div>';
}
function _ac(pct) {
  return pct >= 75 ? 'var(--mint)' : pct >= 65 ? 'var(--amber)' : 'var(--coral)';
}
function _km(title, value, color) {
  return '<div class="kpi-card" style="--kc:' + color + '">' +
         '<div class="kpi-val">' + value + '</div>' +
         '<div class="kpi-lbl">' + title + '</div></div>';
}
function _sb(pct) {
  return pct >= 75 ? '<span class="badge b-g">✓ Good</span>'
       : pct >= 65 ? '<span class="badge b-w">⚠ Warn</span>'
       :             '<span class="badge b-c">✗ Critical</span>';
}
function _msb(label, value, color) {
  return '<div style="background:var(--bg);border-radius:var(--r-sm);padding:10px 12px;text-align:center">' +
    '<div style="font-size:1.05rem;font-weight:800;color:' + color + '">' + value + '</div>' +
    '<div style="font-size:.7rem;color:var(--text3);margin-top:2px">' + label + '</div></div>';
}
function _q(s) { return String(s).replace(/\\/g,'\\\\').replace(/'/g,"\\'"); }

/* ══════════════════════════════════════════════════════════════
   LEVEL 1 — INSTITUTION OVERVIEW
   ══════════════════════════════════════════════════════════════ */
async function initDeptDrill() {
  DRILL.category = DRILL.dept = DRILL.year = DRILL.sem = DRILL.cls = DRILL.subject = null;
  updateBreadcrumb([{ label: '🏫 Institution', call: 'initDeptDrill()' }]);

  var drill = document.getElementById('drillContent');
  if (!drill) return;
  drill.innerHTML = _loading('Loading attendance overview...');

  try {
    var catData  = await api.v2Categories().catch(function(){ return null; });
    var deptData = await api.v2Departments().catch(function(){ return { departments:[], taxonomy:{} }; });
    DRILL._taxonomy = deptData.taxonomy || {};
    var depts = deptData.departments || [];

    var tot = depts.reduce(function(s,d){ return s+(d.total_students||0); },0);
    var avg = depts.length ? Math.round(depts.reduce(function(s,d){ return s+(d.avg_att||0); },0)/depts.length) : 0;
    var crit= depts.reduce(function(s,d){ return s+(d.poor||0); },0);
    var good= depts.reduce(function(s,d){ return s+(d.good||0); },0);
    var warn= depts.reduce(function(s,d){ return s+(d.warn||0); },0);
    var sw  = (catData && catData.software) || {};
    var hw  = (catData && catData.hardware) || {};

    drill.innerHTML =
      '<div class="kpi-strip" style="margin-bottom:24px">' +
        kpi('Total Students',    tot,       'fa-users',        '#4ecba8') +
        kpi('Total Classes',     320,       'fa-chalkboard',   '#4da6f5') +
        kpi('Avg Attendance',    avg + '%', 'fa-chart-line',   '#ffb347') +
        kpi('Critical Students', crit,      'fa-circle-xmark', '#ff7070') +
      '</div>' +
      '<div class="two-col" style="margin-bottom:24px">' +
        '<div class="card">' +
          '<div class="card-head"><h4><i class="fa fa-chart-pie"></i> Overall Attendance</h4></div>' +
          '<div style="display:flex;flex-direction:column;align-items:center;padding:20px 16px">' +
            '<div class="overall-donut-wrap">' +
              '<canvas id="overallDonut" width="180" height="180"></canvas>' +
              '<div class="overall-donut-center">' +
                '<div class="overall-pct">' + avg + '%</div>' +
                '<div class="overall-lbl">Overall</div>' +
              '</div>' +
            '</div>' +
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;width:100%;margin-top:16px">' +
              _km('Total Students', tot, '#4ecba8') + _km('Total Classes', 320, '#4da6f5') +
            '</div>' +
          '</div>' +
        '</div>' +
        '<div class="card">' +
          '<div class="card-head"><h4><i class="fa fa-chart-bar"></i> Department Overview</h4></div>' +
          '<div class="chart-pad"><canvas id="deptOverviewChart" height="200"></canvas></div>' +
        '</div>' +
      '</div>' +
      '<h3 style="font-size:1rem;font-weight:800;color:var(--text);margin-bottom:14px">' +
        '<i class="fa fa-layer-group"></i> Choose a Category</h3>' +
      '<div class="sw-hw-grid">' + _catCard('software',sw,'💻','#4ecba8') + _catCard('hardware',hw,'🔧','#4da6f5') + '</div>';

    setTimeout(function(){
      mkDonut('overallDonut',['Good (≥75%)','Warning','Critical'],[good,warn,crit],['#4ecba8','#ffb347','#ff7070']);
      mkBar('deptOverviewChart',
        depts.map(function(d){return d.key;}),
        depts.map(function(d){return d.avg_att;}),
        depts.map(function(d){return d.color||'#4ecba8;}'},), '%');
    }, 80);
  } catch(e) { drill.innerHTML = _err(e.message,'initDeptDrill()'); }
}

function _catCard(catKey, stats, emoji, color) {
  var dc   = stats && stats.dept_count ? stats.dept_count : '—';
  var av   = stats && stats.avg_att    ? stats.avg_att    : 0;
  var lbl  = catKey === 'software' ? 'Software' : 'Hardware';
  var sub  = catKey === 'software' ? 'CS / IT / AIDS / CSBS / MCA / BCA' : 'ECE / EEE / MECH / CIVIL / Bio-Medical';
  return '<div class="sw-hw-card" style="--swc:' + color + '" onclick="drillToCategory(\'' + catKey + '\')">' +
    '<div class="swc-icon">' + emoji + '</div>' +
    '<div class="swc-label">' + lbl + '</div>' +
    '<div class="swc-sub">' + sub + '</div>' +
    '<div class="swc-meta"><span><i class="fa fa-building-columns"></i> ' + dc + ' Depts</span>' +
    '<span><i class="fa fa-chart-line"></i> ' + av + '% Avg</span></div>' +
    '<div class="swc-action">View Departments <i class="fa fa-arrow-right"></i></div></div>';
}

/* ══════════════════════════════════════════════════════════════
   LEVEL 2 — Category → Dept list
   ══════════════════════════════════════════════════════════════ */
async function drillToCategory(catKey) {
  DRILL.category = catKey;
  DRILL.dept = DRILL.year = DRILL.sem = DRILL.cls = DRILL.subject = null;
  var cl = catKey==='software'?'💻 Software':'🔧 Hardware';
  updateBreadcrumb([
    {label:'🏫 Institution',call:'initDeptDrill()'},
    {label:cl,             call:'drillToCategory(\'' + catKey + '\')'},
  ]);

  var drill = document.getElementById('drillContent');
  drill.innerHTML = _loading('Loading departments...');
  try {
    var data  = await api.v2Departments(catKey);
    DRILL._taxonomy = data.taxonomy || DRILL._taxonomy;
    var depts = data.departments || [];
    var tot   = depts.reduce(function(s,d){return s+(d.total_students||0);},0);
    var avg   = depts.length?Math.round(depts.reduce(function(s,d){return s+(d.avg_att||0);},0)/depts.length):0;
    var crit  = depts.reduce(function(s,d){return s+(d.poor||0);},0);
    var cards = ''; for(var i=0;i<depts.length;i++) cards+=_dCard(depts[i]);

    drill.innerHTML =
      '<div class="kpi-strip" style="margin-bottom:24px">' +
        _km('Departments',depts.length,'#4ecba8')+_km('Students',tot,'#4da6f5')+
        _km('Avg Att',avg+'%','#ffb347')+_km('Critical',crit,'#ff7070') +
      '</div>' +
      '<div class="dept-card-grid">' + cards + '</div>' +
      '<div class="card" style="margin-top:24px">' +
        '<div class="card-head"><h4><i class="fa fa-chart-bar"></i> ' + cl + ' — Attendance %</h4></div>' +
        '<div class="chart-pad"><canvas id="catDeptChart" height="160"></canvas></div>' +
      '</div>';

    setTimeout(function(){
      mkBar('catDeptChart',
        depts.map(function(d){return d.key;}),
        depts.map(function(d){return d.avg_att;}),
        depts.map(function(d){return d.color||'#4ecba8';}), '%');
    },80);
  } catch(e){ drill.innerHTML = _err(e.message,'drillToCategory(\'' + catKey + '\')'); }
}

function _dCard(d) {
  var att=d.avg_att||0, color=d.color||'#4ecba8';
  return '<div class="d-card" style="--dc:' + color + '" onclick="drillToDept(\'' + d.key + '\')">' +
    '<div class="dc-emoji">' + (d.emoji||'🏛️') + '</div>' +
    '<div class="dc-name">' + d.key + '</div>' +
    '<div class="dc-full-name">' + d.name + '</div>' +
    '<div class="dc-att-pct" style="color:' + _ac(att) + '">' + att + '%</div>' +
    '<div class="dc-att-row">' + attBar(att) + '</div>' +
    '<div class="dc-stats-row">' +
      '<span class="dc-stat good">✓ '+(d.good||0)+'</span>' +
      '<span class="dc-stat warn">⚠ '+(d.warn||0)+'</span>' +
      '<span class="dc-stat poor">✗ '+(d.poor||0)+'</span>' +
    '</div>' +
    '<div class="dc-action">View Years <i class="fa fa-arrow-right"></i></div></div>';
}

/* ══════════════════════════════════════════════════════════════
   LEVEL 3 — Dept → Year-wise
   ══════════════════════════════════════════════════════════════ */
async function drillToDept(dk) {
  DRILL.dept=dk; DRILL.year=DRILL.sem=DRILL.cls=DRILL.subject=null;
  DRILL.deptName = (DRILL._taxonomy[dk]&&DRILL._taxonomy[dk].name)||dk;
  var ck=DRILL.category||'software', cl=ck==='software'?'💻 Software':'🔧 Hardware';
  updateBreadcrumb([
    {label:'🏫 Institution',call:'initDeptDrill()'},
    {label:cl,             call:'drillToCategory(\'' + ck + '\')'},
    {label:'🏢 '+dk,       call:'drillToDept(\'' + dk + '\')'},
  ]);
  var drill=document.getElementById('drillContent');
  drill.innerHTML=_loading('Loading year-wise data...');
  try {
    var data=await api.v2Years(dk), years=data.years||[];
    DRILL.deptColor=data.dept_color||'#4ecba8';
    var tot=years.reduce(function(s,y){return s+y.total_students;},0);
    var avg=years.length?Math.round(years.reduce(function(s,y){return s+y.avg_att;},0)/years.length):0;
    var crit=years.reduce(function(s,y){return s+y.poor;},0);
    var cards=''; for(var i=0;i<years.length;i++) cards+=_yCard(years[i],dk);
    var color=DRILL.deptColor;

    drill.innerHTML=
      '<div class="kpi-strip" style="margin-bottom:24px">' +
        _km('Department',dk,color)+_km('Students',tot,'#4da6f5')+
        _km('Avg Att',avg+'%','#ffb347')+_km('Critical',crit,'#ff7070') +
      '</div>' +
      '<h3 class="drill-section-title"><i class="fa fa-calendar-alt"></i> '+DRILL.deptName+' — Year Wise Attendance</h3>' +
      '<div class="dept-card-grid">'+cards+'</div>' +
      '<div class="card" style="margin-top:24px">' +
        '<div class="card-head"><h4><i class="fa fa-chart-bar"></i> '+dk+' — Year Wise Attendance</h4></div>' +
        '<div class="chart-pad"><canvas id="yearBarChart" height="160"></canvas></div>' +
      '</div>';

    setTimeout(function(){
      mkBar('yearBarChart',
        years.map(function(y){return y.year_label;}),
        years.map(function(y){return y.avg_att;}), color,'%');
    },80);
  } catch(e){ drill.innerHTML=_err(e.message,'drillToDept(\'' + dk + '\')'); }
}

function _yCard(y, dk) {
  var att=y.avg_att||0, color=DRILL.deptColor;
  return '<div class="d-card" style="--dc:'+color+'" onclick="drillToYear(\''+dk+'\','+y.year_num+',\''+_q(y.year_label)+'\')">' +
    '<div class="dc-emoji">🎓</div><div class="dc-name">'+y.year_label+'</div>' +
    '<div class="dc-att-pct" style="color:'+_ac(att)+'">'+att+'%</div>' +
    '<div class="dc-att-row">'+attBar(att)+'</div>' +
    '<div class="dc-meta"><span><i class="fa fa-users"></i> '+y.total_students+' students</span>' +
    '<span><i class="fa fa-door-open"></i> Classes: '+(y.classes?y.classes.join(', '):'—')+'</span></div>' +
    '<div class="dc-stats-row"><span class="dc-stat good">✓ '+y.good+'</span><span class="dc-stat warn">⚠ '+y.warn+'</span><span class="dc-stat poor">✗ '+y.poor+'</span></div>' +
    '<div class="dc-action">View Semesters <i class="fa fa-arrow-right"></i></div></div>';
}

/* ══════════════════════════════════════════════════════════════
   LEVEL 4 — Year → Semester-wise
   ══════════════════════════════════════════════════════════════ */
async function drillToYear(dk, yn, yl) {
  DRILL.year=yn; DRILL.yearLabel=yl; DRILL.sem=DRILL.cls=DRILL.subject=null;
  var ck=DRILL.category||'software', cl=ck==='software'?'💻 Software':'🔧 Hardware';
  updateBreadcrumb([
    {label:'🏫 Institution',call:'initDeptDrill()'},
    {label:cl,             call:'drillToCategory(\''+ck+'\')'},
    {label:'🏢 '+dk,       call:'drillToDept(\''+dk+'\')'},
    {label:'🎓 '+yl,       call:'drillToYear(\''+dk+'\','+yn+',\''+_q(yl)+'\')'},
  ]);
  var drill=document.getElementById('drillContent');
  drill.innerHTML=_loading('Loading semester data...');
  try {
    var data=await api.v2Semesters(dk,yn), sems=data.semesters||[];
    var ms=sems.reduce(function(s,x){return Math.max(s,x.total_students);},0);
    var avg=sems.length?Math.round(sems.reduce(function(s,x){return s+x.avg_att;},0)/sems.length):0;
    var cards=''; for(var i=0;i<sems.length;i++) cards+=_sCard(sems[i],dk,yn);
    var color=DRILL.deptColor;

    drill.innerHTML=
      '<div class="kpi-strip" style="margin-bottom:24px">' +
        _km('Year',yl,color)+_km('Students',ms,'#4da6f5')+
        _km('Avg Att',avg+'%','#ffb347')+_km('Semesters',sems.length,'#9b87f5') +
      '</div>' +
      '<h3 class="drill-section-title"><i class="fa fa-layer-group"></i> '+(DRILL.deptName||dk)+' — '+yl+' Attendance</h3>' +
      '<div class="dept-card-grid">'+cards+'</div>' +
      '<div class="card" style="margin-top:24px">' +
        '<div class="card-head"><h4><i class="fa fa-chart-bar"></i> Semester Comparison</h4></div>' +
        '<div class="chart-pad"><canvas id="semBarChart" height="160"></canvas></div>' +
      '</div>';

    setTimeout(function(){
      mkBar('semBarChart',
        sems.map(function(s){return s.sem_label;}),
        sems.map(function(s){return s.avg_att;}), color,'%');
    },80);
  } catch(e){ drill.innerHTML=_err(e.message,'drillToYear(\''+dk+'\','+yn+',\''+_q(yl)+'\')'); }
}

function _sCard(s, dk, yn) {
  var att=s.avg_att||0, color=DRILL.deptColor;
  return '<div class="d-card" style="--dc:'+color+'" onclick="drillToSem(\''+dk+'\','+yn+','+s.sem_num+',\''+_q(s.sem_label)+'\')">' +
    '<div class="dc-emoji">📅</div><div class="dc-name">'+s.sem_label+'</div>' +
    '<div class="dc-att-pct" style="color:'+_ac(att)+'">'+att+'%</div>' +
    '<div class="dc-att-row">'+attBar(att)+'</div>' +
    '<div class="dc-meta"><span><i class="fa fa-book"></i> '+s.subject_count+' subjects</span>' +
    '<span><i class="fa fa-users"></i> '+s.total_students+' students</span></div>' +
    '<div class="dc-stats-row"><span class="dc-stat good">✓ '+s.good+'</span><span class="dc-stat warn">⚠ '+s.warn+'</span><span class="dc-stat poor">✗ '+s.poor+'</span></div>' +
    '<div class="dc-action">View Classes <i class="fa fa-arrow-right"></i></div></div>';
}

/* ══════════════════════════════════════════════════════════════
   LEVEL 5 — Semester → Class-wise
   ══════════════════════════════════════════════════════════════ */
async function drillToSem(dk, yn, sn, sl) {
  DRILL.sem=sn; DRILL.semLabel=sl; DRILL.cls=DRILL.subject=null;
  var ck=DRILL.category||'software', cl=ck==='software'?'💻 Software':'🔧 Hardware';
  var yl=DRILL.yearLabel||('Year '+yn);
  updateBreadcrumb([
    {label:'🏫 Institution',call:'initDeptDrill()'},
    {label:cl,             call:'drillToCategory(\''+ck+'\')'},
    {label:'🏢 '+dk,       call:'drillToDept(\''+dk+'\')'},
    {label:'🎓 '+yl,       call:'drillToYear(\''+dk+'\','+yn+',\''+_q(yl)+'\')'},
    {label:'📅 '+sl,       call:'drillToSem(\''+dk+'\','+yn+','+sn+',\''+_q(sl)+'\')'},
  ]);
  var drill=document.getElementById('drillContent');
  drill.innerHTML=_loading('Loading class data...');
  try {
    var data=await api.v2Classes(dk,yn,sn), cls=data.classes||[], color=DRILL.deptColor;
    var tot=cls.reduce(function(s,c){return s+c.total_students;},0);
    var avg=cls.length?Math.round(cls.reduce(function(s,c){return s+c.avg_att;},0)/cls.length):0;
    var crit=cls.reduce(function(s,c){return s+c.poor;},0);
    var good=cls.reduce(function(s,c){return s+c.good;},0);
    var warn=cls.reduce(function(s,c){return s+c.warn;},0);
    var cards=''; for(var i=0;i<cls.length;i++) cards+=_cCard(cls[i],dk,yn,sn);

    drill.innerHTML=
      '<div class="kpi-strip" style="margin-bottom:24px">' +
        _km('Classes',cls.length,color)+_km('Students',tot,'#4da6f5')+
        _km('Avg Att',avg+'%','#ffb347')+_km('Critical',crit,'#ff7070') +
      '</div>' +
      '<h3 class="drill-section-title"><i class="fa fa-door-open"></i> '+sl+' — Class Wise Attendance</h3>' +
      '<div class="dept-card-grid dept-card-grid--sections">'+cards+'</div>' +
      '<div class="two-col" style="margin-top:24px">' +
        '<div class="card"><div class="card-head"><h4><i class="fa fa-chart-bar"></i> Class Attendance</h4></div>' +
        '<div class="chart-pad"><canvas id="classBarChart" height="180"></canvas></div></div>' +
        '<div class="card"><div class="card-head"><h4><i class="fa fa-chart-pie"></i> Status Distribution</h4></div>' +
        '<div class="chart-pad"><canvas id="classDonut" height="180"></canvas></div></div>' +
      '</div>';

    setTimeout(function(){
      mkBar('classBarChart',
        cls.map(function(c){return c.class_label;}),
        cls.map(function(c){return c.avg_att;}), color,'%');
      mkDonut('classDonut',['Good (≥75%)','Warning (65-75%)','Critical (<65%)'],[good,warn,crit],['#4ecba8','#ffb347','#ff7070']);
    },80);
  } catch(e){ drill.innerHTML=_err(e.message,'drillToSem(\''+dk+'\','+yn+','+sn+',\''+_q(sl)+'\')'); }
}

function _cCard(c, dk, yn, sn) {
  var att=c.avg_att||0, color=DRILL.deptColor;
  return '<div class="d-card" style="--dc:'+color+'" onclick="drillToClass(\''+dk+'\','+yn+','+sn+',\''+c.section+'\')">' +
    '<div class="dc-emoji">🏛️</div><div class="dc-name">'+c.class_label+'</div>' +
    '<div class="dc-att-pct" style="color:'+_ac(att)+'">'+att+'%</div>' +
    '<div class="dc-att-row">'+attBar(att)+'</div>' +
    '<div class="dc-meta"><span><i class="fa fa-users"></i> '+c.total_students+' students</span></div>' +
    '<div class="dc-stats-row"><span class="dc-stat good">✓ '+c.good+'</span><span class="dc-stat warn">⚠ '+c.warn+'</span><span class="dc-stat poor">✗ '+c.poor+'</span></div>' +
    '<div class="dc-action">View Subjects <i class="fa fa-arrow-right"></i></div></div>';
}

/* ══════════════════════════════════════════════════════════════
   LEVEL 6 — Class → Subject-wise
   ══════════════════════════════════════════════════════════════ */
async function drillToClass(dk, yn, sn, cs) {
  DRILL.cls=cs; DRILL.subject=null;
  var ck=DRILL.category||'software', cl=ck==='software'?'💻 Software':'🔧 Hardware';
  var yl=DRILL.yearLabel||('Year '+yn), sl=DRILL.semLabel||('Semester '+sn);
  updateBreadcrumb([
    {label:'🏫 Institution',  call:'initDeptDrill()'},
    {label:cl,               call:'drillToCategory(\''+ck+'\')'},
    {label:'🏢 '+dk,         call:'drillToDept(\''+dk+'\')'},
    {label:'🎓 '+yl,         call:'drillToYear(\''+dk+'\','+yn+',\''+_q(yl)+'\')'},
    {label:'📅 '+sl,         call:'drillToSem(\''+dk+'\','+yn+','+sn+',\''+_q(sl)+'\')'},
    {label:'🏛️ Class '+cs,  call:'drillToClass(\''+dk+'\','+yn+','+sn+',\''+cs+'\')'},
  ]);
  var drill=document.getElementById('drillContent');
  drill.innerHTML=_loading('Loading subjects...');
  try {
    var data=await api.v2Subjects(dk,yn,sn,cs), subjects=data.subjects||[], color=DRILL.deptColor;
    var avg=subjects.length?Math.round(subjects.reduce(function(s,x){return s+x.avg_att;},0)/subjects.length):0;
    var crit=subjects.reduce(function(s,x){return s+x.poor;},0);
    var cards=''; for(var i=0;i<subjects.length;i++) cards+=_subCard(subjects[i],dk,yn,sn,cs);

    drill.innerHTML=
      '<div class="kpi-strip" style="margin-bottom:24px">' +
        _km('Subjects',subjects.length,color)+_km('Students',data.total_students||0,'#4da6f5')+
        _km('Avg Att',avg+'%','#ffb347')+_km('Critical',crit,'#ff7070') +
      '</div>' +
      '<h3 class="drill-section-title"><i class="fa fa-book-open"></i> Class '+cs+' — Subject Wise Attendance</h3>' +
      '<div class="dept-card-grid">'+cards+'</div>' +
      '<div class="card" style="margin-top:24px">' +
        '<div class="card-head"><h4><i class="fa fa-chart-bar"></i> Subject-wise Attendance — Class '+cs+'</h4></div>' +
        '<div class="chart-pad"><canvas id="subjectBarChart" height="160"></canvas></div>' +
      '</div>';

    setTimeout(function(){
      mkBar('subjectBarChart',
        subjects.map(function(s){return s.subject.length>12?s.subject.slice(0,12)+'…':s.subject;}),
        subjects.map(function(s){return s.avg_att;}),
        subjects.map(function(s){return s.avg_att>=75?'#4ecba8':s.avg_att>=65?'#ffb347':'#ff7070';}), '%');
    },80);
  } catch(e){ drill.innerHTML=_err(e.message,'drillToClass(\''+dk+'\','+yn+','+sn+',\''+cs+'\')'); }
}

function _subCard(s, dk, yn, sn, cs) {
  var att=s.avg_att||0, color=DRILL.deptColor, sq=_q(s.subject);
  return '<div class="d-card" style="--dc:'+color+'" onclick="drillToSubject(\''+dk+'\','+yn+','+sn+',\''+cs+'\',\''+sq+'\')">' +
    '<div class="dc-emoji">📖</div>' +
    '<div class="dc-name" title="'+_esc(s.subject)+'">'+_esc(s.subject)+'</div>' +
    '<div class="dc-att-pct" style="color:'+_ac(att)+'">'+att+'%</div>' +
    '<div class="dc-att-row">'+attBar(att)+'</div>' +
    '<div class="dc-meta"><span><i class="fa fa-users"></i> '+s.total+' students</span></div>' +
    '<div class="dc-stats-row"><span class="dc-stat good">✓ '+s.good+'</span><span class="dc-stat warn">⚠ '+s.warn+'</span><span class="dc-stat poor">✗ '+s.poor+'</span></div>' +
    '<div class="dc-action">View Students <i class="fa fa-arrow-right"></i></div></div>';
}

/* ══════════════════════════════════════════════════════════════
   LEVEL 7 — Subject → Student-wise attendance
   ══════════════════════════════════════════════════════════════ */
async function drillToSubject(dk, yn, sn, cs, subject) {
  DRILL.subject=subject;
  var ck=DRILL.category||'software', cl=ck==='software'?'💻 Software':'🔧 Hardware';
  var yl=DRILL.yearLabel||('Year '+yn), sl=DRILL.semLabel||('Semester '+sn);
  var ss=subject.length>14?subject.slice(0,14)+'…':subject, sq=_q(subject);
  updateBreadcrumb([
    {label:'🏫 Institution',  call:'initDeptDrill()'},
    {label:cl,               call:'drillToCategory(\''+ck+'\')'},
    {label:'🏢 '+dk,         call:'drillToDept(\''+dk+'\')'},
    {label:'🎓 '+yl,         call:'drillToYear(\''+dk+'\','+yn+',\''+_q(yl)+'\')'},
    {label:'📅 '+sl,         call:'drillToSem(\''+dk+'\','+yn+','+sn+',\''+_q(sl)+'\')'},
    {label:'🏛️ Class '+cs,  call:'drillToClass(\''+dk+'\','+yn+','+sn+',\''+cs+'\')'},
    {label:'📖 '+ss,         call:'drillToSubject(\''+dk+'\','+yn+','+sn+',\''+cs+'\',\''+sq+'\')'},
  ]);
  var drill=document.getElementById('drillContent');
  drill.innerHTML=_loading('Loading student attendance...');
  try {
    var data=await api.v2SubjectStudents(dk,yn,sn,cs,subject);
    var students=data.students||[], stats=data.stats||{};
    window._drillStudents=students; window._drillDeptKey=dk; window._drillYearNum=yn;
    window._drillSemNum=sn; window._drillCls=cs; window._drillSubject=subject;

    drill.innerHTML=
      '<div class="kpi-strip" style="margin-bottom:24px">' +
        _km('Avg Att',(stats.avg_att||0)+'%',DRILL.deptColor)+_km('Classes Held',data.classes_held||0,'#4da6f5')+
        _km('Attended (Avg)',data.classes_attended_avg||0,'#ffb347')+_km('Students',stats.total||students.length,'#9b87f5') +
      '</div>' +
      '<div class="card" style="margin-bottom:24px">' +
        '<div class="card-head">' +
          '<h4><i class="fa fa-users"></i> '+_esc(subject)+' — Student Attendance (Class '+cs+')</h4>' +
          '<div class="ch-actions">' +
            '<div class="search-box"><i class="fa fa-search"></i><input id="stuDrillSearch" placeholder="Search student..." oninput="filterDrillStudents()"/></div>' +
            '<select class="sel-sm" id="stuDrillFilter" onchange="filterDrillStudents()">' +
              '<option value="">All</option><option value="good">✓ Good</option><option value="warn">⚠ Warning</option><option value="poor">✗ Critical</option>' +
            '</select>' +
          '</div>' +
        '</div>' +
        '<div style="display:flex;gap:24px;padding:12px 20px;border-bottom:1px solid var(--border)">' +
          '<span style="font-size:.82rem;color:var(--text2)"><span style="color:var(--mint-d);font-weight:700">'+(stats.good||0)+'</span> Good</span>' +
          '<span style="font-size:.82rem;color:var(--text2)"><span style="color:var(--amber-d);font-weight:700">'+(stats.warn||0)+'</span> Warning</span>' +
          '<span style="font-size:.82rem;color:var(--text2)"><span style="color:var(--coral-d);font-weight:700">'+(stats.poor||0)+'</span> Critical</span>' +
        '</div>' +
        '<div class="table-scroll">' +
          '<table class="data-tbl"><thead><tr>' +
            '<th>#</th><th>Student Name</th><th>Register No</th><th>Attendance %</th><th>Chart</th><th>Status</th><th>Action</th>' +
          '</tr></thead><tbody id="stuDrillTbody">'+_stuRows(students)+'</tbody></table>' +
        '</div>' +
      '</div>' +
      '<div class="card"><div class="card-head"><h4><i class="fa fa-chart-bar"></i> Student-wise Attendance — '+_esc(subject)+'</h4></div>' +
      '<div class="chart-pad"><canvas id="stuBarChart" height="200"></canvas></div></div>';

    setTimeout(function(){
      var top10=students.slice().sort(function(a,b){return b.att_pct-a.att_pct;}).slice(0,10);
      mkBar('stuBarChart',
        top10.map(function(s){return s.name.split(' ')[0];}),
        top10.map(function(s){return s.att_pct;}),
        top10.map(function(s){return s.att_pct>=75?'#4ecba8':s.att_pct>=65?'#ffb347':'#ff7070';}), '%');
    },80);
  } catch(e){ drill.innerHTML=_err(e.message,'drillToSubject(\''+dk+'\','+yn+','+sn+',\''+cs+'\',\''+sq+'\')'); }
}

function _stuRows(students) {
  if (!students||!students.length)
    return '<tr><td colspan="7" style="text-align:center;padding:28px;color:var(--text3)">No students found in this section.</td></tr>';
  var rows='';
  for(var i=0;i<students.length;i++){
    var s=students[i], sq=_q(window._drillSubject||'');
    rows += '<tr>' +
      '<td style="font-family:var(--mono);color:var(--text3)">'+(i+1)+'</td>' +
      '<td><strong>'+_esc(s.name)+'</strong></td>' +
      '<td><code>'+_esc(s.roll_number)+'</code></td>' +
      '<td style="font-family:var(--mono);font-weight:700;color:'+_ac(s.att_pct)+'">'+s.att_pct+'%</td>' +
      '<td style="min-width:110px">'+attBar(s.att_pct)+'</td>' +
      '<td>'+_sb(s.att_pct)+'</td>' +
      '<td><button class="btn-sm" onclick="drillToStudentDetail(\''+_q(s.student_id)+'\',\''+_q(s.name)+'\',\''+sq+'\')"><i class="fa fa-eye"></i> Detail</button></td>' +
      '</tr>';
  }
  return rows;
}

function filterDrillStudents() {
  var q=(document.getElementById('stuDrillSearch')?document.getElementById('stuDrillSearch').value:'').toLowerCase();
  var f=document.getElementById('stuDrillFilter')?document.getElementById('stuDrillFilter').value:'';
  var filtered=(window._drillStudents||[]).filter(function(s){
    return((s.name||'').toLowerCase().indexOf(q)>=0||(s.roll_number||'').toLowerCase().indexOf(q)>=0)&&(!f||s.status===f);
  });
  var tb=document.getElementById('stuDrillTbody');
  if(tb) tb.innerHTML=_stuRows(filtered);
}

/* ══════════════════════════════════════════════════════════════
   LEVEL 8 — Student Detailed Attendance
   ══════════════════════════════════════════════════════════════ */
async function drillToStudentDetail(sid, sname, subject) {
  var ck=DRILL.category||'software', cl=ck==='software'?'💻 Software':'🔧 Hardware';
  var dk=DRILL.dept||'', yl=DRILL.yearLabel||'', sl=DRILL.semLabel||'', cs=DRILL.cls||'';
  var yn=DRILL.year||0, sn=DRILL.sem||0;
  var ss=subject.length>14?subject.slice(0,14)+'…':subject, sq=_q(subject);
  var backCall='drillToSubject(\''+dk+'\','+yn+','+sn+',\''+cs+'\',\''+sq+'\')';
  updateBreadcrumb([
    {label:'🏫 Institution',  call:'initDeptDrill()'},
    {label:cl,               call:'drillToCategory(\''+ck+'\')'},
    {label:'🏢 '+dk,         call:'drillToDept(\''+dk+'\')'},
    {label:'🎓 '+yl,         call:'drillToYear(\''+dk+'\','+yn+',\''+_q(yl)+'\')'},
    {label:'📅 '+sl,         call:'drillToSem(\''+dk+'\','+yn+','+sn+',\''+_q(sl)+'\')'},
    {label:'🏛️ Class '+cs,  call:'drillToClass(\''+dk+'\','+yn+','+sn+',\''+cs+'\')'},
    {label:'📖 '+ss,         call:'drillToSubject(\''+dk+'\','+yn+','+sn+',\''+cs+'\',\''+sq+'\')'},
    {label:'👤 '+sname,      call:'drillToStudentDetail(\''+_q(sid)+'\',\''+_q(sname)+'\',\''+sq+'\')'},
  ]);
  var drill=document.getElementById('drillContent');
  drill.innerHTML=_loading('Loading student attendance...');
  try {
    var data=await api.v2StudentDetail(sid,subject);
    var initials=(data.name||'?').split(' ').filter(function(w){return w;}).map(function(w){return w[0];}).join('').slice(0,2).toUpperCase();
    var ac=data.overall_att>=75?'var(--mint-d)':data.overall_att>=65?'var(--amber-d)':'var(--coral-d)';
    var logRows='';
    (data.detail_log||[]).forEach(function(l){
      logRows+='<tr><td style="font-family:var(--mono);font-size:.78rem">'+_esc(l.date)+'</td>' +
        '<td style="font-size:.78rem;color:var(--text2)">'+_esc(l.topic)+'</td>' +
        '<td><span class="badge '+(l.status==='Present'?'b-g':'b-c')+'">'+
        (l.status==='Present'?'✓ Present':'✗ Absent')+'</span></td></tr>';
    });
    if(!logRows) logRows='<tr><td colspan="3" style="text-align:center;padding:16px;color:var(--text3)">No records</td></tr>';

    drill.innerHTML=
      '<div class="card" style="margin-bottom:20px">' +
        '<div style="padding:20px 24px;display:flex;align-items:center;gap:20px;flex-wrap:wrap">' +
          '<div class="fac-av fac-av--lg" style="background:var(--lav-l);color:var(--lav-d);font-size:1.4rem">'+initials+'</div>' +
          '<div style="flex:1;min-width:180px">' +
            '<div style="font-size:1.15rem;font-weight:800;color:var(--text)">'+_esc(data.name)+'</div>' +
            '<div style="font-size:.8rem;color:var(--text2);margin-top:2px">Register No: <code>'+_esc(data.register_no)+'</code>&nbsp;·&nbsp; Section: <strong>'+_esc(data.section)+'</strong></div>' +
          '</div>' +
          '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;min-width:320px">' +
            _msb('Overall Attendance',(data.overall_att||0)+'%',ac) +
            _msb('Classes Held',data.classes_held||0,'var(--text2)') +
            _msb('Classes Attended',data.classes_attended||0,'var(--sky)') +
          '</div>' +
        '</div>' +
      '</div>' +
      '<div class="two-col" style="margin-bottom:20px">' +
        '<div class="card"><div class="card-head"><h4><i class="fa fa-chart-line"></i> Attendance Over Time</h4></div>' +
        '<div class="chart-pad"><canvas id="stuWeeklyChart" height="180"></canvas></div></div>' +
        '<div class="card"><div class="card-head"><h4><i class="fa fa-table-list"></i> Attendance Details — '+_esc(subject)+'</h4></div>' +
        '<div class="table-scroll" style="max-height:240px">' +
          '<table class="data-tbl"><thead><tr><th>Date</th><th>Topic</th><th>Status</th></tr></thead>' +
          '<tbody>'+logRows+'</tbody></table></div></div>' +
      '</div>' +
      '<div style="text-align:center;padding:8px 0 16px">' +
        '<button class="btn-secondary" onclick="'+backCall+'"><i class="fa fa-arrow-left"></i> Back to '+_esc(subject)+'</button>' +
      '</div>';

    var weekly=data.weekly_trend||[];
    setTimeout(function(){
      mkBar('stuWeeklyChart',
        weekly.map(function(w){return w.week;}),
        weekly.map(function(w){return w.pct;}),
        weekly.map(function(w){return w.pct>=75?'#4ecba8':w.pct>=65?'#ffb347':'#ff7070';}), '%');
    },80);
  } catch(e){ drill.innerHTML=_err(e.message,backCall); }
}

/* ── Legacy stubs ─────────────────────────────────────────── */
function renderDeptCards(){}
function drillToCourse(d){drillToDept(d);}
function drillToSection(c,col,dk){drillToDept(dk||c);}
function drillToSectionDetail(c,s,col,dk){drillToDept(dk||c);}
function kpiMini(t,v,c){return _km(t,v,c);}
function statusBadge(st){return '<span class="badge '+st.bc+'">'+st.label+'</span>';}

/* ══════════════════════════════════════════════════════════════
   FEATURE 2: FACULTY MANAGEMENT
   ══════════════════════════════════════════════════════════════ */
var FAC_STATE={editLogId:null,editFacId:null,allFaculty:[]};

async function renderFacultyPage() {
  var ds=document.getElementById('facMgmtDept');
  if(ds&&ds.options.length<=1) ['CS','ECE','MECH','CIVIL','IT'].forEach(function(d){ds.innerHTML+='<option value="'+d+'">'+d+'</option>';});
  var dp=document.getElementById('facMgmtDate');
  if(dp&&!dp.value) dp.value=new Date().toISOString().slice(0,10);
  await Promise.all([_facKpis(),_facCharts(),_facTable()]);
}

async function _facKpis() {
  var strip=document.getElementById('facKpiStrip'); if(!strip) return;
  try {
    var s=await api.facultyAnalytics();
    strip.innerHTML=kpi('Total Faculty',s.total_faculty,'fa-chalkboard-teacher','#4ecba8')+
      kpi('Present Today',s.present_today,'fa-circle-check','#4da6f5')+
      kpi('Absent Today',s.absent_today,'fa-circle-xmark','#ff7070')+
      kpi('Avg Att (30d)',(s.avg_att_30d||0)+'%','fa-chart-line','#ffb347');
    window._facAnalytics=s;
  } catch(e){ strip.innerHTML='<div style="color:var(--coral-d);padding:12px">'+_esc(e.message)+'</div>'; }
}

async function _facCharts() {
  try {
    var s=window._facAnalytics||await api.facultyAnalytics();
    window._facAnalytics=s;
    setTimeout(function(){
      if(s.dept_chart&&s.dept_chart.length)
        mkBar('facDeptBarChart',s.dept_chart.map(function(d){return d.dept;}),s.dept_chart.map(function(d){return d.att_pct;}),'#4ecba8','%');
      var dd=s.status_donut||{};
      mkDonut('facStatusDonut',['Present','Absent','Late','On Duty','Not Marked'],
        [dd.present||0,dd.absent||0,dd.late||0,dd.od||0,dd.not_marked||0],['#4ecba8','#ff7070','#ffb347','#4da6f5','#c8d6e8']);
      if(s.comparison&&s.comparison.length){
        var sl=s.comparison.slice(0,10);
        mkBar('facSubjectChart',sl.map(function(f){return f.name.split(' ').pop();}),sl.map(function(f){return f.att_pct;}),
          sl.map(function(f){return f.att_pct>=75?'#4ecba8':f.att_pct>=65?'#ffb347':'#ff7070';}),'%');
      }
    },80);
  } catch(e){}
}

async function _facTable() {
  var tbody=document.getElementById('facTbody'); if(!tbody) return;
  tbody.innerHTML='<tr><td colspan="8" style="text-align:center;padding:24px"><i class="fa fa-spinner fa-spin"></i></td></tr>';
  var dept=(document.getElementById('facMgmtDept')&&document.getElementById('facMgmtDept').value)||'';
  var search=(document.getElementById('facSearch')&&document.getElementById('facSearch').value.trim())||'';
  var date=(document.getElementById('facMgmtDate')&&document.getElementById('facMgmtDate').value)||'';
  try {
    var rows=await api.faculty(dept||null,search||null,date||null);
    FAC_STATE.allFaculty=rows;
    if(!rows.length){tbody.innerHTML='<tr><td colspan="8" style="text-align:center;padding:24px;color:var(--text3)">No faculty found.</td></tr>';return;}
    tbody.innerHTML=rows.map(_fRow).join('');
  } catch(e){ tbody.innerHTML='<tr><td colspan="8" style="text-align:center;padding:20px;color:var(--coral-d)">'+_esc(e.message)+'</td></tr>'; }
}

function _fRow(f) {
  var att=f.att_pct||0, subs=_ps(f.subjects), today=f.today_status||'not_marked';
  var in2=f.name.split(' ').filter(function(w){return w;}).map(function(w){return w[0];}).join('').slice(0,2).toUpperCase();
  return '<tr>' +
    '<td><div style="display:flex;align-items:center;gap:10px"><div class="fac-av" style="background:var(--mint-l);color:var(--mint-d)">'+in2+'</div>' +
    '<div><div style="font-weight:700">'+_esc(f.name)+'</div><div style="font-size:.72rem;color:var(--text3)">'+_esc(f.email||'—')+'</div></div></div></td>' +
    '<td><code>'+_esc(f.fac_id)+'</code></td>' +
    '<td><span class="badge b-lav">'+_esc(f.dept)+'</span></td>' +
    '<td style="font-size:.78rem;max-width:140px">'+_esc(subs.join(', ')||'—')+'</td>' +
    '<td style="font-size:.78rem;color:var(--text2)">'+_esc(f.designation||'—')+'</td>' +
    '<td>'+_tb(today)+'</td><td>'+attBar(att)+'</td>' +
    '<td><div style="display:flex;gap:6px">' +
      '<button class="btn-sm" onclick="editFacAtt(\''+f.fac_id+'\',null)"><i class="fa fa-calendar-check"></i></button>' +
      '<button class="btn-sm" onclick="viewFacDetail(\''+f.fac_id+'\')"><i class="fa fa-eye"></i></button>' +
    '</div></td></tr>';
}

function _tb(status) {
  var m={present:['b-g','✓ Present'],absent:['b-c','✗ Absent'],late:['b-amber','⏰ Late'],
         halfday:['b-w','½ Half Day'],od:['b-lav','📋 On Duty'],leave:['b-d','🌿 Leave'],not_marked:['b-d','— Not Marked']};
  var p=m[status]||m.not_marked;
  return '<span class="badge '+p[0]+'">'+p[1]+'</span>';
}

function _ps(raw) {
  if(!raw) return [];
  if(Array.isArray(raw)) return raw;
  try{return JSON.parse(raw);}catch{return [raw];}
}

function openMarkFacultyModal(){FAC_STATE.editLogId=null;FAC_STATE.editFacId=null;_facModal(null,null);}
function editFacAtt(fid,lid){FAC_STATE.editFacId=fid;FAC_STATE.editLogId=lid||null;_facModal(fid,lid||null);}

async function _facModal(pfid,lid) {
  var fs=document.getElementById('fam_faculty');
  if(fs){
    fs.innerHTML='<option value="">Select faculty...</option>';
    var rows=FAC_STATE.allFaculty.length?FAC_STATE.allFaculty:await api.faculty().catch(function(){return[];});
    rows.forEach(function(f){var o=document.createElement('option');o.value=f.fac_id;o.textContent=f.name+' ('+f.fac_id+')';if(f.fac_id===pfid)o.selected=true;fs.appendChild(o);});
  }
  var df=document.getElementById('fam_date'); if(df&&!df.value) df.value=new Date().toISOString().slice(0,10);
  var sf=document.getElementById('fam_status'); if(sf) sf.value='present';
  var tf=document.getElementById('fam_time');   if(tf) tf.value='09:00';
  var rf=document.getElementById('fam_reason'); if(rf) rf.value='';
  var uf=document.getElementById('fam_updater');if(uf) uf.value='';
  if(lid&&pfid){
    try{
      var det=await api.facultyDetail(pfid);
      var log=(det.attendance_log||[]).find(function(l){return l.id===lid;});
      if(log){if(sf)sf.value=log.status||'present';if(df)df.value=log.att_date||df.value;if(tf&&log.arrival_time)tf.value=log.arrival_time;if(rf)rf.value=log.reason||'';if(uf)uf.value=log.updated_by||'';}
    }catch{}
  }
  var hd=document.querySelector('#facAttModal .modal-header h3');
  if(hd) hd.innerHTML=lid?'<i class="fa fa-pen-to-square"></i> Edit Faculty Attendance':'<i class="fa fa-user-clock"></i> Mark Faculty Attendance';
  var m=document.getElementById('facAttModal'); if(m) m.classList.remove('dn');
}

async function saveFacultyAttendance() {
  var fid=document.getElementById('fam_faculty')?.value?.trim();
  var ad=document.getElementById('fam_date')?.value;
  var st=document.getElementById('fam_status')?.value;
  var tm=document.getElementById('fam_time')?.value;
  var re=document.getElementById('fam_reason')?.value.trim();
  var up=document.getElementById('fam_updater')?.value.trim();
  if(!fid){toast('Select a faculty member','warn');return;}
  if(!ad){toast('Date is required','warn');return;}
  if(!up){toast('Updated By field is required','warn');return;}
  var payload={fac_id:fid,att_date:ad,status:st,arrival_time:tm||null,reason:re,updated_by:up};
  var sb=document.querySelector('#facAttModal .btn-primary');
  if(sb){sb.disabled=true;sb.innerHTML='<i class="fa fa-spinner fa-spin"></i> Saving...';}
  try{
    if(FAC_STATE.editLogId) await api.editFacAttApi(FAC_STATE.editFacId,FAC_STATE.editLogId,payload),toast('Attendance record updated!','success');
    else await api.markFacAtt(payload),toast('Faculty attendance marked!','success');
    closeModal('facAttModal'); await renderFacultyPage();
  }catch(e){toast('Save failed: '+e.message,'error');}
  finally{if(sb){sb.disabled=false;sb.innerHTML='<i class="fa fa-save"></i> Save';}}
}

async function viewFacDetail(fid) {
  var tel=document.getElementById('infoModalTitle'), bel=document.getElementById('infoModalBody');
  if(!tel||!bel) return;
  tel.innerHTML='<i class="fa fa-user-tie"></i> Faculty Profile';
  bel.innerHTML='<div style="text-align:center;padding:24px"><i class="fa fa-spinner fa-spin fa-2x"></i></div>';
  var m=document.getElementById('infoModal'); if(m) m.classList.remove('dn');
  try{
    var f=await api.facultyDetail(fid), subs=_ps(f.subjects), st=getStatus(f.att_pct||0);
    var in2=f.name.split(' ').filter(function(w){return w;}).map(function(w){return w[0];}).join('').slice(0,2).toUpperCase();
    var mo=f.monthly||[], cid='facDetailChart_'+fid, lr='';
    (f.attendance_log||[]).slice(0,10).forEach(function(l){
      lr+='<tr><td style="font-family:var(--mono)">'+_esc(l.att_date)+'</td><td>'+_tb(l.status)+'</td>' +
        '<td style="font-family:var(--mono)">'+(l.arrival_time||'—')+'</td>' +
        '<td style="font-size:.75rem;color:var(--text2)">'+_esc(l.reason||'—')+'</td>' +
        '<td style="font-size:.72rem;color:var(--text3)">'+_esc(l.updated_by||'—')+'</td></tr>';
    });
    if(!lr) lr='<tr><td colspan="5" style="text-align:center;padding:16px;color:var(--text3)">No log yet</td></tr>';

    bel.innerHTML=
      '<div style="display:flex;align-items:center;gap:16px;margin-bottom:20px">' +
        '<div class="fac-av fac-av--lg" style="background:var(--lav-l);color:var(--lav-d);font-size:1.6rem">'+in2+'</div>' +
        '<div><div style="font-size:1.15rem;font-weight:800">'+_esc(f.name)+'</div>' +
        '<div style="font-size:.8rem;color:var(--text2)">'+_esc(f.designation||'—')+' · '+_esc(f.dept)+'</div>' +
        '<div style="font-size:.75rem;color:var(--text3);margin-top:3px"><i class="fa fa-envelope"></i> '+_esc(f.email||'—')+' &nbsp;·&nbsp; <i class="fa fa-phone"></i> '+_esc(f.mobile||'—')+'</div></div>' +
      '</div>' +
      '<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:18px">' +
        _msb('Att %',(f.att_pct||0)+'%',st.color)+_msb('Present',f.present_days||0,'var(--mint-d)')+
        _msb('Absent',f.absent_days||0,'var(--coral-d)')+_msb('Total',f.total_days||0,'var(--text2)') +
      '</div>' +
      '<div style="margin-bottom:14px"><div style="font-size:.72rem;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px">Subjects</div>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap">'+(subs.length?subs.map(function(s){return'<span class="badge b-lav">'+_esc(s)+'</span>';}).join(''):'<em style="color:var(--text3)">—</em>')+'</div></div>' +
      (mo.length?'<div style="margin-bottom:14px"><div style="font-size:.72rem;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Monthly %</div><canvas id="'+cid+'" height="90"></canvas></div>':'') +
      '<div><div style="font-size:.72rem;font-weight:700;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">Recent Log</div>' +
        '<div class="table-scroll" style="max-height:200px"><table class="data-tbl"><thead><tr><th>Date</th><th>Status</th><th>Time</th><th>Reason</th><th>By</th></tr></thead><tbody>'+lr+'</tbody></table></div></div>';

    if(mo.length) setTimeout(function(){
      mkBar(cid,mo.map(function(m){return m.month;}),mo.map(function(m){return m.pct;}),mo.map(function(m){return m.pct>=75?'#4ecba8':m.pct>=65?'#ffb347':'#ff7070';}),'%');
    },80);

    var ft=document.querySelector('#infoModal .modal-footer');
    if(ft) ft.innerHTML='<button class="btn-secondary" onclick="closeModal(\'infoModal\')">Close</button>' +
      '<button class="btn-primary" onclick="editFacAtt(\''+fid+'\',null);closeModal(\'infoModal\')"><i class="fa fa-calendar-check"></i> Mark Attendance</button>';
  }catch(e){ bel.innerHTML='<div style="color:var(--coral-d);padding:20px">'+_esc(e.message)+'</div>'; }
}

async function exportFacultyCSV() {
  var dept=(document.getElementById('facMgmtDept')&&document.getElementById('facMgmtDept').value)||'';
  try{
    var res=await api.exportFacultyCSV(dept||null), blob=await res.blob();
    var a=Object.assign(document.createElement('a'),{href:URL.createObjectURL(blob),download:'faculty_attendance_'+new Date().toISOString().slice(0,10)+'.csv'});
    a.click(); toast('CSV exported!','success');
  }catch(e){toast('Export failed: '+e.message,'error');}
}

/* ══════════════════════════════════════════════════════════════
   DOM READY
   ══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', function() {
  window.initDeptDrill        = initDeptDrill;
  window.updateBreadcrumb     = updateBreadcrumb;
  window.renderDeptCards      = renderDeptCards;
  window.drillToCategory      = drillToCategory;
  window.drillToDept          = drillToDept;
  window.drillToYear          = drillToYear;
  window.drillToSem           = drillToSem;
  window.drillToClass         = drillToClass;
  window.drillToSubject       = drillToSubject;
  window.drillToStudentDetail = drillToStudentDetail;
  window.filterDrillStudents  = filterDrillStudents;
  window.drillToCourse        = drillToCourse;
  window.drillToSection       = drillToSection;
  window.drillToSectionDetail = drillToSectionDetail;
  window.kpiMini              = kpiMini;
  window.statusBadge          = statusBadge;
  window.renderFacultyPage    = renderFacultyPage;
  window.openMarkFacultyModal = openMarkFacultyModal;
  window.editFacAtt           = editFacAtt;
  window.saveFacultyAttendance= saveFacultyAttendance;
  window.viewFacDetail        = viewFacDetail;
  window.exportFacultyCSV     = exportFacultyCSV;
});