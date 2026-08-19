const TIMER_SECONDS = 120;
const SESSION_LENGTH_DEFAULT = 5;

let role = null;
let questionsAnswered = 0;
let currentQuestionNumber = 1;
let sessionLength = SESSION_LENGTH_DEFAULT;
let sessionScore = 0;
let timeRemaining = TIMER_SECONDS;
let timerInterval = null;
let timerHidden = false;
let authToken = null;
let sessionToken = null;
let sessionCompleted = false;
let currentImproved = '';
let currentChanges = [];
let currentCorrectionUsesAI = false;
let submitAbortControl = null;
let isSubmitting = false;
let legacyRetries = 0;

function el(id) {
    return document.getElementById(id);
}

function authHeaders(token) {
    return {'Content-Type': 'application/json', 'Authorisation': 'Bearer ' + token};
}

function setSessionStorage(token, currentRole) {
    localStorage.setItem('session_token', token);
    localStorage.setItem('session_role', currentRole);
}

function clearSessionStorage() {
    localStorage.removeItem('session_token');
    localStorage.removeItem('session_role');
}

async function fetchJSON(url, options) {
    const res = await fetch(url, options);
    if (res.ok) return res;
    const err = new Error(`Server said ${res.status}`);
    err.status = res.status;
    err.response = res;
    throw err;
}

async function initAuth() {
    try {
        const existingToken = localStorage.getItem('auth_token');
        if (existingToken) return existingToken;
        const anonymousEmail = 'anonymous_' + Math.random().toString(36).substr(2, 9) + '@arbiethelp.local';
        const res = await fetch('/auth/signup', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({email: anonymousEmail, password: 'anonymous', role: ''})
        });
        if (res.ok) {
            const data = await res.json();
            localStorage.setItem('auth_token', data.token);
            return data.token;
        }
        return null;
    } catch {
        return null;
    }
}

function updateProgressUI() {
    const progressText = el('progress-text');
    const progressFill = el('progress-fill');
    const qCounter = el('q-counter');
    const qCounterFooter = el('q-count');
    const scoreEl = el('score');

    if (progressFill && progressText) {
        progressText.textContent = `Q${currentQuestionNumber}/${sessionLength}`;
        const pct = Math.min((currentQuestionNumber / sessionLength) * 100, 100);
        progressFill.style.width = pct + '%';
    }
    if (qCounter) qCounter.textContent = `Q${currentQuestionNumber}/${sessionLength}`;
    if (qCounterFooter) qCounterFooter.textContent = questionsAnswered;
    if (scoreEl) scoreEl.textContent = sessionScore;
}

function applyTimerVisibility() {
    const timerBox = el('timer-box');
    const hideBtn = el('hide-timer-btn');
    if (timerBox) timerBox.classList.toggle('timer-hidden', timerHidden);
    if (hideBtn) {
        hideBtn.textContent = timerHidden ? 'SHOW TIMER' : 'HIDE TIMER';
        hideBtn.onclick = timerHidden ? showTimer : hideTimer;
    }
}

function updateDisplay() {
    const timerEl = el('timer-display');
    const timerFillEl = el('timer-fill');
    if (!timerEl || !timerFillEl) return;

    const mins = Math.floor(timeRemaining / 60);
    const sec = timeRemaining % 60;
    timerEl.textContent = `${mins}:${sec.toString().padStart(2, '0')}`;

    const pct = Math.max((timeRemaining / TIMER_SECONDS) * 100, 0);
    timerFillEl.style.width = pct + '%';

    timerEl.classList.remove('warning', 'critical');
    timerFillEl.classList.remove('warning', 'critical');

    if (timeRemaining <= 10) {
        timerEl.classList.add('critical');
        timerFillEl.classList.add('critical');
    } else if (timeRemaining <= 30) {
        timerEl.classList.add('warning');
        timerFillEl.classList.add('warning');
    }
}

function stopTimer() {
    if (timerInterval) {
        clearInterval(timerInterval);
        timerInterval = null;
    }
}

function startTimerForQuestion() {
    stopTimer();
    timeRemaining = TIMER_SECONDS;
    updateDisplay();
    timerInterval = setInterval(() => {
        timeRemaining -= 1;
        updateDisplay();
        if (timeRemaining <= 0) {
            stopTimer();
            handleTime();
        }
    }, 1000);
}

function hideTimer() {
    timerHidden = true;
    applyTimerVisibility();
}

function showTimer() {
    timerHidden = false;
    applyTimerVisibility();
    updateDisplay();
}

