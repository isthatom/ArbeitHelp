let authToken = localStorage.getItem('auth_token');
let timeSeriesChart = null, roleChart = null, diffChart = null, distChart = null;

async function init() {
    authToken = localStorage.getItem('auth_token');
    if (!authToken) { location.href = 'index.html'; return; }

    var r = await fetch('/stats/unlock-status', { headers: authHeaders(authToken) });
    var d = await r.json();
    if (!d.unlocked) {
        document.getElementById('lock-answered').textContent = d.answered;
        document.getElementById('lock-required-count').textContent = d.required;
        document.getElementById('lock-required-total').textContent = d.required;
        document.getElementById('lock-screen').classList.remove('hidden');
        return; 
    }
    document.getElementById('lock-screen').classList.add('hidden');
    document.getElementById('stats-content').classList.remove('hidden');

    renderLastSession();

    fetch('/stats/summary', { headers: authHeaders(authToken) })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            document.getElementById('stat-total-qs').textContent = d.total_questions;
            document.getElementById('stat-avg').textContent = d.avg_score.toFixed(1);
            document.getElementById('start-streak').textContent = d.streak;
            document.getElementById('stat-best-role').textContent = d.best_role;
        })
        .catch(function() { alert('stat failed'); });

    fetch('/stats/chart-data', { headers: authHeaders(authToken) })
        .then(function(r) { return r.json(); })
        .then(function(d) { buildCharts(d); })
        .catch(function() {});
}

function renderLastSession() {
    const section = document.getElementById('last-session-section');
    if (!section) return;
    let summary = null;
    let at = null;
    try {
        const raw = localStorage.getItem('last_session_summary');
        const rawAt = localStorage.getItem('last_session_at');
        if (raw) summary = JSON.parse(raw);
        if (rawAt) at = rawAt;
    } catch {}
    if (!summary || !Array.isArray(summary.questions)) {
        section.classList.add('hidden');
        return;
    }
    section.classList.remove('hidden');
    const meta = document.getElementById('last-session-meta');
    if (meta) {
        const when = at ? new Date(at).toLocaleString() : '';
        const answered = summary.questions_answered ?? summary.questions.filter(q => !q.skipped).length;
        const total = summary.session_length ?? 5;
        const score = summary.session_score ?? summary.total_points ?? 0;
        const role = summary.role || '—';
        meta.textContent = role.toUpperCase() + ' • ' + answered + '/' + total + ' answered • ' + score + '/' + (total * 3) + ' pts' + (when ? ' • ' + when : '') + ' • THIS DEVICE ONLY';
    }
    const cardsHost = document.getElementById('last-session-cards');
    if (cardsHost) {
        cardsHost.replaceChildren();
        const makeCard = (value, label) => {
            const card = document.createElement('div');
            card.className = 'stat-card';
            const span = document.createElement('span');
            span.textContent = value;
            const lab = document.createElement('label');
            lab.textContent = label;
            card.appendChild(span);
            card.appendChild(lab);
            return card;
        };
        const answered = summary.questions_answered ?? summary.questions.filter(q => !q.skipped).length;
        const total = summary.session_length ?? 5;
        const score = summary.session_score ?? summary.total_points ?? 0;
        cardsHost.appendChild(makeCard(score + '/' + (total * 3), 'THIS SESSION SCORE'));
        cardsHost.appendChild(makeCard(answered + '/' + total, 'ANSWERED'));
        if (summary.jd_coverage && (summary.jd_coverage.covered || summary.jd_coverage.missed)) {
            const covered = (summary.jd_coverage.covered || []).length;
            const missed = (summary.jd_coverage.missed || []).length;
            cardsHost.appendChild(makeCard(covered + '/' + (covered + missed), 'JD COVERED'));
        }
    }
    const recapHost = document.getElementById('last-session-recap');
    if (recapHost) {
        recapHost.replaceChildren();
        (summary.questions || []).forEach((q, i) => {
            const item = document.createElement('div');
            item.className = 'recap-item';
            const head = document.createElement('div');
            head.className = 'recap-head';
            const qLabel = document.createElement('span');
            qLabel.className = 'recap-qnum';
            qLabel.textContent = 'Q' + (i + 1);
            const topic = document.createElement('span');
            topic.className = 'recap-topic';
            topic.textContent = (q.topic || 'GENERAL').toUpperCase();
            const pts = document.createElement('span');
            const skipped = !!q.skipped;
            pts.className = 'recap-pts ' + (skipped ? 'skipped' : (q.points === 3 ? 'good' : (q.points === 0 ? 'bad' : 'mid')));
            pts.textContent = skipped ? 'SKIPPED' : ((q.points ?? 0) + '/3 PTS');
            head.appendChild(qLabel);
            head.appendChild(topic);
            head.appendChild(pts);
            if (q.hint_used) {
                const tag = document.createElement('span');
                tag.className = 'recap-hint';
                tag.textContent = 'HINT';
                head.appendChild(tag);
            }
            item.appendChild(head);
            const qText = document.createElement('p');
            qText.className = 'recap-qtext';
            qText.textContent = q.question;
            item.appendChild(qText);
            if (q.feedback) {
                const fb = document.createElement('p');
                fb.className = 'recap-feedback';
                fb.textContent = q.feedback;
                item.appendChild(fb);
            }
            recapHost.appendChild(item);
        });
        if (summary.jd_coverage && (summary.jd_coverage.covered?.length || summary.jd_coverage.missed?.length)) {
            const jd = summary.jd_coverage;
            const covered = jd.covered || [];
            const missed = jd.missed || [];
            const box = document.createElement('div');
            box.className = 'jd-coverage';
            const head = document.createElement('div');
            head.className = 'jd-coverage-head';
            head.textContent = 'JD COVERAGE — THIS SESSION';
            box.appendChild(head);
            [['covered','COVERED'],['missed','MISSED']].forEach(([key,label]) => {
                const terms = key === 'covered' ? covered : missed;
                if (!terms.length) return;
                const rowEl = document.createElement('div');
                rowEl.className = 'jd-row';
                const lbl = document.createElement('span');
                lbl.className = 'jd-label ' + key;
                lbl.textContent = label;
                rowEl.appendChild(lbl);
                terms.forEach(term => {
                    const chip = document.createElement('span');
                    chip.className = 'jd-chip ' + key;
                    chip.textContent = term;
                    rowEl.appendChild(chip);
                });
                box.appendChild(rowEl);
            });
            recapHost.appendChild(box);
        }
    }
}

