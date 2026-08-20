function authHeaders(token) {
    return {'Content-Type': 'application/json', 'Authorisation': 'Bearer ' + token};
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
        const anonymousEmail = 'anon_' + Math.random().toString(36).substr(2, 9) + '@arbiethelp.local';
        const res = await fetch('/auth/signup', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({email: anonymousEmail, password: 'anon', role: ''})
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