function setQuestionDisplay(text) {
    const display = el('question-display');
    if (!display) return;
    display.replaceChildren();
    display.append(document.createTextNode('> ' + text + ' '));
    const cursor = document.createElement('span');
    cursor.className = 'cursor';
    cursor.textContent = '_';
    display.appendChild(cursor);
}

function setLoadingQuestion() {
    const display = el('question-display');
    if (!display) return;
    display.replaceChildren();
    display.append(document.createTextNode('LOADING....'));
    const cursor = document.createElement('span');
    cursor.className = 'cursor';
    cursor.textContent = '_';
    display.appendChild(cursor);
    setBadge('difficulty-lvl', '', '');
    setBadge('question-source', '', '');
}

function showQuestionRetry() {
    const display = el('question-display');
    if (!display) return;
    display.replaceChildren();
    display.append(document.createTextNode('Unable to load question. '));
    const retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.className = 'retry-btn';
    retryBtn.textContent = 'Retry';
    retryBtn.addEventListener('click', loadQuestion);
    display.appendChild(retryBtn);
}

function setBadge(id, text, cls) {
    const node = el(id);
    if (!node) return;
    node.textContent = text || '';
    node.className = cls || '';
}

function applySessionState(data) {
    if (typeof data.question_number === 'number') currentQuestionNumber = data.question_number;
    if (typeof data.session_length === 'number') sessionLength = data.session_length;
    if (typeof data.questions_answered === 'number') questionsAnswered = data.questions_answered;
    if (typeof data.session_score === 'number') sessionScore = data.session_score;
    if (typeof data.session_completed === 'boolean') sessionCompleted = data.session_completed;
    updateProgressUI();
}

function resetActionButtons() {
    const submitBtn = el('submit-btn');
    const skipBtn = el('skip-btn');
    const improveBtn = el('improve-btn');
    const nextBtn = el('next-btn');
    const answer = el('user-answer');

    if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = ' SUBMIT YOUR ANSWER';
    }
    if (skipBtn) skipBtn.disabled = false;
    if (improveBtn) {
        improveBtn.disabled = false;
        improveBtn.textContent = 'AI IMPROVE MY ANSWER';
        improveBtn.classList.add('hidden');
    }
    if (nextBtn) {
        nextBtn.textContent = 'NEXT QUESTION ->';
        nextBtn.onclick = nextQuestion;
    }
    if (answer) answer.disabled = false;
}

function renderQuestion(data) {
    setQuestionDisplay(data.question || '');
    setBadge('difficulty-lvl', (data.difficulty || '').toUpperCase(), data.difficulty || '');
    setBadge('question-source', data.question_source_label || '', data.question_source === 'ai' ? 'ai' : 'fallback');
    const feedbackBox = el('feedback-box');
    if (feedbackBox) feedbackBox.classList.add('hidden');
    const feedbackText = el('feedback-text');
    if (feedbackText) {
        feedbackText.classList.remove('loading');
        feedbackText.textContent = '';
    }
    const breakdownText = el('feedback-breakdown');
    if (breakdownText) breakdownText.textContent = '';
    const scoreDisplay = el('score-display');
    if (scoreDisplay) scoreDisplay.textContent = '';
    const ratingLabel = el('rating-label');
    if (ratingLabel) ratingLabel.textContent = '';
    resetActionButtons();
    applyTimerVisibility();
}

function showCorrection(explanation, changes, usesAI, fallbackReason) {
    const modal = el('correction-modal');
    const header = modal ? modal.querySelector('.modal-header h3') : null;
    const expEl = el('correction-explanation');
    const diffEl = el('correction-diff');

    if (header) {
        header.textContent = usesAI ? 'AI IMPROVED ANSWER' : 'RULE-BASED IMPROVEMENT';
    }

    if (expEl) {
        expEl.replaceChildren();
        const heading = document.createElement('p');
        heading.className = 'correction-explanation';
        const explanationText = Array.isArray(explanation) ? explanation.join('\n') : String(explanation || '');
        heading.textContent = explanationText || (usesAI ? 'AI correction applied.' : 'Rule-based correction applied.');
        expEl.appendChild(heading);
        if (!usesAI && fallbackReason) {
            const note = document.createElement('p');
            note.className = 'correction-explanation';
            note.textContent = `Fallback reason: ${fallbackReason}`;
            expEl.appendChild(note);
        }
    }

    if (diffEl) {
        diffEl.replaceChildren();
        const container = document.createElement('div');
        container.className = 'diff-container';
        (changes || []).forEach(change => {
            const line = document.createElement('div');
            line.className = 'diff-line ' + (change.type === 'add' ? 'diff-add' : (change.type === 'remove' ? 'diff-remove' : 'diff-replace'));
            if (change.original) {
                const original = document.createElement('span');
                original.className = 'diff-original';
                original.textContent = change.original;
                line.appendChild(original);
            }
            if (change.improved) {
                const improved = document.createElement('span');
                improved.className = 'diff-improved';
                improved.textContent = change.improved;
                line.appendChild(improved);
            }
            const reason = document.createElement('span');
            reason.className = 'diff-reason';
            reason.textContent = change.reason || '';
            line.appendChild(reason);
            container.appendChild(line);
        });
        diffEl.appendChild(container);
    }

    if (modal) modal.classList.remove('hidden');
}