function renderWeakTopics(topics) {
    var section = document.getElementById('weak-topics');
    var listEl = document.getElementById('weak-topics-list');
    if (!section || !listEl) return;
    if (!topics || !topics.length) {
        section.style.display = 'none';
        return;
    }
    section.style.display = '';
    listEl.replaceChildren();
    topics.forEach(function(t) {
        var row = document.createElement('div');
        row.className = 'weak-topic-row';
        var name = document.createElement('span');
        name.className = 'weak-topic-name';
        name.textContent = t.topic;
        var meta = document.createElement('span');
        meta.className = 'weak-topic-meta';
        meta.textContent = t.avg_score.toFixed(1) + ' AVG // ' + t.count + 'X';
        row.appendChild(name);
        row.appendChild(meta);
        listEl.appendChild(row);
    });
}

function buildCharts(d) {
    renderWeakTopics(d.weak_topics || []);
    if (!d.time_series || !d.time_series.length) {
        var cans = document.querySelectorAll('.chart-container canvas');
        for (let i = 0; i < cans.length; i++) cans[i].style.display = 'none';
        document.querySelector('.stats-action').style.display = 'none';
        var msg = document.createElement('div');
        msg.textContent = 'No interview history yet. Do an interview to see something here first!';
        msg.style.cssText = 'text-align:center;padding:40px;color:#999;font-family:VT323;font-size:1.2rem;';
        document.querySelector('.chart-container').appendChild(msg);
        return;
    }

    var ctx1 = document.getElementById('chart-time-series').getContext('2d');
    if (timeSeriesChart) timeSeriesChart.destroy();
    timeSeriesChart = new Chart(ctx1, {
        type: 'line',
        data: {
            labels: d.time_series.map(function(x) { return x.index; }),
            datasets: [{
                label: 'Score',
                data: d.time_series.map(function(x) { return x.score; }),
                borderColor: '#3b82f6',
                backgroundColor: 'rgba(59,130,246,0.1)',
                borderWidth: 3,
                fill: true,
                tension: 0.3,
                pointRadius: 5,
                pointHoverRadius: 7,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: true,
            plugins: { legend: { display: false }, title: { display: true, text: 'Score Over Time (All Sessions)', font: { family: 'Press Start 2P', size: 16 }}},
            scales: {
                y: { beginAtZero: true, max: 3, ticks: { stepSize: 1, font: { family: 'VT323', size: 12 }}, title: { display: true, text: 'Score (0-3)', font: { family: 'Press Start 2P', size: 12 }}},
                x: { ticks: { font: { family: 'VT323', size: 12 }}, title: { display: true, text: 'Question #', font: { family: 'Press Start 2P', size: 12 }}},
            }
        }
    });

    if (d.by_role && Object.keys(d.by_role).length) {
        var ctx2 = document.getElementById('chart-by-role').getContext('2d');
        if (roleChart) roleChart.destroy();
        var roles = Object.keys(d.by_role);
        roleChart = new Chart(ctx2, {
            type: 'bar',
            data: { labels: roles, datasets: [{ label: 'Avg Score', data: roles.map(function(r){ return d.by_role[r]; }), backgroundColor: '#22c55e', borderColor: '#16a34a', borderWidth: 2, borderRadius: 6 }]},
            options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { display: false }, title: { display: true, text: 'Average Score by Role', font: { family: 'Press Start 2P', size: 16 }}}, scales: { y: { beginAtZero: true, max: 3, ticks: { stepSize: 1, font: { family: 'VT323', size: 12 }}}, x: { ticks: { font: { family: 'VT323', size: 12 }} } } }
        });
    }

    if (d.by_difficulty && Object.keys(d.by_difficulty).length) {
        var ctx3 = document.getElementById('chart-by-difficulty').getContext('2d');
        if (diffChart) diffChart.destroy();
        var diffs = Object.keys(d.by_difficulty);
        var colors = {'easy': '#22c55e', 'medium': '#f59e0b', 'hard': '#ef4444'};
        diffChart = new Chart(ctx3, {
            type: 'bar',
            data: { labels: diffs.map(function(x){ return x[0].toUpperCase() + x.slice(1); }), datasets: [{ label: 'Avg Score', data: diffs.map(function(x){ return d.by_difficulty[x]; }), backgroundColor: diffs.map(function(x){ return colors[x]; }), borderWidth: 2, borderRadius: 6 }]},
            options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { display: false }, title: { display: true, text: 'Average Score by Difficulty', font: { family: 'Press Start 2P', size: 16 }}}, scales: { y: { beginAtZero: true, max: 3, ticks: { stepSize: 1, font: { family: 'VT323', size: 12 }}}, x: { ticks: { font: { family: 'VT323', size: 12 }} } } }
        });
    }
