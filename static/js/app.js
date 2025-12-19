// static/js/app.js

/* ===============================
   GLOBAL STATE
================================ */

let currentTab = 'active';
let currentSearchType = 'album';
let currentPage = 1;
let itemsPerPage = 10;
let totalResults = 0;
let allSearchResults = [];

let eventSource = null;
let activeDownloads = new Map();
let downloadHistory = [];

const qualityCache = new Map();

/* ===============================
   SSE
================================ */

function initializeSSE() {
    if (eventSource) eventSource.close();

    eventSource = new EventSource('/api/events');

    eventSource.onerror = () => {
        setTimeout(initializeSSE, 5000);
    };

    eventSource.onmessage = (event) => {
        handleSSEMessage(JSON.parse(event.data));
    };
}

function handleSSEMessage(data) {
    switch (data.type) {
        case 'download_started': handleDownloadStarted(data); break;
        case 'download_progress': handleDownloadProgress(data); break;
        case 'download_completed': handleDownloadCompleted(data); break;
        case 'download_error': handleDownloadError(data); break;
    }
}

/* ===============================
   DOWNLOAD HANDLING
================================ */

function handleDownloadStarted(data) {
    activeDownloads.set(data.id, {
        id: data.id,
        metadata: data.metadata,
        status: 'downloading'
    });
    if (currentTab === 'active') renderActiveDownloads();
}

function handleDownloadProgress(data) {
    const d = activeDownloads.get(data.id);
    if (d) d.output = data.output;
}

function handleDownloadCompleted(data) {
    const d = activeDownloads.get(data.id);
    if (!d) return;

    d.status = data.status;
    d.output = data.output;

    setTimeout(() => {
        downloadHistory.unshift(d);
        activeDownloads.delete(data.id);
        if (currentTab === 'active') renderActiveDownloads();
    }, 2000);
}

function handleDownloadError(data) {
    const d = activeDownloads.get(data.id);
    if (!d) return;

    d.status = 'failed';
    d.error = data.error;

    setTimeout(() => {
        downloadHistory.unshift(d);
        activeDownloads.delete(data.id);
        if (currentTab === 'active') renderActiveDownloads();
    }, 2000);
}

function renderActiveDownloads() {
    const el = document.getElementById('activeDownloads');
    if (!el) return;

    if (!activeDownloads.size) {
        el.innerHTML = `<div class="empty-state">NO ACTIVE DOWNLOADS</div>`;
        return;
    }

    el.innerHTML = [...activeDownloads.values()].map(d => `
        <div class="download-item ${d.status}">
            <div class="download-content">
                <div class="download-info">
                    <div class="download-title">${d.metadata?.title || 'Unknown'}</div>
                    <div class="download-artist">${d.metadata?.artist || ''}</div>
                    <span class="status-badge ${d.status}">${d.status}</span>
                </div>
                <div class="download-spinner"></div>
            </div>
        </div>
    `).join('');
}

/* ===============================
   SEARCH
================================ */

function setSearchType(type, el) {
    currentSearchType = type;
    document.querySelectorAll('.search-type-btn').forEach(b => b.classList.remove('active'));
    el.classList.add('active');
}

async function searchMusic() {
    const query = document.getElementById('searchInput').value.trim();
    const source = document.getElementById('searchSource').value;
    if (!query) return alert('Enter search query');

    document.getElementById('searchResults').innerHTML =
        `<div class="empty-state">SEARCHING…</div>`;

    const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, type: currentSearchType, source })
    });

    const data = await res.json();
    if (!res.ok || !data.results) {
        document.getElementById('searchResults').innerHTML =
            `<div class="empty-state">SEARCH FAILED</div>`;
        return;
    }

    allSearchResults = data.results;
    totalResults = data.results.length;
    currentPage = 1;
    displayCurrentPage();
}

