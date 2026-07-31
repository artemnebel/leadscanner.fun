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

// ── OAuth callback error/success codes → friendly text ─────────────────────────
function _oauthProviderName(code) {
  const provider = (code || '').split('_')[0];
  return { google: 'Google', facebook: 'Facebook', github: 'GitHub' }[provider] || 'That provider';
}
function _authErrorMessage(code) {
  const name = _oauthProviderName(code);
  const reason = (code || '').slice(code.indexOf('_') + 1);
  const reasons = {
    failed: `${name} sign-in failed. Please try again.`,
    no_email: `${name} didn't share an email address, so we can't use it to sign in.`,
    email_mismatch: `That ${name} account's email doesn't match your Lead Scanner account. Connect using the ${name} account for your own email instead.`,
    already_linked: `That ${name} account is already connected to a different Lead Scanner account.`,
    link_failed: `Couldn't connect ${name} — please try again.`,
  };
  return reasons[reason] || `${name} sign-in failed. Please try again.`;
}

// ── On page load: handle OAuth redirects (login token, link success/failure) ──
(async function init() {
  const params = new URLSearchParams(window.location.search);
  let dirty = false;

  const urlToken = params.get('token');
  if (urlToken) {
    setToken(urlToken);
    params.delete('token');
    dirty = true;
  }

  const linked = params.get('linked');
  if (linked) {
    localStorage.removeItem('ls_user');  // force fresh Connected-accounts status on next load
    const msg = `${linked.charAt(0).toUpperCase()}${linked.slice(1)} connected.`;
    if (typeof showToast === 'function') showToast(msg, 'success'); else alert(msg);
    params.delete('linked');
    dirty = true;
  }

  const authErr = params.get('auth_error');
  if (authErr) {
    const msg = _authErrorMessage(authErr);
    if (typeof showToast === 'function') showToast(msg, 'error'); else alert(msg);
    params.delete('auth_error');
    dirty = true;
  }

  if (dirty) {
    const newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.replaceState({}, '', newUrl);
  }

  const user = await getUser();
  updateAuthNav(user);
})();

function updateAuthNav(user) {
  const authLink = document.getElementById('nav-auth-link');
  const clientsLink = document.getElementById('nav-clients-link');
  if (!authLink) return;

  // The app view uses plain labels; the terminal pages keep their brackets.
  const plain = authLink.dataset.plain === '1';

  if (user) {
    if (plain) {
      // Signed in on the app view: an initial in a circle rather than a word,
      // and a click opens the account dropdown instead of navigating away.
      const initial = (user.email || '?').trim().charAt(0).toUpperCase() || '?';
      authLink.textContent = initial;
      authLink.classList.add('an-avatar');
      authLink.title = user.email || 'Account';
      authLink.setAttribute('aria-label', 'Account: ' + (user.email || ''));
      authLink.href = '#';
      initAccountDropdown(user, initial);
    } else {
      authLink.textContent = '[ ACCOUNT ]';
      authLink.href = '/dashboard';
    }
    if (clientsLink) clientsLink.style.display = 'inline';
  } else {
    authLink.textContent = plain ? 'Log in' : '[ LOGIN ]';
    authLink.classList.remove('an-avatar');
    authLink.removeAttribute('title');
    authLink.removeAttribute('aria-label');
    authLink.href = '/login';
    if (clientsLink) clientsLink.style.display = 'none';
    const dropdown = document.getElementById('an-dropdown');
    if (dropdown) dropdown.classList.add('hidden');
  }
}

