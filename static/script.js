// defining the functions for loading roles
let selectedRole = null;
let aiOnline = true;
let authToken = null;
let jdPreviewOk = false;

async function loadRoles() {
    const res = await fetch('/roles');
    const roles = await res.json()
    // classifying them to be able to be recognizable by HTML file
    const grid = document.getElementById('role-grid');
    if (grid) {
        grid.replaceChildren();
        roles.forEach(r => {
            const btn = document.createElement('button');
            btn.className = 'role-btn';
            btn.type = 'button';
            btn.dataset.role = r.name;
            btn.addEventListener('click', () => selectRole(r.name));

            const title = document.createElement('span');
            title.textContent = `${r.emoji} ${r.name}`;
            btn.appendChild(title);

            const tagline = document.createElement('span');
            tagline.textContent = r.tagline;
            btn.appendChild(tagline);

            grid.appendChild(btn);
        }); // format of the role names, done to avoid repetitions
    }
    const proof = document.getElementById('hero-proof-text');
    if (proof) {
        const n = Array.isArray(roles) ? roles.length : 0;
        proof.textContent = (n ? n : '5') + ' roles · mixed · easy · medium · hard · JD-aware';
    }
}

// select (not start) a role; the START button begins the session
function selectRole(roleName) {
    selectedRole = roleName;
    document.querySelectorAll('.role-btn').forEach(btn => {
        btn.classList.toggle('selected', btn.dataset.role === roleName);
    });
    const grid = document.getElementById('role-grid');
    if (grid) grid.classList.remove('attention');
    hideStartMsg();
    updateStartButton();
}

function jdText() {
    const el = document.getElementById('jd-text');
    return el ? el.value.trim() : '';
}

function customRoleText() {
    const el = document.getElementById('custom-role');
    return el ? el.value.trim() : '';
}

function startMode() {
    if (selectedRole) return jdText() ? 'tile-jd' : 'tile';
    if (customRoleText() && jdText() && jdPreviewOk) return 'custom';
    return null;
}

const START_LABELS = {
    tile: 'START INTERVIEW ->',
    'tile-jd': 'START JD-MATCHED INTERVIEW ->',
    custom: 'START CUSTOM ROLE INTERVIEW ->'
};

function updateStartButton() {
    const btn = document.getElementById('start-interview-btn');
    if (!btn) return;
    const mode = startMode();
    btn.classList.toggle('no-role', mode === null);
    btn.textContent = START_LABELS[mode] || START_LABELS.tile;
}

function invalidateJdPreview() {
    jdPreviewOk = false;
    const panel = document.getElementById('jd-preview');
    if (panel) panel.classList.add('hidden');
}

// ask the backend to parse the pasted JD and show what was detected
async function analyzeJd() {
    const btn = document.getElementById('analyze-jd-btn');
    const panel = document.getElementById('jd-preview');
    const jd = jdText();
    if (!btn || !panel) return;
    if (!jd) {
        const status = document.getElementById('jd-status');
        if (status) {
            status.textContent = 'PASTE A JOB DESCRIPTION FIRST.';
            status.className = 'status-line warn';
        }
        return;
    }
    btn.disabled = true;
    btn.textContent = '...ANALYZING...';
    try {
        const res = await fetchJSON('/jd/preview', {
            method: 'POST',
            headers: authHeaders(authToken),
            body: JSON.stringify({job_description: jd})
        });
        renderJdPreview(panel, await res.json());
    } catch (err) {
        if (err.status === 401 || err.status === 403) {
            authToken = null;
            localStorage.removeItem('auth_token');
        }
        jdPreviewOk = false;
        panel.replaceChildren();
        panel.append(document.createTextNode('COULD NOT ANALYZE THE JOB DESCRIPTION RIGHT NOW.'));
        panel.classList.remove('hidden');
    } finally {
        btn.disabled = false;
        btn.textContent = 'ANALYZE JOB DESCRIPTION';
    }
}