function closeCorrection() {
    const modal = el('correction-modal');
    if (modal) modal.classList.add('hidden');
    currentImproved = '';
    currentChanges = [];
    currentCorrectionUsesAI = false;
    const btn = el('submit-btn');
    if (btn) {
        btn.disabled = false;
        btn.textContent = ' SUBMIT YOUR ANSWER';
    }
}

function applyCorrection() {
    const answer = el('user-answer');
    if (answer) {
        answer.value = currentImproved;
        answer.dispatchEvent(new Event('input'));
    }
    closeCorrection();
}

function showCompletionState(summary) {
    sessionCompleted = true;
    stopTimer();
    clearSessionStorage();
    sessionToken = null;

    const completedScore = typeof summary?.session_score === 'number' ? summary.session_score : sessionScore;
    const completedAnswered = typeof summary?.questions_answered === 'number' ? summary.questions_answered : questionsAnswered;
    const completedLength = typeof summary?.session_length === 'number' ? summary.session_length : sessionLength;
    sessionScore = completedScore;
    questionsAnswered = completedAnswered;
    sessionLength = completedLength;
    currentQuestionNumber = completedLength;
    updateProgressUI();

    setQuestionDisplay('INTERVIEW COMPLETE');
    setBadge('difficulty-lvl', 'COMPLETE', 'complete');
    setBadge('question-source', 'SESSION COMPLETE', 'complete');

    const answer = el('user-answer');
    if (answer) answer.disabled = true;
    const submitBtn = el('submit-btn');
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'SESSION COMPLETE';
    }
    const skipBtn = el('skip-btn');
    if (skipBtn) skipBtn.disabled = true;
    const improveBtn = el('improve-btn');
    if (improveBtn) improveBtn.classList.add('hidden');

    const feedbackBox = el('feedback-box');
    const feedbackText = el('feedback-text');
    const breakdownText = el('feedback-breakdown');
    const scoreDisplay = el('score-display');
    const ratingLabel = el('rating-label');
    const nextBtn = el('next-btn');

    if (feedbackBox) feedbackBox.classList.remove('hidden');
    if (feedbackText) {
        feedbackText.classList.remove('loading');
        feedbackText.textContent = `Session complete. Total score: ${completedScore}/${completedLength * 3}.`;
    }
    if (breakdownText) {
        breakdownText.textContent = `Answered ${completedAnswered}/${completedLength} questions.`;
    }
    if (scoreDisplay) {
        scoreDisplay.textContent = `${completedScore}/${completedLength * 3} SESSION PTS`;
    }
    if (ratingLabel) ratingLabel.textContent = 'SESSION COMPLETE';
    if (nextBtn) {
        nextBtn.textContent = 'VIEW STATS ->';
        nextBtn.onclick = () => {
            location.href = 'stats.html';
        };
        nextBtn.style.display = 'block';
    }
}

async function ensureSession() {
    if (sessionToken) return sessionToken;
    const storedToken = localStorage.getItem('session_token');
    const storedRole = localStorage.getItem('session_role');
    if (storedToken && storedRole === role) {
        sessionToken = storedToken;
        return sessionToken;
    }
    clearSessionStorage();
    const res = await fetchJSON('/session/start', {
        method: 'POST',
        headers: authHeaders(authToken),
        body: JSON.stringify({role})
    });
    const data = await res.json();
    sessionToken = data.session_token;
    setSessionStorage(sessionToken, role);
    applySessionState(data);
    return sessionToken;
}