////
    if (d.distribution) {
        var ctx4 = document.getElementById('chart-distribution').getContext('2d');
        if (distChart) distChart.destroy();
        distChart = new Chart(ctx4, {
            type: 'doughnut',
            data: { labels: ['0 pts','1 pt','2 pts','3 pts'], datasets: [{ data: [d.distribution[0]||0, d.distribution[1]||0, d.distribution[2]||0, d.distribution[3]||0], backgroundColor: ['#ef4444','#f59e0b','#22c55e','#16a34a'], borderWidth: 2, borderColor: '#fff' }]},
            options: { responsive: true, maintainAspectRatio: true, plugins: { legend: { position: 'bottom', labels: { font: { family: 'VT323', size: 14 }}}, title: { display: true, text: 'Score Distribution', font: { family: 'Press Start 2P', size: 16 }} }}
        });
    }
}

async function downloadBlob(url, filename) {
    var r = await fetch(url, { headers: authHeaders(authToken) });
    if (r.status === 401) {
        localStorage.removeItem('auth_token');
        location.href = 'index.html';
        return false;
    }
    if (!r.ok) throw new Error('download failed');
    var blob = await r.blob();
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
    return true;
}

async function exportJSON() {
    var btn = document.getElementById('export-all-json');
    var txt = btn.textContent;
    btn.disabled = true; btn.textContent = 'EXPORTING...';
    try {
        await downloadBlob('/stats/export/json', 'arbiethelp_history_' + new Date().toISOString().split('T')[0] + '.json');
    } catch(e) { alert('Export failed'); }
    finally { btn.disabled = false; btn.textContent = txt; }
}

function exportPDF() {
    var btn = document.getElementById('export-all-pdf');
    var txt = btn.textContent;
    btn.disabled = true; btn.textContent = 'GENERATING PDF...';
    downloadBlob('/stats/export/pdf', 'arbiethelp_report_' + new Date().toISOString().split('T')[0] + '.pdf')
        .catch(function(e) { if (e !== 'auth') alert('PDF generation failed'); })
        .finally(function() { btn.disabled = false; btn.textContent = txt; });
}

if (document.readyState != 'loading') init();
else document.addEventListener('DOMContentLoaded', init);
