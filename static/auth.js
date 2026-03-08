// ── Token storage ─────────────────────────────────────────────────────────────
function getToken() { return localStorage.getItem('ls_token'); }
function setToken(t) { localStorage.setItem('ls_token', t); }
function clearToken() { localStorage.removeItem('ls_token'); localStorage.removeItem('ls_user'); }

// ── User cache ────────────────────────────────────────────────────────────────
async function getUser(force = false) {
  if (!force) {
    const cached = localStorage.getItem('ls_user');
    if (cached) return JSON.parse(cached);
  }
  const token = getToken();
  if (!token) return null;
  try {
    const res = await fetch('/api/auth/me', {
      headers: { 'Authorization': `Bearer ${token}` }
    });
    if (!res.ok) { clearToken(); return null; }
    const data = await res.json();
    localStorage.setItem('ls_user', JSON.stringify(data));
    return data;
  } catch {
    return null;
  }
}

function logout() {
  clearToken();
  window.location.href = '/login';
}

// ── Usage display ─────────────────────────────────────────────────────────────
function renderUsage(usage) {
  const el = document.getElementById('usage-display');
  if (!el || !usage) return;
  const { type, used, limit, tier } = usage;
  if (tier === 'unlimited') {
    el.textContent = '[ UNLIMITED ]';
    return;
  }
  const label = type === 'scans' ? 'SCANS' : 'LEADS';
  const limitStr = limit != null ? limit : '∞';
  el.textContent = `[ ${label}: ${used}/${limitStr} ]`;
}

// ── On page load: handle Google OAuth token in URL, update nav ────────────────
(async function init() {
  // If Google OAuth redirected here with ?token=...
  const params = new URLSearchParams(window.location.search);
  const urlToken = params.get('token');
  if (urlToken) {
    setToken(urlToken);
    params.delete('token');
    const newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.replaceState({}, '', newUrl);
  }

  const user = await getUser();
  updateAuthNav(user);
  if (user) renderUsage(user.usage);
})();

function updateAuthNav(user) {
  const authLink = document.getElementById('nav-auth-link');
  const usageEl  = document.getElementById('usage-display');
  if (!authLink) return;

  if (user) {
    authLink.textContent = '[ ACCOUNT ]';
    authLink.href = '/dashboard';
    if (usageEl) usageEl.style.display = 'inline';
  } else {
    authLink.textContent = '[ LOGIN ]';
    authLink.href = '/login';
    if (usageEl) usageEl.style.display = 'none';
  }
}
