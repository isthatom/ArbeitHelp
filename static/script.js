// defining the functions for loading roles
async function loadRoles() {
    const res = await fetch('/roles');
    const roles = await res.json()
    // classifying them to be able to be recognizable by HTML file
    const grid = document.getElementById('role-grid');
    if (!grid) return;

    grid.replaceChildren();
    roles.forEach(r => {
        const btn = document.createElement('button');
        btn.className = 'role-btn';
        btn.type = 'button';
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

// jump into the interview for the selected role
function selectRole(role) {
    window.location.href = `questions.html?role=${encodeURIComponent(role)}`;
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

// --boot---
async function init() {
    await initAuth();
    loadRoles();
    runTypewriting();
    initReveal();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}