// ── Account dropdown (app view only) ────────────────────────────────────────
function initAccountDropdown(user, initial) {
  const authLink = document.getElementById('nav-auth-link');
  const dropdown = document.getElementById('an-dropdown');
  if (!authLink || !dropdown) return;

  const ddAvatar = document.getElementById('an-dd-avatar');
  const ddEmail = document.getElementById('an-dd-email');
  if (ddAvatar) ddAvatar.textContent = initial;
  if (ddEmail) ddEmail.textContent = user.email || '';

  const wrap = document.getElementById('an-user-wrap');
  if (wrap.dataset.bound) return;   // bind listeners once
  wrap.dataset.bound = '1';

  const panelMain = document.getElementById('an-dd-panel-main');
  const panelLang = document.getElementById('an-dd-panel-lang');
  const langCurrent = document.getElementById('an-dd-lang-current');
  const logoutBtn = document.getElementById('an-dd-logout');

  function closeDropdown() {
    dropdown.classList.add('hidden');
    dropdown.setAttribute('aria-hidden', 'true');
    authLink.setAttribute('aria-expanded', 'false');
  }
  function openDropdown() {
    dropdown.classList.remove('hidden');
    dropdown.setAttribute('aria-hidden', 'false');
    authLink.setAttribute('aria-expanded', 'true');
  }

  authLink.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (dropdown.classList.contains('hidden')) openDropdown();
    else closeDropdown();
  });

  document.addEventListener('click', (e) => {
    if (!dropdown.classList.contains('hidden') && !wrap.contains(e.target)) closeDropdown();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const modal = document.getElementById('settings-modal');
    if (modal && !modal.classList.contains('hidden')) modal.classList.add('hidden');
    else closeDropdown();
  });

  document.getElementById('an-dd-settings-open')?.addEventListener('click', () => {
    closeDropdown();
    openSettingsModal(user);
  });

  // Language: swap to the language list panel and back. Selecting a language
  // here only updates this menu's UI/preference — the site has no translated
  // copy yet, so nothing on the page actually changes language.
  document.getElementById('an-dd-lang-open')?.addEventListener('click', () => {
    panelMain.classList.add('hidden');
    panelLang.classList.remove('hidden');
  });
  document.getElementById('an-dd-lang-back')?.addEventListener('click', () => {
    panelLang.classList.add('hidden');
    panelMain.classList.remove('hidden');
  });

  const savedLang = localStorage.getItem('ls_lang');
  document.querySelectorAll('.an-lang-opt').forEach(opt => {
    if (savedLang && opt.dataset.lang === savedLang) {
      document.querySelectorAll('.an-lang-opt').forEach(o => o.classList.remove('is-selected'));
      opt.classList.add('is-selected');
      if (langCurrent) langCurrent.textContent = opt.dataset.label;
    }
    opt.addEventListener('click', () => {
      document.querySelectorAll('.an-lang-opt').forEach(o => o.classList.remove('is-selected'));
      opt.classList.add('is-selected');
      localStorage.setItem('ls_lang', opt.dataset.lang);
      if (langCurrent) langCurrent.textContent = opt.dataset.label;
      panelLang.classList.add('hidden');
      panelMain.classList.remove('hidden');
      closeDropdown();
    });
  });

  logoutBtn?.addEventListener('click', () => { closeDropdown(); logout(); });
}

// ── Settings modal (app view only) ──────────────────────────────────────────
// Centered dialog over the app rather than a separate page. Only Password
// lives here for now; more settings can join it later without a new route.
function renderSettingsPwSection(modal) {
  const hasPassword = modal.dataset.hasPassword === '1';
  const isGoogle = modal.dataset.isGoogle === '1';
  const blurb = document.getElementById('settings-pw-blurb');
  const group = document.getElementById('settings-current-pw-group');
  const btn = document.getElementById('settings-pw-btn');
  if (hasPassword) {
    blurb.textContent = 'Change your Lead Scanner password.';
    group.classList.remove('hidden');
    btn.textContent = 'Change password';
  } else {
    blurb.textContent = isGoogle
      ? 'You signed up with Google, so you have no password yet. Set one to also log in with your email and password.'
      : 'Set a password to log in with your email and password.';
    group.classList.add('hidden');
    btn.textContent = 'Set password';
  }
}

function closeSettingsModal() {
  document.getElementById('settings-modal')?.classList.add('hidden');
}

// ── Connected accounts ───────────────────────────────────────────────────────
function renderConnectedAccounts(user) {
  document.querySelectorAll('.settings-oauth-row').forEach(row => {
    const provider = row.dataset.provider;
    const connected = !!user[provider];
    const statusEl = row.querySelector('[data-role="status"]');
    const actionBtn = row.querySelector('[data-role="action"]');
    statusEl.textContent = connected ? 'Connected' : 'Not connected';
    statusEl.classList.toggle('is-connected', connected);
    actionBtn.textContent = connected ? 'Disconnect' : 'Connect';
    actionBtn.classList.toggle('settings-btn-danger', connected);
  });
}