function renderJdPreview(panel, data) {
    panel.replaceChildren();
    const signals = data.signals || {};
    const groups = [['SKILLS', signals.skills], ['TOOLS', signals.tools], ['FOCUS', signals.focus_areas]];
    const hasSignals = groups.some(([, terms]) => Array.isArray(terms) && terms.length > 0) || !!signals.seniority;

    const head = document.createElement('div');
    head.className = 'jd-preview-head';
    head.textContent = hasSignals
        ? (data.ai_used ? 'SIGNALS DETECTED' + (signals.seniority ? ' - LEVEL: ' + signals.seniority.toUpperCase() : '') : 'NO SIGNALS DETECTED')
        : 'NOTHING DETECTED';
    panel.appendChild(head);

    if (!hasSignals || data.ai_used === false) {
        const note = document.createElement('p');
        note.className = 'status-line ' + (hasSignals ? 'ok' : 'warn');
        note.textContent = hasSignals
            ? 'ANALYSIS CACHED FROM A PREVIOUS AI RUN.'
            : (aiOnline ? 'THE JD DID NOT YIELD USABLE SIGNALS. QUESTIONS WILL FOLLOW YOUR ROLE NAME ONLY.'
                        : 'AI IS OFFLINE - ANALYSIS UNAVAILABLE, CUSTOM ROLES CANNOT START.');
        panel.appendChild(note);
        jdPreviewOk = hasSignals && aiOnline;
    } else {
        jdPreviewOk = true;
    }

    groups.forEach(([label, terms]) => {
        if (!Array.isArray(terms) || !terms.length) return;
        const rowEl = document.createElement('div');
        rowEl.className = 'jd-row';
        const lbl = document.createElement('span');
        lbl.className = 'jd-label';
        lbl.textContent = label;
        rowEl.appendChild(lbl);
        terms.forEach(term => {
            const chip = document.createElement('span');
            chip.className = 'pv-chip';
            chip.textContent = term;
            rowEl.appendChild(chip);
        });
        panel.appendChild(rowEl);
    });

    if (jdPreviewOk && !selectedRole) {
        const ready = document.createElement('p');
        ready.className = 'status-line ok';
        ready.textContent = 'READY - PRESS START CUSTOM ROLE INTERVIEW.';
        panel.appendChild(ready);
    }
    panel.classList.remove('hidden');
}

function showStartMsg(text) {
    const msg = document.getElementById('start-msg');
    if (!msg) return;
    msg.textContent = text;
    msg.classList.remove('hidden');
}

function hideStartMsg() {
    const msg = document.getElementById('start-msg');
    if (msg) msg.classList.add('hidden');
}

// begin the interview for the selected tile role, or a custom role + analyzed JD
function startInterview() {
    const mode = startMode();
    if (!mode) {
        showStartMsg('PICK A ROLE TILE, OR ENTER A CUSTOM ROLE, PASTE ITS JOB DESCRIPTION AND RUN ANALYZE.');
        const grid = document.getElementById('role-grid');
        if (grid) {
            grid.classList.remove('attention');
            void grid.offsetWidth;
            grid.classList.add('attention');
        }
        return;
    }
    const role = selectedRole || customRoleText();
    const select = document.getElementById('difficulty-select');
    const difficulty = (select && select.value) ? encodeURIComponent(select.value) : 'mixed';
    const jd = jdText();
    if (jd) {
        sessionStorage.setItem('jd_text', jd);
    } else {
        sessionStorage.removeItem('jd_text');
    }
    window.location.href = `questions.html?role=${encodeURIComponent(role)}&difficulty=${difficulty}`;
}

// live feedback while typing the job description
function initJdInput() {
    const el = document.getElementById('jd-text');
    const status = document.getElementById('jd-status');
    const custom = document.getElementById('custom-role');
    if (!el || !status) return;
    el.addEventListener('input', () => {
        invalidateJdPreview();
        if (el.value.trim()) {
            status.textContent = aiOnline
                ? 'JD ATTACHED - QUESTIONS WILL BE WEIGHTED TOWARD IT.'
                : 'JD ATTACHED - BUT AI IS OFFLINE, SO ONLY KNOWN ROLES CAN USE THE PRACTICE BANK.';
            status.className = 'status-line ' + (aiOnline ? 'ok' : 'warn');
        } else {
            status.className = 'status-line hidden';
            status.textContent = '';
        }
        updateStartButton();
    });
    if (custom) {
        custom.addEventListener('input', () => {
            invalidateJdPreview();
            updateStartButton();
        });
    }
}

// surface AI availability so the JD flow never silently pretends to match
async function checkAiMode() {
    try {
        const res = await fetch('/health');
        const data = await res.json();
        aiOnline = !!data.ai_enabled;
    } catch (err) {
        aiOnline = false;
    }
    const note = document.getElementById('ai-mode-note');
    if (note && !aiOnline) {
        note.textContent = 'AI offline — JD-matched questions will fall back to the practice bank.';
        note.classList.remove('hidden');
    }
    const navStatus = document.getElementById('nav-status-text');
    if (navStatus) navStatus.textContent = aiOnline ? 'AI online' : 'AI offline';
    const dot = document.querySelector('.site-nav .status-dot');
    if (dot) {
        dot.classList.toggle('online', aiOnline);
        dot.style.background = aiOnline ? 'var(--accent)' : 'var(--muted)';
    }
    const jdEl = document.getElementById('jd-text');
    if (jdEl && jdEl.value.trim()) {
        jdEl.dispatchEvent(new Event('input'));
    }
}

