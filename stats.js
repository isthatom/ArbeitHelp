var authToken = localStorage.getItem('auth_token');
var timeSeriesChart = null, roleChart = null, diffChart = null, distChart = null;

async function init() {
    authToken = localStorage.getItem('auth_token');
    if (!authToken) { location.href = 'index.html'; return; }

    var r = await fetch('/stats/unlock-status', { headers: { 'Authorisation': 'Bearer ' + authToken }});
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

    fetch('/stats/summary', { headers: { 'Authorisation': 'Bearer ' + authToken }})
        .then(function(r) { return r.json(); })
        .then(function(d) {
            document.getElementById('stat-total-qs').textContent = d.total_questions;
            document.getElementById('stat-avg').textContent = d.avg_score.toFixed(1);
            document.getElementById('start-streak').textContent = d.streak;
            document.getElementById('stat-best-role').textContent = d.best_role;
        })
        .catch(function() { alert('stat failed'); });

    fetch('/stats/chart-data', { headers: { 'Authorisation': 'Bearer ' + authToken }})
        .then(function(r) { return r.json(); })
        .then(function(d) { buildCharts(d); })
        .catch(function() {});
}

function buildCharts(d) {
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
            plugins: { legend: { display: false }, title: { display: true, text: 'Score Over Time', font: { family: 'Press Start 2P', size: 16 }}},
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

async function exportJSON() {
    var btn = document.getElementById('export-all-json');
    var txt = btn.textContent;
    btn.disabled = true; btn.textContent = 'EXPORTING...';
    try {
        var r = await fetch('/stats/export/json', { headers: { 'Authorisation': 'Bearer ' + authToken }});
        if (r.status === 401) { localStorage.removeItem('auth_token'); location.href = 'index.html'; return; }
        var blob = await r.blob();
        var a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'unjobless_history_' + new Date().toISOString().split('T')[0] + '.json';
        a.click();
        URL.revokeObjectURL(a.href);
    } catch(e) { alert('Export failed'); }
    finally { btn.disabled = false; btn.textContent = txt; }
}

function exportPDF() {
    var btn = document.getElementById('export-all-pdf');
    var txt = btn.textContent;
    btn.disabled = true; btn.textContent = 'GENERATING PDF...';
    fetch('/stats/export/pdf', { headers: { 'Authorisation': 'Bearer ' + authToken }})
        .then(function(r) {
            if (r.status === 401) { localStorage.removeItem('auth_token'); location.href = 'index.html'; throw 'auth'; }
            return r.blob();
        })
        .then(function(blob) {
            var a = document.createElement('a');
            a.href = URL.createObjectURL(blob);
            a.download = 'unjobless_report_' + new Date().toISOString().split('T')[0] + '.pdf';
            a.click();
            URL.revokeObjectURL(a.href);
        })
        .catch(function(e) { if (e !== 'auth') alert('PDF generation failed'); })
        .finally(function() { btn.disabled = false; btn.textContent = txt; });
}

if (document.readyState != 'loading') init();
else document.addEventListener('DOMContentLoaded', init);