async function _handleOAuthAction(provider, connected) {
  if (connected) {
    if (!confirm(`Disconnect ${provider.charAt(0).toUpperCase() + provider.slice(1)} from your account?`)) return;
    try {
      const res = await fetch('/api/auth/oauth/unlink', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getToken() },
        body: JSON.stringify({ provider }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not disconnect.');
      localStorage.removeItem('ls_user');
      const user = await getUser(true);
      renderConnectedAccounts(user);
      if (typeof showToast === 'function') showToast(`${provider} disconnected`, 'success');
    } catch (err) {
      if (typeof showToast === 'function') showToast(err.message, 'error'); else alert(err.message);
    }
  } else {
    // Full-page redirect into the provider's OAuth consent screen; the JWT
    // rides along as link_token (state) so the callback knows which account
    // to attach the provider to instead of treating this as a fresh login.
    window.location.href = `/api/auth/${provider}?link_token=${encodeURIComponent(getToken())}`;
  }
}

// ── Billing ──────────────────────────────────────────────────────────────────
function renderSettingsBilling(user) {
  const plan = user.plan || {};
  const usage = user.usage || {};
  const remaining = usage.credits || 0;
  const total = usage.credits_total || 0;
  const used = Math.max(0, total - remaining);
  const pct = total > 0 ? Math.min(100, Math.round((used / total) * 100)) : 0;
  document.getElementById('settings-used-label').textContent = `Used ${used} / ${total}`;
  document.getElementById('settings-fill').style.width = pct + '%';
  document.getElementById('settings-pct').textContent = `${pct}% used`;

  const buyBtn = document.getElementById('settings-buy-btn');
  buyBtn.style.display = plan.leads_available === null ? 'none' : 'inline-flex';
}

const _fmtDate = (unixSeconds) =>
  new Date(unixSeconds * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
const _fmtMoney = (cents) => `$${(cents / 100).toFixed(2)}`;

function renderSettingsSubscription(data) {
  const block = document.getElementById('settings-subscription-block');
  if (!data || !data.has_subscription) {
    block.classList.add('hidden');
    return;
  }
  block.classList.remove('hidden');
  block.dataset.cancelAtPeriodEnd = data.cancel_at_period_end ? '1' : '0';

  document.getElementById('settings-plan-name').textContent = data.plan_label || 'Subscription';
  document.getElementById('settings-plan-interval').textContent = data.interval ? `Billed ${data.interval}ly` : '';
  document.getElementById('settings-plan-renew').textContent = data.current_period_end
    ? (data.cancel_at_period_end
        ? `Ends on ${_fmtDate(data.current_period_end)}`
        : `Renews on ${_fmtDate(data.current_period_end)}`)
    : '';

  const paySummary = document.getElementById('settings-payment-summary');
  paySummary.textContent = data.card
    ? `${data.card.brand.charAt(0).toUpperCase()}${data.card.brand.slice(1)} •••• ${data.card.last4}`
    : 'No card on file';

  const tbody = document.getElementById('settings-invoices-tbody');
  const empty = document.getElementById('settings-invoices-empty');
  tbody.innerHTML = '';
  const invoices = data.invoices || [];
  empty.style.display = invoices.length ? 'none' : 'block';
  document.getElementById('settings-invoices-table').style.display = invoices.length ? 'table' : 'none';
  invoices.forEach(inv => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${_fmtDate(inv.date)}</td>
      <td>${_fmtMoney(inv.total)}</td>
      <td><span class="settings-invoice-status${inv.status === 'paid' ? ' is-paid' : ''}">${inv.status}</span></td>
      <td>${inv.url ? `<a href="${inv.url}" target="_blank" rel="noopener">View</a>` : '—'}</td>
    `;
    tbody.appendChild(tr);
  });

  const cancelLabel = document.getElementById('settings-cancel-label');
  const cancelBlurb = document.getElementById('settings-cancel-blurb');
  const cancelBtn = document.getElementById('settings-cancel-btn');
  if (data.cancel_at_period_end) {
    cancelLabel.textContent = 'Plan is ending';
    cancelBlurb.textContent = `Your plan won't renew and will end on ${_fmtDate(data.current_period_end)}.`;
    cancelBtn.textContent = 'Resume plan';
    cancelBtn.classList.remove('settings-btn-danger');
  } else {
    cancelLabel.textContent = 'Cancel plan';
    cancelBlurb.textContent = "You'll keep access until the end of the current billing period.";
    cancelBtn.textContent = 'Cancel';
    cancelBtn.classList.add('settings-btn-danger');
  }
}

async function loadSettingsSubscription() {
  try {
    const res = await fetch('/api/billing/subscription', { headers: { 'Authorization': `Bearer ${getToken()}` } });
    const data = await res.json();
    renderSettingsSubscription(res.ok ? data : null);
  } catch {
    renderSettingsSubscription(null);
  }
}

async function _openBillingPortalFlow(flow, btn) {
  const label = btn.textContent;
  btn.textContent = 'Loading…';
  btn.disabled = true;
  try {
    const res = await fetch(`/api/billing/portal?flow=${encodeURIComponent(flow)}`, {
      headers: { 'Authorization': `Bearer ${getToken()}` },
    });
    const data = await res.json();
    if (res.ok) window.location.href = data.url;
    else { btn.textContent = label; btn.disabled = false; }
  } catch {
    btn.textContent = label;
    btn.disabled = false;
  }
}

async function _toggleCancelSubscription() {
  const block = document.getElementById('settings-subscription-block');
  const btn = document.getElementById('settings-cancel-btn');
  const cancel = block.dataset.cancelAtPeriodEnd !== '1';  // toggle
  if (cancel && !confirm("Cancel your plan? You'll keep access until the current period ends.")) return;

  const label = btn.textContent;
  btn.textContent = cancel ? 'Cancelling…' : 'Resuming…';
  btn.disabled = true;
  try {
    const res = await fetch('/api/billing/cancel', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getToken() },
      body: JSON.stringify({ cancel_at_period_end: cancel }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Could not update subscription.');
    await loadSettingsSubscription();
    if (typeof showToast === 'function') showToast(cancel ? 'Plan set to cancel' : 'Plan resumed', 'success');
  } catch (err) {
    btn.textContent = label;
    btn.disabled = false;
    if (typeof showToast === 'function') showToast(err.message, 'error'); else alert(err.message);
  }
}

// ── Notifications ────────────────────────────────────────────────────────────
async function _saveMarketingPreference(optOut) {
  try {
    const res = await fetch('/api/account/preferences', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getToken() },
      body: JSON.stringify({ marketing_opt_out: optOut }),
    });
    if (!res.ok) throw new Error('Could not save preference.');
    localStorage.removeItem('ls_user');
    if (typeof showToast === 'function') showToast('Preference saved', 'success');
  } catch (err) {
    if (typeof showToast === 'function') showToast(err.message, 'error'); else alert(err.message);
  }
}

// ── Privacy / delete account ─────────────────────────────────────────────────
function _resetDeleteConfirm() {
  document.getElementById('settings-delete-confirm').classList.add('hidden');
  document.getElementById('settings-delete-confirm-input').value = '';
  document.getElementById('settings-delete-confirm-btn').disabled = true;
  document.getElementById('settings-delete-msg').classList.add('hidden');
}

async function _deleteOwnAccount() {
  const btn = document.getElementById('settings-delete-confirm-btn');
  const msg = document.getElementById('settings-delete-msg');
  btn.disabled = true;
  btn.textContent = 'Deleting…';
  msg.classList.add('hidden');
  try {
    const res = await fetch('/api/account/delete', {
      method: 'POST',
      headers: { 'Authorization': 'Bearer ' + getToken() },
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === 'object' ? detail.message : (detail || 'Could not delete account.'));
    }
    clearToken();
    window.location.href = '/';
  } catch (err) {
    msg.style.color = '#ff6b6b';
    msg.textContent = err.message;
    msg.classList.remove('hidden');
    btn.disabled = false;
    btn.textContent = 'Permanently delete';
  }
}

function openSettingsModal(user) {
  const modal = document.getElementById('settings-modal');
  if (!modal) return;

  if (!modal.dataset.bound) {
    modal.dataset.bound = '1';
    document.getElementById('settings-close-btn').addEventListener('click', closeSettingsModal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeSettingsModal(); });

    // Sidebar nav: swap the active .settings-panel, same .hidden-toggle idiom
    // as the account dropdown's Language sub-panel.
    document.querySelectorAll('.settings-nav-item').forEach(navBtn => {
      navBtn.addEventListener('click', () => {
        document.querySelectorAll('.settings-nav-item').forEach(b => b.classList.remove('is-active'));
        navBtn.classList.add('is-active');
        const target = navBtn.dataset.panel;
        document.querySelectorAll('.settings-panel').forEach(panel => {
          panel.classList.toggle('hidden', panel.dataset.panel !== target);
        });
        // Real Stripe data — fetch once per modal-open, not on every nav click.
        if (target === 'billing' && !modal.dataset.billingLoaded) {
          modal.dataset.billingLoaded = '1';
          loadSettingsSubscription();
        }
      });
    });

    document.getElementById('settings-pw-form').addEventListener('submit', async (e) => {
      e.preventDefault();
      const msg = document.getElementById('settings-pw-msg');
      const newPw = document.getElementById('settings-new-pw').value;
      const curPw = document.getElementById('settings-current-pw').value;
      const pwBtn = document.getElementById('settings-pw-btn');
      msg.classList.add('hidden');
      if (newPw.length < 8) {
        msg.style.color = '#ff6b6b';
        msg.textContent = 'Password must be at least 8 characters.';
        msg.classList.remove('hidden');
        return;
      }
      pwBtn.disabled = true;
      pwBtn.textContent = 'Saving...';
      try {
        const payload = { password: newPw };
        if (modal.dataset.hasPassword === '1') payload.current_password = curPw;
        const res = await fetch('/api/auth/set-password', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getToken() },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Could not save password.');
        modal.dataset.hasPassword = '1';
        localStorage.removeItem('ls_user');   // force a fresh profile next load
        document.getElementById('settings-new-pw').value = '';
        document.getElementById('settings-current-pw').value = '';
        msg.style.color = '#3ee87b';
        msg.textContent = 'Password saved. You can now log in with your email and password.';
        msg.classList.remove('hidden');
      } catch (err) {
        msg.style.color = '#ff6b6b';
        msg.textContent = err.message;
        msg.classList.remove('hidden');
      } finally {
        pwBtn.disabled = false;
        renderSettingsPwSection(modal);
      }
    });

    document.querySelectorAll('.settings-oauth-row [data-role="action"]').forEach(btn => {
      btn.addEventListener('click', () => {
        const row = btn.closest('.settings-oauth-row');
        const connected = row.querySelector('[data-role="status"]').classList.contains('is-connected');
        _handleOAuthAction(row.dataset.provider, connected);
      });
    });

    document.getElementById('settings-adjust-plan-btn').addEventListener('click', (e) => {
      _openBillingPortalFlow('subscription_update', e.currentTarget);
    });
    document.getElementById('settings-update-payment-btn').addEventListener('click', (e) => {
      _openBillingPortalFlow('payment_method_update', e.currentTarget);
    });
    document.getElementById('settings-cancel-btn').addEventListener('click', _toggleCancelSubscription);

    document.getElementById('settings-marketing-toggle').addEventListener('change', (e) => {
      _saveMarketingPreference(!e.target.checked);
    });

    document.getElementById('settings-delete-open-btn').addEventListener('click', () => {
      document.getElementById('settings-delete-confirm').classList.remove('hidden');
      document.getElementById('settings-delete-confirm-input').focus();
    });
    document.getElementById('settings-delete-cancel-btn').addEventListener('click', _resetDeleteConfirm);
    document.getElementById('settings-delete-confirm-input').addEventListener('input', (e) => {
      document.getElementById('settings-delete-confirm-btn').disabled = e.target.value !== modal.dataset.email;
    });
    document.getElementById('settings-delete-confirm-btn').addEventListener('click', _deleteOwnAccount);
  }

  modal.dataset.hasPassword = user.has_password ? '1' : '0';
  modal.dataset.isGoogle = user.google ? '1' : '0';
  modal.dataset.email = user.email || '';
  delete modal.dataset.billingLoaded;   // re-fetch Stripe data fresh each time the modal opens

  document.getElementById('settings-account-email').textContent = user.email || '';
  document.getElementById('settings-pw-msg').classList.add('hidden');
  document.getElementById('settings-new-pw').value = '';
  document.getElementById('settings-current-pw').value = '';
  renderSettingsPwSection(modal);

  renderConnectedAccounts(user);
  renderSettingsBilling(user);
  document.getElementById('settings-subscription-block').classList.add('hidden');   // shown once loadSettingsSubscription() resolves, if the Billing tab is opened
  // If the modal was left open on the Billing tab last time, the nav click that
  // normally triggers the fetch won't fire again — load it directly instead.
  if (document.querySelector('.settings-nav-item.is-active')?.dataset.panel === 'billing') {
    modal.dataset.billingLoaded = '1';
    loadSettingsSubscription();
  }
  document.getElementById('settings-marketing-toggle').checked = !user.marketing_opt_out;
  document.getElementById('settings-delete-email-hint').textContent = user.email || '';
  _resetDeleteConfirm();

  modal.classList.remove('hidden');
}
