/* ===== TOAST ===== */
function showToast(message, type = 'error') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    const prefix = type === 'error' ? '[ERR] ' : '[WARN] ';
    el.className = `toast toast-${type}`;
    el.textContent = prefix + message;
    container.appendChild(el);
    requestAnimationFrame(() => el.classList.add('toast-show'));
    setTimeout(() => {
        el.classList.add('toast-hide');
        el.addEventListener('transitionend', () => el.remove(), { once: true });
    }, 4000);
}

/* ===== STATE ===== */
const state = {
    map: null,
    searchCircle: null,
    centerMarker: null,
    markersLayer: null,
    allLeads: [],
    filteredLeads: [],
    seenUrls: new Set(),   // deduplication across searches
    sortCol: null,
    sortAsc: true,
    totalScanned: 0,
    totalSkipped: 0,
    // multi-scan
    bulkMode: false,
    bulkTargets: [],       // [{latlng, circle, marker}]
};

/* ===== CUSTOM ICONS ===== */
const orangePin = L.divIcon({
    className: '',
    html: `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">
        <rect x="0" y="0" width="18" height="18" fill="#ff2200" opacity="0.85"/>
        <rect x="6" y="6" width="6" height="6" fill="#0a0a0a"/>
    </svg>`,
    iconSize: [18, 18],
    iconAnchor: [9, 9],
    popupAnchor: [0, -12],
});

// Drag handle: terminal crosshair
const dragHandleIcon = L.divIcon({
    className: '',
    html: `<svg xmlns="http://www.w3.org/2000/svg" width="44" height="44" viewBox="0 0 44 44">
        <rect x="21" y="2" width="2" height="40" fill="#33ff00" opacity="0.8"/>
        <rect x="2" y="21" width="40" height="2" fill="#33ff00" opacity="0.8"/>
        <rect x="16" y="16" width="12" height="12" fill="rgba(51,255,0,0.12)" stroke="#33ff00" stroke-width="1"/>
        <rect x="19" y="19" width="6" height="6" fill="#33ff00" opacity="0.6"/>
    </svg>`,
    iconSize: [44, 44],
    iconAnchor: [22, 22],
});

/* ===== BULK TARGET ICON ===== */
const bulkTargetIcon = L.divIcon({
    className: '',
    html: `<div style="width:64px;height:64px;display:flex;align-items:center;justify-content:center;position:relative;cursor:pointer">
        <svg xmlns="http://www.w3.org/2000/svg" width="36" height="36" viewBox="0 0 36 36">
            <line x1="18" y1="2" x2="18" y2="12" stroke="#ffb000" stroke-width="1.5" opacity="0.9"/>
            <line x1="18" y1="24" x2="18" y2="34" stroke="#ffb000" stroke-width="1.5" opacity="0.9"/>
            <line x1="2" y1="18" x2="12" y2="18" stroke="#ffb000" stroke-width="1.5" opacity="0.9"/>
            <line x1="24" y1="18" x2="34" y2="18" stroke="#ffb000" stroke-width="1.5" opacity="0.9"/>
            <circle cx="18" cy="18" r="8" fill="rgba(255,176,0,0.08)" stroke="#ffb000" stroke-width="1.5" stroke-dasharray="3 2"/>
            <circle cx="18" cy="18" r="2.5" fill="#ffb000" opacity="0.95"/>
        </svg>
        <span style="position:absolute;top:4px;right:4px;font-family:monospace;font-size:10px;color:#ffb000;line-height:1;opacity:0.7">×</span>
    </div>`,
    iconSize: [64, 64],
    iconAnchor: [32, 32],
});