function displayCurrentPage() {
    const start = (currentPage - 1) * itemsPerPage;
    const page = allSearchResults.slice(start, start + itemsPerPage);

    const el = document.getElementById('searchResults');
    if (!page.length) {
        el.innerHTML = `<div class="empty-state">NO RESULTS</div>`;
        return;
    }

    el.innerHTML = page.map(r => `
        <div class="search-result-item"
            data-id="${r.id}"
            data-source="${r.service}"
            data-type="${r.type}">

            <div class="result-album-art placeholder" id="art-${r.id}">🎵</div>

            <div class="result-info">
                <span class="result-service">${r.service}</span>

                ${r.service === 'qobuz' && (r.type === 'track' || r.type === 'album')
                    ? `<span class="quality-badge loading" id="quality-${r.id}">
                        Checking…
                       </span>`
                    : ''}

                <div class="result-title">${r.title || ''}</div>
                <div class="result-artist">${r.artist || r.desc}</div>
                <div class="result-id">${r.id}</div>
            </div>

            <button class="result-download-btn"
                onclick="downloadFromUrl('${r.url}')">
                DOWNLOAD
            </button>
        </div>
    `).join('');

    updatePaginationControls();
    loadAlbumArtForVisibleItems();
    inspectVisibleMediaQuality();
}

/* ===============================
   PAGINATION
================================ */

function updatePaginationControls() {
    const pages = Math.ceil(totalResults / itemsPerPage);
    document.getElementById('pageInfo').textContent =
        `Page ${currentPage} of ${pages}`;
}

function changePage(dir) {
    const pages = Math.ceil(totalResults / itemsPerPage);
    const next = currentPage + dir;
    if (next < 1 || next > pages) return;
    currentPage = next;
    displayCurrentPage();
}

/* ===============================
   QUALITY INSPECTION (TRACKS + ALBUMS)
================================ */

async function fetchMediaQuality(source, type, id) {
    const key = `${source}:${type}:${id}`;
    if (qualityCache.has(key)) return qualityCache.get(key);

    const res = await fetch('/api/quality', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ source, type, id })
    });

    const data = await res.json();
    qualityCache.set(key, data);
    return data;
}

function applyQuality(id, data) {
    const el = document.getElementById(`quality-${id}`);
    if (!el) return;

    // reset state
    el.classList.remove('loading', 'hires', 'cd', 'lossy', 'unknown');

    const q = data?.quality;

    if (!q || (!q.bit_depth && !q.sample_rate)) {
        el.textContent = 'Unknown';
        el.classList.add('unknown');
        return;
    }

    // Prefer backend-generated label
    el.textContent = q.label
        || `${q.bit_depth}-bit / ${q.sample_rate} kHz`;

    if (q.hires || q.bit_depth > 16) {
        el.classList.add('hires');
    } else {
        el.classList.add('cd');
    }
}

function inspectVisibleMediaQuality() {
    requestAnimationFrame(() => {
        document.querySelectorAll('.search-result-item').forEach(async el => {
            let source = (el.dataset.source || '').toLowerCase();
            let type = (el.dataset.type || '').toLowerCase();
            const id = el.dataset.id;

            // normalize streamrip plurals
            if (type === 'tracks') type = 'track';
            if (type === 'albums') type = 'album';

            if (source === 'qobuz' && (type === 'track' || type === 'album')) {
                const data = await fetchMediaQuality(source, type, id);
                applyQuality(id, data);
            }
        });
    });
}




/* ===============================
   ALBUM ART
================================ */

async function loadAlbumArtForVisibleItems() {
    document.querySelectorAll('.search-result-item').forEach(async el => {
        const id = el.dataset.id;
        const src = el.dataset.source;
        const type = el.dataset.type;

        const res = await fetch(`/api/album-art?source=${src}&type=${type}&id=${id}`);
        const data = await res.json();
        if (!data.album_art) return;

        document.getElementById(`art-${id}`).innerHTML =
            `<img src="${data.album_art}" class="result-album-art">`;
    });
}

/* ===============================
   DOWNLOAD
================================ */

async function downloadFromUrl(url) {
    const q = document.getElementById('qualitySelect').value;
    await fetch('/api/download-from-url', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url, quality: parseInt(q) })
    });
    switchTab('active');
}

/* ===============================
   TABS / INIT
================================ */

function switchTab(tab) {
    currentTab = tab;
    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.getElementById(`${tab}Tab`).classList.add('active');
    if (tab === 'active') renderActiveDownloads();
}

window.addEventListener('load', initializeSSE);