async function loadQuestion() {
    if (!role || sessionCompleted) return;
    setLoadingQuestion();

    try {
        await ensureSession();
        const res = await fetchJSON('/session/question', {
            headers: {'Authorisation': 'Bearer ' + sessionToken}
        });
        const data = await res.json();
        legacyRetries = 0;
        sessionCompleted = false;
        applySessionState(data);
        renderQuestion(data);
        startTimerForQuestion();
    } catch (err) {
        if (err.status === 409) {
            const data = await err.response.json().catch(() => ({}));
            showCompletionState(data.summary || {});
            return;
        }
        if (err.status === 401 || err.status === 403) {
            sessionToken = null;
            clearSessionStorage();
        }
        legacyRetries += 1;
        if (legacyRetries >= 3) {
            showQuestionRetry();
            return;
        }
        setTimeout(loadQuestion, 500);
    }
}

async function submitAnswer() {
    if (isSubmitting) return;
    if (!role) {
        alert('Role not specified. Please reload the page with a valid role.');
        return;
    }

    const answerEl = el('user-answer');
    const answer = (answerEl?.value || '').trim();
    if (!answer) {
        alert('write something first');
        return;
    }

    isSubmitting = true;
    stopTimer();
    submitAbortControl = new AbortController();

    const btn = el('submit-btn');
    const feedbackBox = el('feedback-box');
    const feedbackText = el('feedback-text');
    const breakdownText = el('feedback-breakdown');
    const scoreDisplay = el('score-display');
    const ratingLabel = el('rating-label');
    const nextBtn = el('next-btn');
    const improveBtn = el('improve-btn');

    if (btn) {
        btn.disabled = true;
        btn.textContent = '...gradin ts...';
    }
    if (feedbackBox) feedbackBox.classList.remove('hidden');
    if (feedbackText) {
        feedbackText.classList.add('loading');
        feedbackText.textContent = 'gradin ts...';
    }
    if (breakdownText) breakdownText.textContent = '';
    if (scoreDisplay) scoreDisplay.textContent = '';
    if (ratingLabel) ratingLabel.textContent = '';
    if (nextBtn) nextBtn.style.display = 'none';
    if (improveBtn) improveBtn.classList.add('hidden');

    try {
        await ensureSession();
        const res = await fetchJSON('/session/submit', {
            method: 'POST',
            headers: authHeaders(sessionToken),
            body: JSON.stringify({answer}),
            signal: submitAbortControl.signal
        });
        const data = await res.json();
        if (data.session_completed) {
            showCompletionState({
                session_score: data.session_score,
                questions_answered: data.questions_answered,
                session_length: data.session_length
            });
            isSubmitting = false;
            submitAbortControl = null;
            return;
        }

        if (feedbackText) {
            feedbackText.classList.remove('loading');
            feedbackText.textContent = data.feedback || '';
        }
        if (breakdownText) breakdownText.textContent = data.breakdown || '';
        if (scoreDisplay) scoreDisplay.textContent = `${data.points}/${data.max_points} PTS`;
        if (ratingLabel) ratingLabel.textContent = data.ai_used ? 'AI GRADE' : 'RULE GRADE';

        currentQuestionNumber = data.question_number || currentQuestionNumber;
        questionsAnswered = typeof data.questions_answered === 'number' ? data.questions_answered : questionsAnswered + 1;
        sessionScore = typeof data.session_score === 'number' ? data.session_score : sessionScore + (data.points || 0);
        sessionLength = data.session_length || sessionLength;
        updateProgressUI();

        if (nextBtn) nextBtn.style.display = 'block';
        if (improveBtn) improveBtn.classList.remove('hidden');
    } catch (err) {
        if (err.name === 'AbortError') {
            isSubmitting = false;
            submitAbortControl = null;
            return;
        }
        if (err.status === 409) {
            const data = await err.response.json().catch(() => ({}));
            showCompletionState(data.summary || {});
            isSubmitting = false;
            submitAbortControl = null;
            return;
        }
        if (err.status === 401 || err.status === 403) {
            sessionToken = null;
            clearSessionStorage();
        }
        if (feedbackText) {
            feedbackText.classList.remove('loading');
            feedbackText.textContent = 'Something went wrong. Your answer was not graded.';
        }
        if (btn) {
            btn.disabled = false;
            btn.textContent = ' SUBMIT YOUR ANSWER';
        }
    } finally {
        isSubmitting = false;
        submitAbortControl = null;
    }
}

async function nextQuestion() {
    if (sessionCompleted) {
        location.href = 'stats.html';
        return;
    }
    if (submitAbortControl) {
        submitAbortControl.abort();
        submitAbortControl = null;
    }
    isSubmitting = false;

    const answer = el('user-answer');
    if (answer) {
        answer.value = '';
        answer.disabled = false;
    }
    const wordCount = el('word-count');
    if (wordCount) wordCount.textContent = '0';
    const wordCountLabel = el('word-count-label');
    if (wordCountLabel) wordCountLabel.className = '';

    const feedbackBox = el('feedback-box');
    if (feedbackBox) feedbackBox.classList.add('hidden');
    await loadQuestion();
}

