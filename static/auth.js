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

// ── Mobile nav toggle (hamburger) — injected on every page that has #main-nav ──
function initMobileNav() {
  const nav = document.getElementById('main-nav');
  if (!nav || nav.querySelector('.nav-toggle')) return;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'nav-toggle';
  btn.setAttribute('aria-label', 'Toggle navigation menu');
  btn.setAttribute('aria-expanded', 'false');
  btn.textContent = '[ MENU ]';

  function close() {
    nav.classList.remove('nav-open');
    btn.setAttribute('aria-expanded', 'false');
    btn.textContent = '[ MENU ]';
  }

  btn.addEventListener('click', () => {
    const open = nav.classList.toggle('nav-open');
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    btn.textContent = open ? '[ CLOSE ]' : '[ MENU ]';
  });
  nav.appendChild(btn);

  // Tapping a link navigates away; collapse the menu for same-page/hash cases too.
  nav.querySelectorAll('.nav-link').forEach(link => link.addEventListener('click', close));
}
initMobileNav();

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
})();

function updateAuthNav(user) {
  const authLink = document.getElementById('nav-auth-link');
  const clientsLink = document.getElementById('nav-clients-link');
  if (!authLink) return;

  if (user) {
    authLink.textContent = '[ ACCOUNT ]';
    authLink.href = '/dashboard';
    if (clientsLink) clientsLink.style.display = 'inline';
  } else {
    authLink.textContent = '[ LOGIN ]';
    authLink.href = '/login';
    if (clientsLink) clientsLink.style.display = 'none';
  }
}