// typerwritr effect
function runTypewriting() {
    const el = document.getElementById('typewriter-text');
    if (!el) return;

    const text = 'no fluff. no filler. just interview prep.';
    let i = 0;

    function type() {
        if (i< text.length) {
            el.replaceChildren();
            el.append(document.createTextNode(text.slice(0, i + 1)));
            const cursor = document.createElement('span');
            cursor.className = 'typewriter-cursor';
            cursor.textContent = '_';
            el.appendChild(cursor);
            i++;
            setTimeout(type,55);

        } else {
            el.replaceChildren();
            el.append(document.createTextNode(text));
            const cursor = document.createElement('span');
            cursor.className = 'typewriter-cursor';
            cursor.textContent = '_';
            el.appendChild(cursor);
        }
    }
    setTimeout(type, 750);
}

function initReveal () {
    const elemnts = document.querySelectorAll('.reveal:not(.visible)');
    const observer = new IntersectionObserver((entries)=> {
        entries.forEach(entry => {
            if (entry.isIntersecting) {
                entry.target.classList.add('visible');
                observer.unobserve(entry.target);
            }
        });
    }, { threshold: 0.12});

    elemnts.forEach(el => observer.observe(el));
}

function maybeShowTour() {
    try {
        if (localStorage.getItem('arbeithelp_tour_seen_v1')) return;
    } catch { return; }
    const root = document.getElementById('tour-root');
    if (!root) return;
    const card = document.createElement('div');
    card.className = 'tour-card';
    card.setAttribute('role', 'dialog');
    card.setAttribute('aria-label', 'Quick tour');
    const head = document.createElement('div');
    head.className = 'tour-head';
    const title = document.createElement('div');
    title.className = 'tour-title';
    title.textContent = 'Quick tour — 30 seconds';
    const close = document.createElement('button');
    close.className = 'tour-close';
    close.setAttribute('aria-label', 'Dismiss tour');
    close.textContent = '×';
    close.onclick = () => { card.remove(); try { localStorage.setItem('arbeithelp_tour_seen_v1','1'); } catch {} };
    head.appendChild(title);
    head.appendChild(close);
    const steps = document.createElement('div');
    steps.className = 'tour-steps';
    [
        ['1', 'Pick a role & difficulty, paste a JD if you have one.'],
        ['2', 'Get graded 0–3 — a hint caps that question at 2/3.'],
        ['3', 'See your recap and copy it — stats are all-time.'],
    ].forEach(([n, t]) => {
        const row = document.createElement('div');
        row.className = 'tour-step';
        const num = document.createElement('span');
        num.className = 'tour-num';
        num.textContent = n;
        const txt = document.createElement('span');
        txt.textContent = t;
        row.appendChild(num);
        row.appendChild(txt);
        steps.appendChild(row);
    });
    const actions = document.createElement('div');
    actions.className = 'tour-actions';
    const prog = document.createElement('span');
    prog.className = 'tour-progress';
    prog.textContent = 'Shows once';
    const btn = document.createElement('button');
    btn.className = 'btn-primary';
    btn.style.padding = '8px 14px';
    btn.style.fontSize = '0.82rem';
    btn.textContent = 'Got it';
    btn.onclick = () => { card.remove(); try { localStorage.setItem('arbeithelp_tour_seen_v1','1'); } catch {} };
    actions.appendChild(prog);
    actions.appendChild(btn);
    card.appendChild(head);
    card.appendChild(steps);
    card.appendChild(actions);
    root.appendChild(card);
    const onKey = (e) => { if (e.key === 'Escape') { card.remove(); try { localStorage.setItem('arbeithelp_tour_seen_v1','1'); } catch {} window.removeEventListener('keydown', onKey); } };
    window.addEventListener('keydown', onKey);
}

// --boot---
async function init() {
    authToken = await initAuth();
    loadRoles();
    initJdInput();
    checkAiMode();
    updateStartButton();
    runTypewriting();
    initReveal();
    setTimeout(maybeShowTour, 700);
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