/* ===== BULK SCAN FUNCTIONS ===== */
function addBulkTarget(latlng) {
    const radius = parseInt(document.getElementById('radius-slider').value, 10);
    const circle = L.circle(latlng, {
        radius,
        color: '#ffb000',
        fillColor: '#ffb000',
        fillOpacity: 0.05,
        weight: 1.5,
        dashArray: '5 4',
    }).addTo(state.map);

    const marker = L.marker(latlng, {
        icon: bulkTargetIcon,
        zIndexOffset: 900,
    }).addTo(state.map);

    const target = { latlng, circle, marker };
    marker.on('click', e => {
        L.DomEvent.stopPropagation(e);
        const idx = state.bulkTargets.indexOf(target);
        if (idx !== -1) {
            state.map.removeLayer(target.circle);
            state.map.removeLayer(target.marker);
            state.bulkTargets.splice(idx, 1);
            updateBulkBtn();
        }
    });

    state.bulkTargets.push(target);
    updateBulkBtn();
}

function clearBulkTargets() {
    state.bulkTargets.forEach(t => {
        state.map.removeLayer(t.circle);
        state.map.removeLayer(t.marker);
    });
    state.bulkTargets = [];
}

function updateBulkBtn() {
    const btn = document.getElementById('multi-btn');
    if (!btn) return;
    if (state.bulkMode) {
        const n = state.bulkTargets.length;
        btn.classList.add('bulk-active');
        btn.querySelector('svg').setAttribute('stroke', 'currentColor');
        const label = n === 0 ? 'MULTI' : `MULTI [${n}]`;
        btn.childNodes[btn.childNodes.length - 1].textContent = label;
    } else {
        btn.classList.remove('bulk-active');
        btn.childNodes[btn.childNodes.length - 1].textContent = 'MULTI';
    }
}

function toggleBulkMode() {
    if (state.bulkMode) {
        // Turn off: clear all targets
        clearBulkTargets();
        state.bulkMode = false;
        // Restore main circle/marker visibility
        if (state.searchCircle) state.searchCircle.setStyle({ opacity: 1, fillOpacity: 0.05 });
        if (state.centerMarker) state.centerMarker.setOpacity(1);
    } else {
        state.bulkMode = true;
        // Dim main circle/marker
        if (state.searchCircle) state.searchCircle.setStyle({ opacity: 0.25, fillOpacity: 0.02 });
        if (state.centerMarker) state.centerMarker.setOpacity(0.25);
    }
    updateBulkBtn();
}

/* ===== MAP INIT ===== */
function initMap() {
    state.map = L.map('map', { zoomControl: true }).setView([40.0379, -76.3055], 11); // Lancaster, PA default
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
        subdomains: 'abcd',
        maxZoom: 20,
    }).addTo(state.map);

    state.markersLayer = L.layerGroup().addTo(state.map);

    const defaultRadius = parseInt(document.getElementById('radius-slider').value, 10);
    const defaultCenter = state.map.getCenter();

    // Draw the search circle
    state.searchCircle = L.circle(defaultCenter, {
        radius: defaultRadius,
        color: '#33ff00',
        fillColor: '#33ff00',
        fillOpacity: 0.05,
        weight: 2,
        dashArray: '6 4',
    }).addTo(state.map);

    // Draggable center marker
    state.centerMarker = L.marker(defaultCenter, {
        draggable: true,
        icon: dragHandleIcon,
        zIndexOffset: 1000,
    }).addTo(state.map);

    // Circle follows marker while dragging
    state.centerMarker.on('drag', e => {
        state.searchCircle.setLatLng(e.target.getLatLng());
    });

    // Click anywhere on the map to reposition (normal) or place target (bulk)
    state.map.on('click', e => {
        if (e.originalEvent.target.closest && e.originalEvent.target.closest('.leaflet-popup')) return;
        if (state.bulkMode) {
            addBulkTarget(e.latlng);
        } else {
            state.centerMarker.setLatLng(e.latlng);
            state.searchCircle.setLatLng(e.latlng);
        }
    });
}