async function skipQuestion() {
    if (sessionCompleted) return;
    stopTimer();
    if (submitAbortControl) {
        submitAbortControl.abort();
        submitAbortControl = null;
    }
    isSubmitting = false;

    const answer = el('user-answer');
    if (answer) {
        answer.value = '';
        answer.disabled = false;
    }
    const wordCount = el('word-count');
    if (wordCount) wordCount.textContent = '0';
    const wordCountLabel = el('word-count-label');
    if (wordCountLabel) wordCountLabel.className = '';

    const feedbackBox = el('feedback-box');
    if (feedbackBox) feedbackBox.classList.add('hidden');

    try {
        await ensureSession();
        const res = await fetchJSON('/session/skip', {
            method: 'POST',
            headers: {'Authorisation': 'Bearer ' + sessionToken}
        });
        const data = await res.json();
        currentQuestionNumber = data.question_number || currentQuestionNumber;
        questionsAnswered = typeof data.questions_answered === 'number' ? data.questions_answered : questionsAnswered + 1;
        sessionScore = typeof data.session_score === 'number' ? data.session_score : sessionScore;
        sessionLength = data.session_length || sessionLength;
        updateProgressUI();
        if (data.session_completed) {
            showCompletionState({
                session_score: data.session_score,
                questions_answered: data.questions_answered,
                session_length: data.session_length
            });
            return;
        }
    } catch (err) {
        console.warn('skip sync failed', err);
    }

    await loadQuestion();
}

async function handleTime() {
    if (isSubmitting) return;
    const answer = (el('user-answer')?.value || '').trim();
    if (answer) {
        await submitAnswer();
    } else {
        await skipQuestion();
    }
}

async function improveAnswer() {
    if (!role) {
        alert('Role not specified. Please reload the page.');
        return;
    }

    const answerEl = el('user-answer');
    const answer = (answerEl?.value || '').trim();
    if (!answer) {
        alert('write something first');
        return;
    }

    const btn = el('improve-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = '...improving...';
    }

    try {
        await ensureSession();
        const res = await fetchJSON('/correct', {
            method: 'POST',
            headers: authHeaders(sessionToken),
            body: JSON.stringify({answer})
        });
        const data = await res.json();
        currentImproved = data.improved || '';
        currentChanges = Array.isArray(data.changes) ? data.changes : [];
        currentCorrectionUsesAI = !!data.ai_used;
        showCorrection(data.explanation, currentChanges, currentCorrectionUsesAI, data.fallback_reason);
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'AI IMPROVE MY ANSWER';
        }
    } catch (err) {
        if (err.status === 401 || err.status === 403) {
            sessionToken = null;
            clearSessionStorage();
        }
        alert('AI correction unavailable right now.');
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'AI IMPROVE MY ANSWER';
        }
    }
}

async function init() {
    authToken = await initAuth();
    const params = new URLSearchParams(window.location.search);
    role = params.get('role');

    if (!role) {
        alert('Role not given. Please provide a role in the URL query parameters.');
        return;
    }

    const storedToken = localStorage.getItem('session_token');
    const storedRole = localStorage.getItem('session_role');
    if (storedToken && storedRole === role) {
        sessionToken = storedToken;
    } else if (storedToken && storedRole !== role) {
        clearSessionStorage();
        sessionToken = null;
    }

    const answerEl = el('user-answer');
    if (answerEl) {
        answerEl.addEventListener('keydown', (e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                if (!isSubmitting) submitAnswer();
            }
        });
        answerEl.addEventListener('input', () => {
            const text = answerEl.value.trim();
            const count = text === '' ? 0 : text.split(/\s+/).length;
            const countEl = el('word-count');
            if (countEl) countEl.textContent = count;
            const label = el('word-count-label');
            if (label) label.className = 'word-count ' + (count >= 50 ? 'good' : '');
        });
    }

    const roleTitle = el('role-title');
    if (roleTitle) roleTitle.textContent = role.toUpperCase();
    const roleSubtitle = el('role-subtitle');
    if (roleSubtitle) roleSubtitle.textContent = '// ' + role + ' Interview //';

    applyTimerVisibility();
    updateProgressUI();
    await loadQuestion();
}

window.addEventListener('beforeunload', () => {
    stopTimer();
    if (submitAbortControl) submitAbortControl.abort();
});

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
