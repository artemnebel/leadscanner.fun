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

// ── Plan badge (daily scans left for free tier) ───────────────────────────────
function renderPlanBadge(plan) {
  const el = document.getElementById('usage-display');
  if (!el) return;
  // Pro / uncapped: no badge.
  if (!plan || plan.pro || plan.daily_scan_limit == null) {
    el.textContent = '';
    el.style.display = 'none';
    return;
  }
  const left = plan.daily_remaining != null ? plan.daily_remaining : plan.daily_scan_limit;
  el.textContent = `[ SCANS: ${left}/${plan.daily_scan_limit} ]`;
  el.style.display = 'inline';
}

// ── Usage display (legacy leads badge) ────────────────────────────────────────
function renderUsage(usage) {
  const el = document.getElementById('usage-display');
  if (!el || !usage) return;
  const { tier, free_used, free_limit, credits, available, used, limit } = usage;
  // Uncapped (admin / legacy unlimited): show no usage badge at all.
  if (available === null || tier === 'unlimited') {
    el.textContent = '';
    el.style.display = 'none';
    return;
  }
  // New credit-aware shape: show total leads available across free allotment + credits
  if (typeof available === 'number') {
    el.textContent = `[ LEADS: ${available} ]`;
    return;
  }
  // Legacy fallback (in case server returns old shape)
  const limitStr = limit != null ? limit : '∞';
  el.textContent = `[ LEADS: ${used ?? 0}/${limitStr} ]`;
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

  if (params.get('auth_error')) {
    alert('Google sign-in failed. Please try again.');
    history.replaceState({}, '', window.location.pathname);
  }

  const user = await getUser();
  updateAuthNav(user);
  if (user) renderPlanBadge(user.plan);
})();

function updateAuthNav(user) {
  const authLink = document.getElementById('nav-auth-link');
  const usageEl  = document.getElementById('usage-display');
  const clientsLink = document.getElementById('nav-clients-link');
  if (!authLink) return;

  if (user) {
    authLink.textContent = '[ ACCOUNT ]';
    authLink.href = '/dashboard';
    if (usageEl) usageEl.style.display = 'inline';
    if (clientsLink) clientsLink.style.display = 'inline';
  } else {
    authLink.textContent = '[ LOGIN ]';
    authLink.href = '/login';
    if (usageEl) usageEl.style.display = 'none';
    if (clientsLink) clientsLink.style.display = 'none';
  }
}