/* ===== RADIUS SLIDER ===== */
function initRadiusSlider() {
    const slider = document.getElementById('radius-slider');
    const label  = document.getElementById('radius-label');

    function updateRadius() {
        const meters = parseInt(slider.value, 10);
        const miles = meters / 1609.34;
        label.textContent = miles >= 10 ? `${Math.round(miles)}mi` : `${miles.toFixed(1)}mi`;
        if (state.searchCircle) state.searchCircle.setRadius(meters);
        state.bulkTargets.forEach(t => t.circle.setRadius(meters));
    }

    slider.addEventListener('input', updateRadius);
    updateRadius();
}

/* ===== MY LOCATION ===== */
function initLocateBtn() {
    document.getElementById('locate-btn').addEventListener('click', () => {
        if (!navigator.geolocation) {
            showToast('Geolocation not supported by your browser.', 'warn');
            return;
        }
        navigator.geolocation.getCurrentPosition(
            pos => {
                const latlng = L.latLng(pos.coords.latitude, pos.coords.longitude);
                state.centerMarker.setLatLng(latlng);
                state.searchCircle.setLatLng(latlng);
                state.map.flyTo(latlng, state.map.getZoom(), { duration: 1 });
            },
            () => showToast('Could not get location. Allow location access in your browser.', 'warn')
        );
    });
}

