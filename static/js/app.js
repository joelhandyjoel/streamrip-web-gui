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

    eventSource.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleSSEMessage(data);
    };

    eventSource.onerror = () => {
        setTimeout(initializeSSE, 5000);
    };
}

function handleSSEMessage(data) {
    switch (data.type) {
        case 'download_started':
            handleDownloadStarted(data);
            break;
        case 'download_progress':
            handleDownloadProgress(data);
            break;
        case 'download_completed':
            handleDownloadCompleted(data);
            break;
        case 'download_error':
            handleDownloadError(data);
            break;
    }
}

/* ===============================
   DOWNLOAD HANDLING
================================ */

function handleDownloadStarted(data) {
    activeDownloads.set(data.id, {
       id: data.id,
       metadata: data.metadata,
       status: 'downloading',
       logs: []
   });
    if (currentTab === 'active') renderActiveDownloads();
}

function handleDownloadProgress(data) {
    const d = activeDownloads.get(data.id);
    if (!d) return;

    if (data.line) {
        d.logs.push(data.line);
        updateDownloadLog(data.id, d.logs);
    }
}


function handleDownloadCompleted(data) {
    const d = activeDownloads.get(data.id);
    if (!d) return;

    d.status = data.status || 'completed';
    d.output = data.output || '';

    // move to history FIRST
    downloadHistory.unshift({
        ...d,
        completedAt: Date.now()
    });

    activeDownloads.delete(data.id);

    // render ONLY the currently visible tab
    if (currentTab === 'active') {
        renderActiveDownloads();
    } else if (currentTab === 'history') {
        renderDownloadHistory();
    }
}

