/**
 * Citation Triage — Client App
 * Loads all data once, filters/sorts in-memory, renders paginated table.
 */

(function () {
    'use strict';

    // ── State ────────────────────────────────────────────────
    let allRows = [];
    let filtered = [];
    let sortKey = 'gpt_relation_score';
    let sortDir = 'asc'; // lowest (worst) first
    let pageSize = 80;
    let renderedCount = 0;
    let selectedSet = new Set(); // indices into `filtered`
    let drawerRow = null;

    // ── DOM refs ─────────────────────────────────────────────
    const $ = (sel) => document.querySelector(sel);
    const $$ = (sel) => document.querySelectorAll(sel);

    const tbody = $('#tbody');
    const loadMoreWrap = $('#load-more-wrap');
    const emptyState = $('#empty-state');
    const loadingState = $('#loading-state');
    const filterCount = $('#filter-count');
    const bulkBar = $('#bulk-bar');
    const bulkCount = $('#bulk-count');

    const fFile = $('#f-file');
    const fReason = $('#f-reason');
    const fRel = $('#f-rel');
    const fFlags = $('#f-flags');
    const fReview = $('#f-review');
    const fSearch = $('#f-search');

    // ── Data Loading ─────────────────────────────────────────
    async function loadData() {
        loadingState.style.display = '';
        emptyState.style.display = 'none';
        tbody.innerHTML = '';
        try {
            const res = await fetch('/api/all');
            const data = await res.json();
            allRows = data.rows || [];
            populateFilterDropdowns();
            applyFilters();
            updateStats();
            loadingState.style.display = 'none';
        } catch (err) {
            loadingState.innerHTML = `<p style="color:var(--red)">Failed to load: ${err.message}</p>`;
        }
    }

    function updateStats() {
        $('#pill-total').textContent = allRows.length;
        $('#pill-flagged').textContent = allRows.filter(r => r.gpt_below_threshold || r.out_of_jurisdiction_flag || r.age_mismatch_flag || r.name_in_excerpt === false).length;
        $('#pill-reviewed').textContent = allRows.filter(r => r.review).length;
    }

    // ── Filter Dropdowns ────────────────────────────────────
    function populateFilterDropdowns() {
        const files = [...new Set(allRows.map(r => r.filename))].sort();
        const reasons = [...new Set(allRows.map(r => r.gpt_reason_code).filter(Boolean))].sort();

        fFile.innerHTML = '<option value="">All files (' + files.length + ')</option>' +
            files.map(f => `<option value="${esc(f)}">${esc(f.replace('.json', ''))}</option>`).join('');

        fReason.innerHTML = '<option value="">All reasons</option>' +
            (reasons.includes('missing_metadata') ? '<option value="!missing_metadata">Hide "Missing Metadata"</option>' : '') +
            reasons.map(r => `<option value="${esc(r)}">${esc(formatReason(r))}</option>`).join('');
    }

    // ── Filtering ───────────────────────────────────────────
    function applyFilters() {
        const file = fFile.value;
        const reason = fReason.value;
        const rel = fRel.value;
        const flags = fFlags.value;
        const review = fReview.value;
        const search = fSearch.value.toLowerCase().trim();

        filtered = allRows.filter(r => {
            if (file && r.filename !== file) return false;
            if (reason) {
                if (reason === '!missing_metadata') {
                    if (r.gpt_reason_code === 'missing_metadata') return false;
                } else if (r.gpt_reason_code !== reason) {
                    return false;
                }
            }
            if (!matchScore(r.gpt_relation_score, rel)) return false;
            if (!matchFlags(r, flags)) return false;
            if (!matchReview(r, review)) return false;
            if (search && !matchSearch(r, search)) return false;
            return true;
        });

        // Sort
        filtered.sort((a, b) => {
            let va = a[sortKey], vb = b[sortKey];
            // review_status virtual field
            if (sortKey === 'review_status') {
                va = a.review ? a.review.status : '';
                vb = b.review ? b.review.status : '';
            }
            if (va == null) va = sortDir === 'asc' ? Infinity : -Infinity;
            if (vb == null) vb = sortDir === 'asc' ? Infinity : -Infinity;
            if (typeof va === 'string') {
                const cmp = va.localeCompare(vb);
                return sortDir === 'asc' ? cmp : -cmp;
            }
            return sortDir === 'asc' ? va - vb : vb - va;
        });

        selectedSet.clear();
        updateBulkBar();
        renderTable();
        filterCount.textContent = `${filtered.length} of ${allRows.length}`;
    }

    function matchScore(val, filter) {
        if (!filter) return true;
        if (val == null) return filter === '';
        const v = parseFloat(val);
        if (filter === 'critical') return v < 0.3;
        if (filter === 'suspect') return v >= 0.3 && v < 0.6;
        if (filter === 'low') return v < 0.6;
        if (filter === 'medium') return v >= 0.6 && v <= 0.8;
        if (filter === 'high') return v > 0.8;
        return true;
    }

    function matchFlags(r, filter) {
        if (!filter) return true;
        const hasBelow = r.gpt_below_threshold;
        const hasJuris = r.out_of_jurisdiction_flag;
        const hasAge = r.age_mismatch_flag;
        const hasName = r.name_in_excerpt === false;
        const anyFlag = hasBelow || hasJuris || hasAge || hasName;
        if (filter === 'any_flag') return anyFlag;
        if (filter === 'no_flags') return !anyFlag;
        if (filter === 'below_threshold') return hasBelow;
        if (filter === 'out_of_jurisdiction') return hasJuris;
        if (filter === 'age_mismatch') return hasAge;
        if (filter === 'name_mismatch') return r.name_in_excerpt === false;
        return true;
    }

    function matchReview(r, filter) {
        if (!filter) return true;
        if (filter === 'unreviewed') return !r.review;
        return r.review && r.review.status === filter;
    }

    function matchSearch(r, s) {
        return (r.citation || '').toLowerCase().includes(s) ||
            (r.canlii_title || '').toLowerCase().includes(s) ||
            (r.filename || '').toLowerCase().includes(s) ||
            (r.paragraph || '').toLowerCase().includes(s) ||
            (r.gpt_relation_reasoning || '').toLowerCase().includes(s);
    }

    // ── Table Rendering ─────────────────────────────────────
    function renderTable() {
        tbody.innerHTML = '';
        renderedCount = 0;
        renderMoreRows();
        updateRowCounter();
    }

    function renderMoreRows() {
        const end = Math.min(renderedCount + pageSize, filtered.length);
        const frag = document.createDocumentFragment();

        for (let i = renderedCount; i < end; i++) {
            frag.appendChild(createRow(filtered[i], i));
        }
        tbody.appendChild(frag);
        renderedCount = end;

        loadMoreWrap.style.display = renderedCount < filtered.length ? '' : 'none';
        emptyState.style.display = filtered.length === 0 && loadingState.style.display === 'none' ? '' : 'none';
        updateRowCounter();
    }

    function updateRowCounter() {
        let counter = $('#row-counter');
        if (!counter) {
            counter = document.createElement('div');
            counter.id = 'row-counter';
            counter.className = 'row-counter';
            document.getElementById('table-wrap').appendChild(counter);
        }
        if (filtered.length === 0) {
            counter.style.display = 'none';
        } else {
            counter.style.display = '';
            counter.textContent = `Showing ${renderedCount} of ${filtered.length} rows` +
                (renderedCount < filtered.length ? ' — scroll down for more' : '');
        }
    }

    function createRow(r, idx) {
        const tr = document.createElement('tr');
        const reviewStatus = r.review ? r.review.status : '';
        if (reviewStatus) tr.classList.add('reviewed-' + reviewStatus);
        if (selectedSet.has(idx)) tr.classList.add('row-selected');

        tr.innerHTML = `
      <td class="col-check"><input type="checkbox" data-idx="${idx}" class="row-check" ${selectedSet.has(idx) ? 'checked' : ''}></td>
      <td class="col-file"><span class="cell-file" data-file="${esc(r.filename)}">${esc(r.filename.replace('.json', ''))}</span></td>
      <td class="col-citation"><span class="cell-citation" data-idx="${idx}">${esc(r.citation)}</span></td>
      <td class="col-page cell-page">${r.page || ''}</td>
      <td class="col-rel">${scoreBadge(r.gpt_relation_score)}</td>
      <td class="col-reason">${reasonTag(r.gpt_reason_code)}</td>
      <td class="col-flags">${flagPills(r)}</td>
      <td class="col-status">${statusBadge(reviewStatus)}</td>
      <td class="col-actions">${actionBtns(idx, reviewStatus)}</td>
    `;
        return tr;
    }

    function scoreBadge(val) {
        if (val == null || val === '') return '<span class="score-badge score-na">—</span>';
        const v = parseFloat(val);
        let cls = 'score-high';
        if (v < 0.3) cls = 'score-critical';
        else if (v < 0.6) cls = 'score-low';
        else if (v <= 0.8) cls = 'score-medium';
        return `<span class="score-badge ${cls}">${v.toFixed(2)}</span>`;
    }

    function reasonTag(code) {
        if (!code) return '';
        return `<span class="reason-tag reason-${esc(code)}">${esc(formatReason(code))}</span>`;
    }

    function formatReason(code) {
        return (code || '').replace(/_/g, ' ');
    }

    function flagPills(r) {
        let html = '';
        if (r.gpt_below_threshold) html += '<span class="flag-pill flag-below">BELOW</span>';
        if (r.out_of_jurisdiction_flag) html += '<span class="flag-pill flag-jurisdiction">JURIS</span>';
        if (r.age_mismatch_flag) html += '<span class="flag-pill flag-age">AGE</span>';
        if (r.name_in_excerpt === false) html += '<span class="flag-pill flag-name">NAME</span>';
        return html || '<span style="color:var(--text-3)">—</span>';
    }

    function statusBadge(status) {
        if (!status) return '<span class="status-badge status-unreviewed">—</span>';
        const labels = { fine: '✓ Fine', problematic: '✗ Problem', ignored: '⊘ Ignored' };
        return `<span class="status-badge status-${status}">${labels[status] || status}</span>`;
    }

    function actionBtns(idx, currentStatus) {
        const r = filtered[idx];
        const hasComment = r.review && r.review.reason;
        return `<div class="action-btns">
      <button class="action-btn act-fine ${currentStatus === 'fine' ? 'active-fine' : ''}" data-idx="${idx}" data-action="fine" title="Mark Fine">✓</button>
      <button class="action-btn act-problematic ${currentStatus === 'problematic' ? 'active-problematic' : ''}" data-idx="${idx}" data-action="problematic" title="Mark Problematic">✗</button>
      <button class="action-btn act-ignored ${currentStatus === 'ignored' ? 'active-ignored' : ''}" data-idx="${idx}" data-action="ignored" title="Mark Ignored">⊘</button>
      <button class="action-btn act-detail${hasComment ? ' has-comment' : ''}" data-idx="${idx}" data-action="detail" title="${hasComment ? 'Has comment — click to view' : 'View Details'}">${hasComment ? '💬' : '⋯'}</button>
    </div>`;
    }

    // ── Review Actions ──────────────────────────────────────
    async function reviewInstance(row, status, reason) {
        const r = reason !== undefined ? reason : (row.review ? row.review.reason || '' : '');
        try {
            const res = await fetch('/api/review', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    filename: row.filename,
                    citation: row.citation,
                    instance_id: row.instance_id,
                    status: status,
                    reason: r,
                }),
            });
            if (res.ok) {
                row.review = { status, reason: r, updated_at: new Date().toISOString() };
                updateStats();
                return true;
            }
        } catch (e) {
            console.error('Review error:', e);
        }
        return false;
    }

    // Save only the comment (without changing review status)
    async function saveComment(row, reason) {
        const status = row.review ? row.review.status : 'fine';
        return reviewInstance(row, status, reason);
    }

    async function bulkReview(status) {
        const items = [];
        for (const idx of selectedSet) {
            const r = filtered[idx];
            items.push({ filename: r.filename, citation: r.citation, instance_id: r.instance_id });
        }
        if (!items.length) return;
        try {
            const res = await fetch('/api/review/bulk', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ items, status, reason: '' }),
            });
            if (res.ok) {
                for (const idx of selectedSet) {
                    filtered[idx].review = { status, reason: '', updated_at: new Date().toISOString() };
                }
                selectedSet.clear();
                updateBulkBar();
                renderTable();
                updateStats();
            }
        } catch (e) {
            console.error('Bulk review error:', e);
        }
    }

    // ── Drawer ──────────────────────────────────────────────
    function openDrawer(row) {
        drawerRow = row;
        const overlay = $('#drawer-overlay');
        const drawer = $('#drawer');
        const body = $('#drawer-body');
        const footer = $('#drawer-footer');
        const title = $('#drawer-title');

        title.textContent = row.citation;

        body.innerHTML = `
      <div class="detail-section">
        <h3>Document</h3>
        <div class="detail-grid">
          <span class="label">File</span><span class="value">${esc(row.filename)}</span>
          <span class="label">Court No</span><span class="value">${esc(row.court_no)}</span>
          <span class="label">Style</span><span class="value">${esc(row.style_of_cause)}</span>
          <span class="label">Nature</span><span class="value">${esc(row.nature_of_proceedings)}</span>
        </div>
      </div>

      <div class="detail-section">
        <h3>Citation</h3>
        <div class="detail-grid">
          <span class="label">Citation</span><span class="value" style="font-weight:600;color:var(--text-0)">${esc(row.citation)}</span>
          <span class="label">Case Name</span><span class="value">${esc(row.canlii_title)}</span>
          <span class="label">CanLII</span><span class="value">${row.canlii_url ? `<a class="detail-link" href="${esc(row.canlii_url)}" target="_blank">${esc(row.canlii_url)}</a>` : '—'}</span>
          <span class="label">Name Match</span><span class="value">${row.name_in_excerpt === true ? '✓ Match' : (row.name_in_excerpt === false ? '<span style="color:var(--text-0);font-weight:600">✗ Mismatch</span>' : '—')}</span>
          <span class="label">Page</span><span class="value">${row.page || '—'}</span>
        </div>
      </div>

      <div class="detail-section">
        <h3>GPT Analysis</h3>
        <div class="detail-grid">
          <span class="label">Relation</span><span class="value">${scoreBadge(row.gpt_relation_score)}</span>
          <span class="label">Reason</span><span class="value">${reasonTag(row.gpt_reason_code)}</span>
          <span class="label">Pass</span><span class="value">${row.gpt_pass || '—'}</span>
          <span class="label">Flags</span><span class="value">${flagPills(row) || '—'}</span>
          <span class="label">Keyword Overlap</span><span class="value">${row.keyword_overlap != null ? parseFloat(row.keyword_overlap).toFixed(3) : '—'}</span>
        </div>
      </div>



      <div class="detail-section">
        <h3>GPT Reasoning</h3>
        <div class="detail-reasoning">${esc(row.gpt_relation_reasoning) || '<em>No reasoning provided</em>'}</div>
      </div>

      <div class="detail-section">
        <h3>Citation Paragraph</h3>
        <div class="detail-paragraph">${esc(row.paragraph)}</div>
      </div>

      ${row.review ? `
      <div class="detail-section">
        <h3>Current Review</h3>
        <div class="detail-grid">
          <span class="label">Status</span><span class="value">${statusBadge(row.review.status)}</span>
          ${row.review.reason ? `<span class="label">Comment</span><span class="value">${esc(row.review.reason)}</span>` : ''}
        </div>
      </div>` : ''}
    `;

        const reviewStatus = row.review ? row.review.status : '';
        const existingComment = row.review ? (row.review.reason || '') : '';
        footer.innerHTML = `
      <div class="drawer-footer-inner">
        <div class="drawer-comment">
          <label for="drawer-comment-input">Comment / Note</label>
          <textarea id="drawer-comment-input" placeholder="Add a comment about this citation…" rows="2">${esc(existingComment)}</textarea>
          <button class="btn btn-sm btn-ghost" id="drawer-save-comment" title="Save comment without changing status">Save Comment</button>
        </div>
        <div class="drawer-review-btns">
          <button class="btn btn-fine ${reviewStatus === 'fine' ? 'active-fine' : ''}" data-drawer-action="fine">✓ Fine</button>
          <button class="btn btn-problematic ${reviewStatus === 'problematic' ? 'active-problematic' : ''}" data-drawer-action="problematic">✗ Problematic</button>
          <button class="btn btn-ignored ${reviewStatus === 'ignored' ? 'active-ignored' : ''}" data-drawer-action="ignored">⊘ Ignore</button>
        </div>
      </div>
    `;

        // Wire up "save comment" button
        const saveCommentBtn = $('#drawer-save-comment');
        if (saveCommentBtn) {
            saveCommentBtn.addEventListener('click', async () => {
                const comment = $('#drawer-comment-input').value.trim();
                if (!comment && (!row.review || !row.review.reason)) return;
                await saveComment(row, comment);
                openDrawer(row); // refresh
                renderTable();
            });
        }

        overlay.classList.add('open');
        drawer.classList.add('open');
    }

    function closeDrawer() {
        $('#drawer-overlay').classList.remove('open');
        $('#drawer').classList.remove('open');
        drawerRow = null;
    }

    // ── Selection / Bulk ────────────────────────────────────
    function updateBulkBar() {
        const n = selectedSet.size;
        bulkBar.style.display = n > 0 ? '' : 'none';
        bulkCount.textContent = `${n} selected`;
        $('#th-check').checked = n > 0 && n === Math.min(renderedCount, filtered.length);
    }

    // ── Sorting ─────────────────────────────────────────────
    function setSort(key) {
        if (sortKey === key) {
            sortDir = sortDir === 'asc' ? 'desc' : 'asc';
        } else {
            sortKey = key;
            sortDir = 'asc';
        }
        $$('.triage-table th').forEach(th => th.classList.remove('sorted-asc', 'sorted-desc'));
        const th = $(`.triage-table th[data-sort="${key}"]`);
        if (th) th.classList.add(sortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
        applyFilters();
    }

    // ── CSV Export ──────────────────────────────────────────
    function exportCSV() {
        const headers = ['filename', 'citation', 'canlii_title', 'page', 'gpt_relation_score',
            'gpt_reason_code', 'gpt_relation_reasoning', 'gpt_below_threshold', 'out_of_jurisdiction_flag',
            'age_mismatch_flag', 'name_mismatch_flag', 'review_status', 'review_reason'];
        const lines = [headers.join(',')];
        for (const r of filtered) {
            lines.push(headers.map(h => {
                let v = '';
                if (h === 'review_status') v = r.review ? r.review.status : '';
                else if (h === 'review_reason') v = r.review ? r.review.reason : '';
                else if (h === 'name_mismatch_flag') v = r.name_in_excerpt === false;
                else v = r[h];
                if (v == null) v = '';
                return '"' + String(v).replace(/"/g, '""') + '"';
            }).join(','));
        }
        const blob = new Blob([lines.join('\n')], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'citation_triage_export.csv';
        a.click();
        URL.revokeObjectURL(url);
    }

    // ── Utility ─────────────────────────────────────────────
    function esc(s) {
        if (s == null) return '';
        return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    // ── Event Wiring ────────────────────────────────────────
    // Filters
    [fFile, fReason, fRel, fFlags, fReview].forEach(el => {
        el.addEventListener('change', () => applyFilters());
    });

    let searchTimeout;
    fSearch.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(applyFilters, 200);
    });

    $('#btn-clear-filters').addEventListener('click', () => {
        fFile.value = '';
        fReason.value = '';
        fRel.value = '';
        fFlags.value = '';
        fReview.value = '';
        fSearch.value = '';
        applyFilters();
    });

    // Sorting
    $$('.triage-table th.sortable').forEach(th => {
        th.addEventListener('click', () => setSort(th.dataset.sort));
    });

    // Load more (button fallback)
    $('#btn-load-more').addEventListener('click', () => renderMoreRows());

    // Infinite scroll: auto-load more rows when scrolling near the bottom
    const tableWrap = $('#table-wrap');
    tableWrap.addEventListener('scroll', () => {
        if (renderedCount >= filtered.length) return;
        const { scrollTop, scrollHeight, clientHeight } = tableWrap;
        if (scrollTop + clientHeight >= scrollHeight - 200) {
            renderMoreRows();
        }
    });

    // Header checkbox
    $('#th-check').addEventListener('change', (e) => {
        const checked = e.target.checked;
        if (checked) {
            for (let i = 0; i < renderedCount; i++) selectedSet.add(i);
        } else {
            selectedSet.clear();
        }
        $$('.row-check').forEach(cb => { cb.checked = checked; });
        $$('.triage-table tbody tr').forEach(tr => {
            tr.classList.toggle('row-selected', checked);
        });
        updateBulkBar();
    });

    // Select-all visible checkbox in bulk bar
    $('#bulk-select-all').addEventListener('change', (e) => {
        const checked = e.target.checked;
        if (checked) {
            for (let i = 0; i < filtered.length; i++) selectedSet.add(i);
        } else {
            selectedSet.clear();
        }
        $$('.row-check').forEach(cb => { cb.checked = checked; });
        $$('.triage-table tbody tr').forEach(tr => {
            tr.classList.toggle('row-selected', checked);
        });
        updateBulkBar();
    });

    // Bulk actions
    $('#bulk-fine').addEventListener('click', () => bulkReview('fine'));
    $('#bulk-problematic').addEventListener('click', () => bulkReview('problematic'));
    $('#bulk-ignored').addEventListener('click', () => bulkReview('ignored'));

    // Table click delegation
    tbody.addEventListener('click', async (e) => {
        const target = e.target.closest('[data-action], .cell-file, .cell-citation, .row-check');
        if (!target) return;

        // Checkbox
        if (target.classList.contains('row-check')) {
            const idx = parseInt(target.dataset.idx);
            if (target.checked) selectedSet.add(idx); else selectedSet.delete(idx);
            target.closest('tr').classList.toggle('row-selected', target.checked);
            updateBulkBar();
            return;
        }

        // File click → filter by file
        if (target.classList.contains('cell-file')) {
            fFile.value = target.dataset.file;
            applyFilters();
            return;
        }

        // Citation click → open drawer
        if (target.classList.contains('cell-citation')) {
            const idx = parseInt(target.dataset.idx);
            openDrawer(filtered[idx]);
            return;
        }

        // Action buttons
        const action = target.dataset.action;
        const idx = parseInt(target.dataset.idx);
        if (action === 'detail') {
            openDrawer(filtered[idx]);
            return;
        }

        // Review inline
        if (['fine', 'problematic', 'ignored'].includes(action)) {
            const row = filtered[idx];
            // Toggle off if same status
            if (row.review && row.review.status === action) {
                // Un-review
                try {
                    await fetch('/api/review', {
                        method: 'DELETE',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ filename: row.filename, citation: row.citation, instance_id: row.instance_id }),
                    });
                    row.review = null;
                } catch (e) { console.error(e); }
            } else {
                await reviewInstance(row, action);
            }
            // Re-render just this row
            const tr = target.closest('tr');
            const newTr = createRow(row, idx);
            tr.replaceWith(newTr);
            updateStats();
        }
    });

    // Drawer events
    $('#drawer-close').addEventListener('click', closeDrawer);
    $('#drawer-overlay').addEventListener('click', closeDrawer);

    $('#drawer-footer').addEventListener('click', async (e) => {
        const btn = e.target.closest('[data-drawer-action]');
        if (!btn || !drawerRow) return;
        const action = btn.dataset.drawerAction;
        const commentInput = $('#drawer-comment-input');
        const comment = commentInput ? commentInput.value.trim() : '';
        if (drawerRow.review && drawerRow.review.status === action) {
            try {
                await fetch('/api/review', {
                    method: 'DELETE',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ filename: drawerRow.filename, citation: drawerRow.citation, instance_id: drawerRow.instance_id }),
                });
                drawerRow.review = null;
            } catch (e) { console.error(e); }
        } else {
            await reviewInstance(drawerRow, action, comment);
        }
        openDrawer(drawerRow); // refresh drawer content
        renderTable(); // refresh table
        updateStats();
    });

    // Keyboard: Escape to close drawer
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeDrawer();
    });

    // Refresh button
    $('#btn-refresh').addEventListener('click', () => loadData());

    // CSV export
    $('#btn-export-csv').addEventListener('click', () => exportCSV());

    // ── Init ────────────────────────────────────────────────
    // Default sort indicator
    const defaultTh = $(`.triage-table th[data-sort="${sortKey}"]`);
    if (defaultTh) defaultTh.classList.add('sorted-asc');

    loadData();
})();