/* ===== XSS HELPER ===== */
function esc(str) {
    return String(str ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/* ===== MAP: ADD NEW RESULT PINS (accumulates, doesn't clear) ===== */
function addResultPins(leads) {
    leads.forEach(lead => {
        if (lead.lat == null || lead.lng == null) return;

        const marker = L.marker([lead.lat, lead.lng], { icon: orangePin });

        const ratingHtml = lead.rating != null
            ? `<div class="popup-row">RAT: ${lead.rating} [${(lead.reviews ?? 0).toLocaleString()} reviews]</div>`
            : '';

        marker.bindPopup(`
            <div class="popup-name">&gt; ${esc(lead.name)}</div>
            <div class="popup-row">TEL: ${esc(lead.phone)}</div>
            ${ratingHtml}
            <a href="${esc(lead.maps_url)}" target="_blank" rel="noopener" class="popup-link">[ VIEW PROFILE ]</a>
        `);

        state.markersLayer.addLayer(marker);
    });
}

/* ===== PAYWALL MODAL ===== */
function showPaywallModal(message) {
    let modal = document.getElementById('paywall-modal');
    if (modal) modal.remove();
    modal = document.createElement('div');
    modal.id = 'paywall-modal';
    modal.innerHTML = `
        <div class="paywall-box">
            <div class="paywall-title">&gt; LIMIT_REACHED</div>
            <p class="paywall-msg">${message}</p>
            <a href="/pricing" class="auth-btn paywall-upgrade-btn">[ UPGRADE PLAN ]</a>
            <button class="paywall-close" onclick="document.getElementById('paywall-modal').remove()">[ DISMISS ]</button>
        </div>`;
    document.body.appendChild(modal);
}

/* ===== SEARCH HANDLER ===== */
async function handleSearch() {
    const category = document.getElementById('category-input').value.trim();

    if (!category) {
        showToast('Please enter a target category.', 'warn');
        return;
    }

    const token = typeof getToken === 'function' ? getToken() : null;
    if (!token) {
        window.location.href = '/login';
        return;
    }

    const radius = parseInt(document.getElementById('radius-slider').value, 10);

    // ── BULK SCAN ──
    if (state.bulkMode) {
        if (state.bulkTargets.length === 0) {
            showToast('No targets placed. Click the map to add scan areas.', 'warn');
            return;
        }
        setLoading(true);
        const total = state.bulkTargets.length;
        for (let i = 0; i < total; i++) {
            document.getElementById('search-btn').textContent = `[ SCANNING ${i + 1}/${total}... ]`;
            const { latlng } = state.bulkTargets[i];
            try {
                const resp = await fetch('/api/search', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${token}` },
                    body: JSON.stringify({ category, lat: latlng.lat, lng: latlng.lng, radius_meters: radius }),
                });
                const data = await resp.json();
                if (resp.status === 401) { window.location.href = '/login'; return; }
                if (resp.status === 429) {
                    showPaywallModal('You\'ve reached your monthly limit. Upgrade to keep scanning.');
                    setLoading(false);
                    return;
                }
                if (!resp.ok) { showToast(`Zone ${i + 1}: ${data.detail || 'Search failed'}`, 'error'); continue; }
                if (data.usage && typeof renderUsage === 'function') {
                    renderUsage(data.usage);
                    localStorage.removeItem('ls_user');
                }
                const newLeads = data.leads.filter(l => !state.seenUrls.has(l.maps_url));
                newLeads.forEach(l => state.seenUrls.add(l.maps_url));
                state.allLeads = [...state.allLeads, ...newLeads];
                state.totalScanned += data.total_found;
                state.totalSkipped += data.skipped_has_website;
                addResultPins(newLeads);
            } catch (err) {
                showToast(`Zone ${i + 1}: ${err.message}`, 'error');
            }
        }
        setLoading(false);
        applyFilterAndRender();
        showLeadsUI();
        return;
    }

    // ── SINGLE SCAN ──
    const { lat, lng } = state.centerMarker.getLatLng();
    setLoading(true);

    try {
        const resp = await fetch('/api/search', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`,
            },
            body: JSON.stringify({ category, lat, lng, radius_meters: radius }),
        });

        const data = await resp.json();

        if (resp.status === 401) {
            window.location.href = '/login';
            return;
        }

        if (resp.status === 429) {
            showPaywallModal('You\'ve reached your monthly limit. Upgrade to keep scanning.');
            return;
        }

        if (!resp.ok) {
            throw new Error(data.detail || 'Search failed');
        }

        if (data.usage && typeof renderUsage === 'function') {
            renderUsage(data.usage);
            localStorage.removeItem('ls_user');
        }

        const newLeads = data.leads.filter(l => !state.seenUrls.has(l.maps_url));
        newLeads.forEach(l => state.seenUrls.add(l.maps_url));
        state.allLeads = [...state.allLeads, ...newLeads];
        state.totalScanned += data.total_found;
        state.totalSkipped += data.skipped_has_website;

        addResultPins(newLeads);
        applyFilterAndRender();
        showLeadsUI();

    } catch (err) {
        showToast(err.message);
    } finally {
        setLoading(false);
    }
}

/* ===== FILTER + SORT + RENDER ===== */
function applyFilterAndRender() {
    const query = (document.getElementById('filter-input').value || '').toLowerCase();

    state.filteredLeads = state.allLeads.filter(lead =>
        (lead.name || '').toLowerCase().includes(query) ||
        (lead.city || '').toLowerCase().includes(query)
    );

    if (state.sortCol) {
        state.filteredLeads.sort((a, b) => {
            let va = a[state.sortCol] ?? '';
            let vb = b[state.sortCol] ?? '';
            if (typeof va === 'string') va = va.toLowerCase();
            if (typeof vb === 'string') vb = vb.toLowerCase();
            if (va < vb) return state.sortAsc ? -1 : 1;
            if (va > vb) return state.sortAsc ? 1 : -1;
            return 0;
        });
    }

    renderTable();

    const count = state.filteredLeads.length;
    const total = state.allLeads.length;
    const foundLabel = count === total
        ? `${total} LEAD${total !== 1 ? 'S' : ''} FOUND`
        : `${count}/${total} LEADS (FILTERED)`;
    let countText = total > 0
        ? `&gt; <span class="found-text">${foundLabel}</span>`
        : `&gt; ${foundLabel}`;
    if (state.totalScanned > 0) {
        countText += `<span class="count-meta"> [${state.totalScanned} SCANNED / ${state.totalSkipped} SKIPPED]</span>`;
    }
    document.getElementById('lead-count').innerHTML = countText;
}

function renderTable() {
    const tbody = document.getElementById('leads-tbody');
    tbody.innerHTML = '';

    if (state.filteredLeads.length === 0) {
        const tr = document.createElement('tr');
        tr.id = 'empty-row';
        tr.innerHTML = `<td colspan="5">${
            state.allLeads.length === 0
                ? 'No leads found — try a broader search or different category'
                : 'No matches for that filter'
        }</td>`;
        tbody.appendChild(tr);
        return;
    }

    state.filteredLeads.forEach(lead => {
        const tr = document.createElement('tr');

        const ratingCell = lead.rating != null
            ? `<span class="rating-badge">[${lead.rating}]</span>`
            : `<span style="color:#1f521f">--</span>`;

        tr.innerHTML = `
            <td class="name-cell" title="${esc(lead.name)}"><a href="${esc(lead.maps_url)}" target="_blank" rel="noopener" class="name-link">${esc(lead.name)}</a></td>
            <td class="muted">${esc(lead.phone)}</td>
            <td class="muted" title="${esc(lead.city)}">${esc(lead.city)}</td>
            <td>${ratingCell}</td>
            <td class="muted">${lead.reviews != null ? lead.reviews.toLocaleString() : '—'}</td>
        `;
        tbody.appendChild(tr);
    });
}

/* ===== SORT HEADERS ===== */
function initSortHeaders() {
    document.querySelectorAll('#leads-table thead th[data-col]').forEach(th => {
        th.addEventListener('click', () => {
            const col = th.dataset.col;
            if (state.sortCol === col) {
                state.sortAsc = !state.sortAsc;
            } else {
                state.sortCol = col;
                state.sortAsc = true;
            }
            clearSortClasses();
            th.classList.add(state.sortAsc ? 'sort-asc' : 'sort-desc');
            applyFilterAndRender();
        });
    });
}

function clearSortClasses() {
    document.querySelectorAll('#leads-table thead th').forEach(h => h.classList.remove('sort-asc', 'sort-desc'));
}

/* ===== EXPORT ===== */
function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

function exportCSV() {
    const headers = ['Name', 'Phone', 'City', 'Rating', 'Reviews', 'Google Maps URL'];
    const rows = state.filteredLeads.map(lead => [
        lead.name, lead.phone || '', lead.city,
        lead.rating ?? '', lead.reviews ?? '', lead.maps_url,
    ]);
    const content = [headers, ...rows]
        .map(row => row.map(cell => `"${String(cell).replace(/"/g, '""')}"`).join(','))
        .join('\r\n');
    downloadBlob(new Blob(['\uFEFF' + content], { type: 'text/csv;charset=utf-8;' }),
        `leads-${new Date().toISOString().slice(0, 10)}.csv`);
}

function exportJSON() {
    const data = state.filteredLeads.map(({ name, phone, city, rating, reviews, maps_url }) =>
        ({ name, phone, city, rating, reviews, maps_url }));
    downloadBlob(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
        `leads-${new Date().toISOString().slice(0, 10)}.json`);
}

function exportXLSX() {
    const rows = state.filteredLeads.map(lead => ({
        Name: lead.name,
        Phone: lead.phone || '',
        City: lead.city,
        Rating: lead.rating ?? '',
        Reviews: lead.reviews ?? '',
        'Google Maps URL': lead.maps_url,
    }));
    const ws = XLSX.utils.json_to_sheet(rows);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Leads');
    XLSX.writeFile(wb, `leads-${new Date().toISOString().slice(0, 10)}.xlsx`);
}

function doExport(format) {
    document.getElementById('export-menu').classList.add('hidden');
    if (format === 'csv') exportCSV();
    else if (format === 'json') exportJSON();
    else if (format === 'xlsx') exportXLSX();
}

/* ===== CLEAR ALL ===== */
function clearAllLeads() {
    state.allLeads = [];
    state.filteredLeads = [];
    state.seenUrls = new Set();
    state.totalScanned = 0;
    state.totalSkipped = 0;
    state.markersLayer.clearLayers();
    document.getElementById('lead-count').innerHTML = '&gt; awaiting input_';
    document.getElementById('filter-input').value = '';
    renderTable();
}

/* ===== EXPLOSION ANIMATION ===== */
let _missileImgCache = null;
function loadMissileImage() {
    if (_missileImgCache) return Promise.resolve(_missileImgCache);
    return new Promise(resolve => {
        const img = new Image();
        img.onload = () => {
            // Strip white background from the JPG
            const off = document.createElement('canvas');
            off.width = img.width; off.height = img.height;
            const octx = off.getContext('2d');
            octx.drawImage(img, 0, 0);
            const d = octx.getImageData(0, 0, off.width, off.height);
            for (let i = 0; i < d.data.length; i += 4) {
                if (d.data[i] > 210 && d.data[i+1] > 210 && d.data[i+2] > 210)
                    d.data[i+3] = 0;
            }
            octx.putImageData(d, 0, 0);
            _missileImgCache = off;
            resolve(off);
        };
        img.src = '/static/missile.jpg';
    });
}

async function explodeAndClear() {
    const targets = [];
    state.markersLayer.eachLayer(marker => {
        const pt = state.map.latLngToContainerPoint(marker.getLatLng());
        targets.push({ x: pt.x, y: pt.y });
    });
    if (targets.length === 0) { clearAllLeads(); return; }

    const missileImg = await loadMissileImage();

    const mapEl = document.getElementById('map');
    const rect   = mapEl.getBoundingClientRect();
    const canvas = document.createElement('canvas');
    canvas.width  = rect.width;
    canvas.height = rect.height;
    canvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:900;';
    mapEl.appendChild(canvas);
    const ctx = canvas.getContext('2d');

    // Hide markers immediately
    state.markersLayer.eachLayer(m => {
        const el = m.getElement();
        if (el) el.style.opacity = '0';
    });

    const SIZE    = 38;
    const FLIGHT  = 1400;
    const STAGGER = Math.min(200, 3000 / targets.length);
    const DEBRIS_COLORS = ['#ff4400','#ff8800','#ffcc00','#ff2200','#ffaa00','#ffffff','#ff6600'];

    const missiles = targets.map((t, i) => ({
        sx: 30 + Math.random() * (rect.width - 60),
        sy: -SIZE - 10,
        tx: t.x, ty: t.y,
        delay: i * STAGGER,
        done: false,
    }));

    const particles = [];
    const flashes   = [];

    function boom(x, y, now) {
        flashes.push({ x, y, r: 0, born: now, life: 380 });
        for (let i = 0; i < 28; i++) {
            const angle = Math.random() * Math.PI * 2;
            const speed = 1.5 + Math.random() * 7;
            const shrapnel = Math.random() > 0.45;
            particles.push({
                x, y,
                vx: Math.cos(angle) * speed,
                vy: Math.sin(angle) * speed - 2.5,
                w: shrapnel ? 2 + Math.random() * 3  : 5 + Math.random() * 9,
                h: shrapnel ? 7 + Math.random() * 12 : 5 + Math.random() * 9,
                rot: Math.random() * Math.PI * 2,
                spin: (Math.random() - 0.5) * 0.25,
                color: DEBRIS_COLORS[Math.floor(Math.random() * DEBRIS_COLORS.length)],
                born: now,
                life: 700 + Math.random() * 500,
            });
        }
    }

    const startTime = performance.now();

    function frame(now) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        let anyAlive = false;

        // — Missiles —
        missiles.forEach(m => {
            const elapsed = now - startTime - m.delay;
            if (elapsed < 0) { anyAlive = true; return; }
            const t = Math.min(elapsed / FLIGHT, 1);
            if (t < 1) {
                anyAlive = true;
                const x = m.sx + (m.tx - m.sx) * t;
                const y = m.sy + (m.ty - m.sy) * t;
                const angle = Math.atan2(m.ty - m.sy, m.tx - m.sx) + Math.PI / 2;
                ctx.save();
                ctx.translate(x, y);
                ctx.rotate(angle);
                ctx.drawImage(missileImg, -SIZE / 2, -SIZE / 2, SIZE, SIZE);
                ctx.restore();
            } else if (!m.done) {
                m.done = true;
                boom(m.tx, m.ty, now);
            }
        });

        // — Impact flashes —
        for (let i = flashes.length - 1; i >= 0; i--) {
            const f = flashes[i];
            const age = now - f.born;
            if (age > f.life) { flashes.splice(i, 1); continue; }
            anyAlive = true;
            const t = age / f.life;
            const r = 65 * t;
            const grad = ctx.createRadialGradient(f.x, f.y, 0, f.x, f.y, r);
            grad.addColorStop(0,   'rgba(255,255,220,' + (1 - t) + ')');
            grad.addColorStop(0.35,'rgba(255,140,0,'   + (1 - t) * 0.9 + ')');
            grad.addColorStop(1,   'rgba(255,40,0,0)');
            ctx.globalAlpha = 1;
            ctx.fillStyle = grad;
            ctx.beginPath();
            ctx.arc(f.x, f.y, r, 0, Math.PI * 2);
            ctx.fill();
        }

        // — Debris particles —
        for (let i = particles.length - 1; i >= 0; i--) {
            const p = particles[i];
            const age = now - p.born;
            if (age > p.life) { particles.splice(i, 1); continue; }
            anyAlive = true;
            p.x += p.vx; p.y += p.vy;
            p.vy += 0.2; p.vx *= 0.97;
            p.rot += p.spin;
            ctx.globalAlpha = Math.max(1 - age / p.life, 0);
            ctx.fillStyle = p.color;
            ctx.save();
            ctx.translate(p.x, p.y);
            ctx.rotate(p.rot);
            ctx.fillRect(-p.w / 2, -p.h / 2, p.w, p.h);
            ctx.restore();
        }

        ctx.globalAlpha = 1;
        if (anyAlive) {
            requestAnimationFrame(frame);
        } else {
            canvas.remove();
            clearAllLeads();
        }
    }

    requestAnimationFrame(frame);
}

/* ===== UI HELPERS ===== */
function setLoading(isLoading) {
    document.getElementById('spinner').classList.toggle('hidden', !isLoading);
    document.getElementById('search-btn').disabled = isLoading;
    if (!isLoading) document.getElementById('search-btn').textContent = '[ SCAN ]';
    const multiBtn = document.getElementById('multi-btn');
    if (multiBtn) multiBtn.disabled = isLoading;
}

function showLeadsUI() {
    document.getElementById('export-wrap').classList.remove('hidden');
    document.getElementById('clear-btn').classList.remove('hidden');
    document.getElementById('filter-wrap').classList.remove('hidden');
}

/* ===== BOOTSTRAP ===== */
document.addEventListener('DOMContentLoaded', () => {
    initMap();
    initRadiusSlider();
    initSortHeaders();
    initLocateBtn();

    document.getElementById('search-btn').addEventListener('click', handleSearch);
    document.getElementById('multi-btn').addEventListener('click', toggleBulkMode);
    document.getElementById('filter-input').addEventListener('input', applyFilterAndRender);
    document.getElementById('export-btn').addEventListener('click', e => {
        e.stopPropagation();
        document.getElementById('export-menu').classList.toggle('hidden');
    });
    document.addEventListener('click', () => {
        document.getElementById('export-menu')?.classList.add('hidden');
    });
    document.getElementById('clear-btn').addEventListener('click', explodeAndClear);

    document.getElementById('category-input').addEventListener('keydown', e => {
        if (e.key === 'Enter') handleSearch();
    });
});