function updateDownloadLog(id, logs) {
    const pre = document.getElementById(`log-${id}`);
    if (!pre) return;

    pre.textContent = logs.join('\n');
    pre.scrollTop = pre.scrollHeight;
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
        el.innerHTML = '<div class="empty-state">NO ACTIVE DOWNLOADS</div>';
        return;
    }

    el.innerHTML = [...activeDownloads.values()].map(d => `
        <div class="download-item ${d.status}">
            <div class="download-content">
                <div class="download-info">
                    <div class="download-title">${d.metadata.title || 'Unknown'}</div>
                    <div class="download-artist">${d.metadata.artist || ''}</div>
                    <span class="status-badge ${d.status}">${d.status}</span>

                    <button class="toggle-log-btn"
                            onclick="document.getElementById('log-wrap-${d.id}').classList.toggle('visible')">
                        SHOW LOG
                    </button>
                </div>

                <div class="download-spinner"></div>
            </div>

            <div class="download-log" id="log-wrap-${d.id}">
                <pre id="log-${d.id}">${(d.logs || []).join('\n')}</pre>
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

    if (!query) {
        alert('Enter search query');
        return;
    }

    document.getElementById('searchResults').innerHTML =
        '<div class="empty-state">SEARCHING...</div>';

    const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, type: currentSearchType, source })
    });

    const data = await res.json();

    if (!res.ok || !data.results) {
        document.getElementById('searchResults').innerHTML =
            '<div class="empty-state">SEARCH FAILED</div>';
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
        el.innerHTML = '<div class="empty-state">NO RESULTS</div>';
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
                    ? `<span class="quality-badge loading" id="quality-${r.id}">Checking...</span>`
                    : ''}

                <div class="result-title">${r.title || ''}</div>
                <div class="result-artist">${r.artist || r.desc || ''}</div>
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
   QUALITY (TRACKS + ALBUMS)
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

    el.classList.remove('loading', 'hires', 'cd', 'unknown');

    if (!data || !data.quality || !data.quality.bit_depth) {
        el.textContent = 'Unknown';
        el.classList.add('unknown');
        return;
    }

    const q = data.quality;
    el.textContent = q.label || `${q.bit_depth}-bit / ${q.sample_rate} kHz`;
    el.classList.add(q.hires || q.bit_depth > 16 ? 'hires' : 'cd');
}

function inspectVisibleMediaQuality() {
    document.querySelectorAll('.search-result-item').forEach(async el => {
        const { source, type, id } = el.dataset;
        if (source === 'qobuz' && (type === 'track' || type === 'album')) {
            const data = await fetchMediaQuality(source, type, id);
            applyQuality(id, data);
        }
    });
}

/* ===============================
   ALBUM ART
================================ */

async function loadAlbumArtForVisibleItems() {
    document.querySelectorAll('.search-result-item').forEach(async el => {
        const { id, source, type } = el.dataset;

        const res = await fetch(`/api/album-art?source=${source}&type=${type}&id=${id}`);
        const data = await res.json();
        if (!data.album_art) return;

        const art = document.getElementById(`art-${id}`);
        if (art) {
            art.innerHTML = `<img src="${data.album_art}" class="result-album-art">`;
            art.classList.remove('placeholder');
        }
    });
}

/* ===============================
   DOWNLOAD
================================ */

async function downloadFromUrl(url) {
    const quality = document.getElementById('qualitySelect').value;

    // Find metadata from the clicked result
    let metadata = {};

    document.querySelectorAll('.search-result-item').forEach(item => {
        const btn = item.querySelector('.result-download-btn');
        if (btn && btn.getAttribute('onclick')?.includes(url)) {
            metadata = {
                title: item.querySelector('.result-title')?.textContent || '',
                artist: item.querySelector('.result-artist')?.textContent || '',
                service: item.dataset.source || '',
                album_art: item.querySelector('.result-album-art img')?.src || ''
            };
        }
    });

    // 🚫 Prevent double-clicks
    const buttons = document.querySelectorAll('.result-download-btn');
    buttons.forEach(b => b.disabled = true);

    try {
        await fetch('/api/download-from-url', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url,
                quality: parseInt(quality),
                metadata     // ✅ IMPORTANT
            })
        });

        switchTab('active');

    } finally {
        buttons.forEach(b => b.disabled = false);
    }
}


/* ===============================
   CONFIG
================================ */

async function loadConfig() {
    const res = await fetch('/api/config');
    const data = await res.json();
    document.getElementById('configEditor').value = data.config || '';
}

/* ===============================
   FILES
================================ */

async function loadFiles() {
    try {
        const res = await fetch('/api/browse');
        const items = await res.json();

        const container = document.getElementById('fileList');

        if (!items.length) {
            container.innerHTML = '<div class="empty-state">NO FILES FOUND</div>';
            return;
        }

        container.innerHTML = items.map(item => {

            // =====================
            // ALBUM (FOLDER)
            // =====================
            if (item.type === 'album') {
                return `
                    <div class="album-block">
            
                        <div class="album-row"
                             onclick="this.nextElementSibling.classList.toggle('hidden')">
                            <div class="album-title">
                                📁 ${item.name}
                            </div>
            
                            <div class="album-actions">
                                <button class="delete-album-btn"
                                        onclick="event.stopPropagation(); deleteFolder('${item.name}')">
                                    DELETE ALBUM
                                </button>
                            </div>
                        </div>
            
                        <!-- CLOSED BY DEFAULT -->
                        <div class="album-tracks hidden">
                            ${item.tracks.map(t => `
                                <div class="file-row">
                                    <div class="file-name">
                                        ${t.name}
                                    </div>
            
                                    <div class="file-actions">
                                        <button class="file-delete-btn"
                                                onclick="deleteFile('${t.path}')">
                                            DELETE
                                        </button>
                                    </div>
                                </div>
                            `).join('')}
                        </div>
            
                    </div>
                `;
            }


            // =====================
            // LOOSE FILE
            // =====================
            return `
                <div class="file-row">
                    <div class="file-name">
                        ${item.name}
                    </div>

                    <div class="file-actions">
                        <button class="file-delete-btn"
                                onclick="deleteFile('${item.path}')">
                            DELETE
                        </button>
                    </div>
                </div>
            `;

        }).join('');

    } catch (err) {
        alert('Failed to load files: ' + err.message);
    }
}


async function deleteFolder(path) {
    if (!confirm(`Delete entire album:\n\n${path}\n\nThis cannot be undone.`)) return;

    const res = await fetch('/api/delete-folder', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path })
    });

    const data = await res.json();

    if (!res.ok) {
        alert(data.error || 'Failed to delete folder');
        return;
    }

    loadFiles(); // refresh list
}








async function deleteFile(path) {
    if (!confirm(`Delete ${path}?`)) return;

    const res = await fetch('/api/delete-file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path })
    });

    if (!res.ok) {
        const data = await res.json();
        alert(data.error || 'Delete failed');
        return;
    }

    loadFiles(); // refresh view
}




function renderDownloadHistory() {
    const el = document.getElementById('downloadHistory');
    if (!el) return;

    if (!downloadHistory.length) {
        el.innerHTML = `<div class="empty-state">NO DOWNLOAD HISTORY</div>`;
        return;
    }

    el.innerHTML = downloadHistory.map(d => `
        <div class="download-item completed">
            <div class="download-content">
                <div class="download-info">
                    <div class="download-title">${d.metadata?.title || 'Unknown'}</div>
                    <div class="download-artist">${d.metadata?.artist || ''}</div>
                    <span class="status-badge completed">completed</span>
                </div>
            </div>
        </div>
    `).join('');
}





/* ===============================
   TABS
================================ */

function switchTab(tab, element) {
    currentTab = tab;

    if (element) {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        element.classList.add('active');
    }

    document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
    document.getElementById(tab + 'Tab').classList.add('active');

    if (tab === 'active') {
        renderActiveDownloads();
    } else if (tab === 'history') {
        renderDownloadHistory();
    }
}



/* ===============================
   INIT
================================ */

window.addEventListener('load', initializeSSE);
