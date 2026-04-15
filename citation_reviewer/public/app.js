/**
 * Citation Reviewer Frontend Application
 * Handles all UI interactions, API calls, and state management
 */

class CitationReviewerApp {
  constructor() {
    this.currentView = 'dashboard';
    this.currentFile = null;
    this.currentCitation = null;
    this.stats = {};
    this.files = [];
    this.allCitations = [];
    this.selectedStatus = null;

    this.init();
  }

  async init() {
    this.bindEvents();
    await this.loadInitialData();
    this.checkUrlHash();
  }

  bindEvents() {
    // Navigation
    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const view = item.dataset.view;
        this.showView(view);
      });
    });

    // Mobile menu toggle
    document.getElementById('menu-toggle').addEventListener('click', () => {
      document.getElementById('sidebar').classList.toggle('open');
    });

    // Refresh button
    document.getElementById('refresh-btn').addEventListener('click', async () => {
      const btn = document.getElementById('refresh-btn');
      btn.classList.add('loading');
      await this.loadInitialData();
      btn.classList.remove('loading');
    });

    // Search
    document.getElementById('search-input').addEventListener('input', (e) => {
      this.handleSearch(e.target.value);
    });

    // Filter dropdowns - All Citations page
    document.getElementById('filter-status').addEventListener('change', () => this.applyAllFilters());
    document.getElementById('filter-relation-score').addEventListener('change', () => this.applyAllFilters());
    document.getElementById('filter-pinpoint-score').addEventListener('change', () => this.applyAllFilters());
    document.getElementById('filter-flags').addEventListener('change', () => this.applyAllFilters());
    document.getElementById('clear-filters-btn').addEventListener('click', () => this.clearFilters());

    document.getElementById('reviewed-filter-status').addEventListener('change', (e) => {
      this.filterReviewedCitations(e.target.value);
    });


    // Modal status buttons
    document.querySelectorAll('.status-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.status-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.selectedStatus = btn.dataset.status;

        const reasonGroup = document.getElementById('reason-group');
        if (this.selectedStatus === 'fraud' || this.selectedStatus === 'ignore') {
          reasonGroup.style.display = 'block';
        } else {
          reasonGroup.style.display = 'none';
        }

        this.updateSubmitButton();
      });
    });

    // Submit review
    document.getElementById('submit-review').addEventListener('click', () => {
      this.submitReview();
    });

    // Close sidebar when clicking outside on mobile
    document.querySelector('.main-content').addEventListener('click', () => {
      if (window.innerWidth <= 768) {
        document.getElementById('sidebar').classList.remove('open');
      }
    });
  }

  async loadInitialData() {
    try {
      const [stats, files] = await Promise.all([
        this.fetchStats(),
        this.fetchFiles()
      ]);

      this.stats = stats;
      this.files = files;

      this.renderStats();
      this.renderFiles();
      this.renderDashboard();

    } catch (error) {
      console.error('Error loading initial data:', error);
      this.showError('Failed to load data. Please refresh the page.');
    }
  }

  async fetchStats() {
    const res = await fetch('/api/stats');
    return res.json();
  }

  async fetchFiles() {
    const res = await fetch('/api/files');
    return res.json();
  }

  async fetchFile(filename) {
    const res = await fetch(`/api/files/${encodeURIComponent(filename)}`);
    return res.json();
  }

  async fetchCitations(options = {}) {
    const params = new URLSearchParams();
    if (options.status) params.append('status', options.status);
    if (options.flagged) params.append('flagged', 'true');

    const res = await fetch(`/api/citations?${params}`);
    return res.json();
  }

  renderStats() {
    document.getElementById('stat-files').textContent = this.stats.total_files || 0;
    document.getElementById('stat-reviewed').textContent = this.stats.reviews?.total || 0;
    document.getElementById('dashboard-files').textContent = this.stats.total_files || 0;
    document.getElementById('dashboard-citations').textContent = this.stats.total_citation_instances || 0;
    document.getElementById('dashboard-ok').textContent = this.stats.reviews?.ok || 0;
    document.getElementById('dashboard-fraud').textContent = this.stats.reviews?.fraud || 0;
    document.getElementById('dashboard-ignore').textContent = this.stats.reviews?.ignore || 0;

    const totalCitations = this.stats.total_citation_instances || 1;
    const reviewed = this.stats.reviews?.total || 0;
    const progress = Math.round((reviewed / totalCitations) * 100);
    document.getElementById('dashboard-progress').textContent = `${progress}%`;
  }

  async renderDashboard() {
    // Render recent flagged citations
    const flaggedContainer = document.getElementById('recent-flagged');
    try {
      const flagged = await this.fetchCitations({ flagged: true });
      const recentFlagged = flagged.filter(c => !c.review).slice(0, 5);

      document.getElementById('flagged-count').textContent = flagged.filter(c => !c.review).length;

      if (recentFlagged.length === 0) {
        flaggedContainer.innerHTML = this.renderEmptyState('No flagged citations pending review');
      } else {
        flaggedContainer.innerHTML = recentFlagged.map(c => `
          <div class="recent-item" onclick="window.app.openCitationDetail(${JSON.stringify(c).replace(/"/g, '&quot;')})">
            <div class="recent-item-icon flag">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z"/>
                <line x1="4" y1="22" x2="4" y2="15"/>
              </svg>
            </div>
            <div class="recent-item-content">
              <div class="recent-item-title">${this.escapeHtml(c.citation)}</div>
              <div class="recent-item-meta">${this.escapeHtml(c.filename)} • ${c.gpt_reason_code || 'Flagged'}</div>
            </div>
          </div>
        `).join('');
      }
    } catch (e) {
      flaggedContainer.innerHTML = '<div class="loading">Error loading flagged citations</div>';
    }

    // Render recent reviews
    const reviewsContainer = document.getElementById('recent-reviews');
    try {
      const allCitations = await this.fetchCitations();
      const reviewed = allCitations.filter(c => c.review).slice(0, 5);

      if (reviewed.length === 0) {
        reviewsContainer.innerHTML = this.renderEmptyState('No reviews yet');
      } else {
        reviewsContainer.innerHTML = reviewed.map(c => `
          <div class="recent-item" onclick="window.app.openCitationDetail(${JSON.stringify(c).replace(/"/g, '&quot;')})">
            <div class="recent-item-icon ${c.review.status}">
              ${this.getStatusIcon(c.review.status)}
            </div>
            <div class="recent-item-content">
              <div class="recent-item-title">${this.escapeHtml(c.citation)}</div>
              <div class="recent-item-meta">${this.getStatusLabel(c.review.status)} • ${this.escapeHtml(c.filename)}</div>
            </div>
          </div>
        `).join('');
      }
    } catch (e) {
      reviewsContainer.innerHTML = '<div class="loading">Error loading reviews</div>';
    }
  }

  renderFiles() {
    const tbody = document.getElementById('files-tbody');

    if (this.files.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="loading">No files found in database</td></tr>';
      return;
    }

    tbody.innerHTML = this.files.map(file => {
      const totalCitations = file.total_citations || 0;
      const reviewed = file.reviewed_count || 0;
      let statusClass = 'pending';
      let statusText = 'Pending';

      if (reviewed === totalCitations && totalCitations > 0) {
        statusClass = 'complete';
        statusText = 'Complete';
      } else if (reviewed > 0) {
        statusClass = 'partial';
        statusText = 'In Progress';
      }

      return `
        <tr>
          <td>
            <span class="file-link" onclick="window.app.openFile('${this.escapeHtml(file.filename)}')">${this.escapeHtml(file.filename)}</span>
          </td>
          <td>${this.escapeHtml(file.court_no || '-')}</td>
          <td class="truncate" title="${this.escapeHtml(file.style_of_cause || '')}">${this.escapeHtml(file.style_of_cause || '-')}</td>
          <td class="truncate">${this.escapeHtml(file.nature_desc || '-')}</td>
          <td>${file.unique_citations || 0} / ${file.total_citations || 0}</td>
          <td>${reviewed} / ${totalCitations}</td>
          <td><span class="status-badge ${statusClass}">${statusText}</span></td>
          <td>
            <button class="btn btn-secondary btn-sm" onclick="window.app.openFile('${this.escapeHtml(file.filename)}')">
              View
            </button>
          </td>
        </tr>
      `;
    }).join('');
  }

  async openFile(filename) {
    this.currentFile = filename;
    this.showView('file-detail');

    const headerEl = document.getElementById('file-detail-header');
    const infoEl = document.getElementById('file-info');
    const summaryEl = document.getElementById('file-summary');
    const citationsEl = document.getElementById('file-citations');

    citationsEl.innerHTML = '<div class="loading">Loading citations...</div>';

    try {
      const file = await this.fetchFile(filename);

      infoEl.innerHTML = `
        <h2>${this.escapeHtml(file.style_of_cause || file.filename)}</h2>
        <p><strong>File:</strong> ${this.escapeHtml(file.filename)}</p>
        <p><strong>Nature:</strong> ${this.escapeHtml(file.english_nature_desc || 'N/A')}</p>
        <p><strong>Track:</strong> ${this.escapeHtml(file.english_track_name || 'N/A')}</p>
        <span class="court-no">${this.escapeHtml(file.court_no || 'No Court No.')}</span>
      `;

      summaryEl.innerHTML = `
        <h3>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <circle cx="12" cy="12" r="10"/>
            <line x1="12" y1="16" x2="12" y2="12"/>
            <line x1="12" y1="8" x2="12.01" y2="8"/>
          </svg>
          AI Summary
        </h3>
        <p>${this.escapeHtml(file.file_ai_summary || 'No summary available.')}</p>
      `;

      if (!file.results || file.results.length === 0) {
        citationsEl.innerHTML = this.renderEmptyState('No citations found in this file');
        return;
      }

      citationsEl.innerHTML = file.results.map(citation =>
        citation.instances.map(instance =>
          this.renderCitationCard({
            filename: file.filename,
            citation: citation.citation,
            citation_normalized: citation.citation_normalized,
            instance_id: instance.instance_id,
            page: instance.page,
            paragraph: instance.paragraph,
            pinpoints: instance.pinpoints,
            gpt_relation_score: instance.gpt_relation_score,
            gpt_pinpoint_score: instance.gpt_pinpoint_score,
            gpt_relation_reasoning: instance.gpt_relation_reasoning,
            gpt_reason_code: instance.gpt_reason_code,
            gpt_below_threshold: instance.gpt_below_threshold,
            out_of_jurisdiction_flag: instance.out_of_jurisdiction_flag,
            age_mismatch_flag: instance.age_mismatch_flag,
            self_citation_docket_flag: instance.self_citation_docket_flag,
            canlii_data: citation.canlii_api_response,
            hf_result: citation.hf_result,
            review: instance.review
          })
        ).join('')
      ).join('');

    } catch (error) {
      console.error('Error loading file:', error);
      citationsEl.innerHTML = '<div class="loading">Error loading file data</div>';
    }
  }

  async loadAllCitations(filters = {}) {
    const container = document.getElementById('all-citations');
    const resultsCountEl = document.getElementById('filter-results-count');
    container.innerHTML = '<div class="loading">Loading citations...</div>';
    resultsCountEl.innerHTML = '';

    try {
      const citations = await this.fetchCitations();
      this.allCitations = citations;

      let filtered = this.applyFiltersToData(citations, filters);

      // Update results count
      const totalCount = citations.length;
      const filteredCount = filtered.length;
      const hasFilters = Object.values(filters).some(v => v);

      if (hasFilters) {
        resultsCountEl.innerHTML = `Showing <strong>${filteredCount}</strong> of <strong>${totalCount}</strong> citations`;
      } else {
        resultsCountEl.innerHTML = `<strong>${totalCount}</strong> total citations`;
      }

      if (filtered.length === 0) {
        container.innerHTML = this.renderEmptyState('No citations match the current filters');
        return;
      }

      container.innerHTML = filtered.map(c => this.renderCitationCard(c, true)).join('');

    } catch (error) {
      console.error('Error loading citations:', error);
      container.innerHTML = '<div class="loading">Error loading citations</div>';
    }
  }

  applyFiltersToData(citations, filters) {
    let filtered = citations;

    // Filter by review status
    if (filters.status === 'unreviewed') {
      filtered = filtered.filter(c => !c.review);
    } else if (filters.status) {
      filtered = filtered.filter(c => c.review?.status === filters.status);
    }

    // Filter by relation score
    if (filters.relationScore) {
      filtered = filtered.filter(c => {
        const score = c.gpt_relation_score;
        if (score === null || score === undefined) return false;
        if (filters.relationScore === 'low') return score < 0.4;
        if (filters.relationScore === 'medium') return score >= 0.4 && score <= 0.7;
        if (filters.relationScore === 'high') return score > 0.7;
        return true;
      });
    }

    // Filter by pinpoint score
    if (filters.pinpointScore) {
      filtered = filtered.filter(c => {
        const score = c.gpt_pinpoint_score;
        if (score === null || score === undefined) return false;
        if (filters.pinpointScore === 'low') return score < 0.4;
        if (filters.pinpointScore === 'medium') return score >= 0.4 && score <= 0.7;
        if (filters.pinpointScore === 'high') return score > 0.7;
        return true;
      });
    }

    // Filter by flags
    if (filters.flags) {
      filtered = filtered.filter(c => {
        const hasAnyFlag = c.gpt_below_threshold ||
          c.out_of_jurisdiction_flag ||
          c.age_mismatch_flag ||
          c.self_citation_docket_flag;

        if (filters.flags === 'below_threshold') return c.gpt_below_threshold;
        if (filters.flags === 'out_of_jurisdiction') return c.out_of_jurisdiction_flag;
        if (filters.flags === 'age_mismatch') return c.age_mismatch_flag;
        if (filters.flags === 'self_citation') return c.self_citation_docket_flag;
        if (filters.flags === 'any_flag') return hasAnyFlag;
        if (filters.flags === 'no_flags') return !hasAnyFlag;
        return true;
      });
    }

    return filtered;
  }

  applyAllFilters() {
    const filters = {
      status: document.getElementById('filter-status').value,
      relationScore: document.getElementById('filter-relation-score').value,
      pinpointScore: document.getElementById('filter-pinpoint-score').value,
      flags: document.getElementById('filter-flags').value
    };
    this.loadAllCitations(filters);
  }

  clearFilters() {
    document.getElementById('filter-status').value = '';
    document.getElementById('filter-relation-score').value = '';
    document.getElementById('filter-pinpoint-score').value = '';
    document.getElementById('filter-flags').value = '';
    this.loadAllCitations({});
  }


  async loadFlaggedCitations() {
    const container = document.getElementById('flagged-citations');
    container.innerHTML = '<div class="loading">Loading flagged citations...</div>';

    try {
      const citations = await this.fetchCitations({ flagged: true });
      const unreviewed = citations.filter(c => !c.review);

      if (unreviewed.length === 0) {
        container.innerHTML = this.renderEmptyState('No flagged citations pending review');
        return;
      }

      container.innerHTML = unreviewed.map(c => this.renderCitationCard(c, true)).join('');

    } catch (error) {
      console.error('Error loading flagged citations:', error);
      container.innerHTML = '<div class="loading">Error loading flagged citations</div>';
    }
  }

  async loadReviewedCitations(filterStatus = '') {
    const container = document.getElementById('reviewed-citations');
    container.innerHTML = '<div class="loading">Loading reviewed citations...</div>';

    try {
      const citations = await this.fetchCitations();
      let reviewed = citations.filter(c => c.review);

      if (filterStatus) {
        reviewed = reviewed.filter(c => c.review.status === filterStatus);
      }

      if (reviewed.length === 0) {
        container.innerHTML = this.renderEmptyState('No reviewed citations found');
        return;
      }

      container.innerHTML = reviewed.map(c => this.renderCitationCard(c, true)).join('');

    } catch (error) {
      console.error('Error loading reviewed citations:', error);
      container.innerHTML = '<div class="loading">Error loading reviewed citations</div>';
    }
  }

  renderCitationCard(citation, showFileInfo = false) {
    const relScore = citation.gpt_relation_score;
    const pinScore = citation.gpt_pinpoint_score;
    const isFlagged = citation.gpt_below_threshold ||
      citation.out_of_jurisdiction_flag ||
      citation.age_mismatch_flag ||
      citation.self_citation_docket_flag ||
      (relScore !== null && relScore !== undefined && relScore < 0.6) ||
      (pinScore !== null && pinScore !== undefined && pinScore < 0.6);

    const reviewClass = citation.review ? `reviewed-${citation.review.status}` : '';
    const flaggedClass = isFlagged && !citation.review ? 'flagged' : '';

    const scoreClass = (score) => {
      if (score === null || score === undefined) return '';
      if (score >= 0.7) return 'high';
      if (score >= 0.4) return 'medium';
      return 'low';
    };

    const formatScore = (score) => {
      if (score === null || score === undefined) return 'Not analyzed';
      if (typeof score !== 'number') return 'Not analyzed';
      return Math.round(score * 100) + '%';
    };



    const flags = [];
    if (citation.gpt_below_threshold) flags.push({ class: 'low-score', text: 'Low AI Score' });
    if (citation.out_of_jurisdiction_flag) flags.push({ class: 'jurisdiction', text: 'Out of Jurisdiction' });
    if (citation.age_mismatch_flag) flags.push({ class: 'age', text: 'Age Mismatch' });
    if (citation.self_citation_docket_flag) flags.push({ class: 'self', text: 'Self Citation' });

    const citationData = JSON.stringify(citation).replace(/"/g, '&quot;');

    return `
      <div class="citation-card ${flaggedClass} ${reviewClass}">
        <div class="citation-header">
          <div class="citation-title">
            <strong>${this.escapeHtml(citation.citation)}</strong>
            <div class="citation-flags">
              ${flags.map(f => `<span class="flag-badge ${f.class}">${f.text}</span>`).join('')}
            </div>
          </div>
          <div class="citation-actions">
            <button class="btn btn-secondary btn-sm" onclick="window.app.openCitationDetail(${citationData})">
              Details
            </button>
            <button class="btn btn-primary btn-sm" onclick="window.app.openReviewModal(${citationData})">
              ${citation.review ? 'Update Review' : 'Review'}
            </button>
          </div>
        </div>
        <div class="citation-body">
          ${showFileInfo ? `
            <div class="citation-meta">
              <div class="meta-item">
                <span class="meta-label">File</span>
                <span class="meta-value file-link" onclick="window.app.openFile('${this.escapeHtml(citation.filename)}')">${this.escapeHtml(citation.filename)}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">Court No.</span>
                <span class="meta-value">${this.escapeHtml(citation.court_no || 'N/A')}</span>
              </div>
            </div>
          ` : ''}
          <div class="citation-meta">
            <div class="meta-item">
              <span class="meta-label">Page</span>
              <span class="meta-value">${citation.page || 'N/A'}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Pinpoints</span>
              <span class="meta-value">${(citation.pinpoints || []).join(', ') || 'None'}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Relation Score</span>
              <span class="meta-value score ${scoreClass(citation.gpt_relation_score)}">${formatScore(citation.gpt_relation_score)}</span>
            </div>
            <div class="meta-item">
              <span class="meta-label">Pinpoint Score</span>
              <span class="meta-value score ${scoreClass(citation.gpt_pinpoint_score)}">${formatScore(citation.gpt_pinpoint_score)}</span>
            </div>

          </div>
          ${citation.gpt_relation_reasoning ? `
            <div class="meta-item" style="margin-top: 12px;">
              <span class="meta-label">AI Reasoning</span>
              <span class="meta-value">${this.escapeHtml(citation.gpt_relation_reasoning)}</span>
            </div>
          ` : ''}
          ${citation.paragraph ? `
            <div class="citation-paragraph">
              <span class="para-label">Paragraph Context</span>
              <div class="para-text">${this.escapeHtml(citation.paragraph.substring(0, 500))}${citation.paragraph.length > 500 ? '...' : ''}</div>
              ${citation.paragraph.length > 500 ? `<button class="expand-btn" onclick="this.previousElementSibling.classList.toggle('expanded'); this.textContent = this.textContent === 'Show more' ? 'Show less' : 'Show more'">Show more</button>` : ''}
            </div>
          ` : ''}
          ${citation.review ? `
            <div class="citation-review-status">
              <span class="review-badge ${citation.review.status}">
                ${this.getStatusIcon(citation.review.status)}
                ${this.getStatusLabel(citation.review.status)}
              </span>
              ${citation.review.reason ? `<span class="review-reason">"${this.escapeHtml(citation.review.reason)}"</span>` : ''}
            </div>
          ` : ''}
        </div>
      </div>
    `;
  }

  openReviewModal(citation) {
    this.currentCitation = citation;

    const modal = document.getElementById('review-modal');
    const preview = document.getElementById('modal-citation-preview');

    preview.innerHTML = `
      <div class="preview-citation">${this.escapeHtml(citation.citation)}</div>
      <div class="preview-file">From: ${this.escapeHtml(citation.filename)} • Page ${citation.page || 'N/A'}</div>
    `;

    // Reset modal state
    document.querySelectorAll('.status-btn').forEach(btn => btn.classList.remove('active'));
    document.getElementById('review-reason').value = '';
    document.getElementById('reason-group').style.display = 'none';
    this.selectedStatus = null;
    this.updateSubmitButton();

    // Pre-fill if already reviewed
    if (citation.review) {
      const btn = document.querySelector(`.status-btn[data-status="${citation.review.status}"]`);
      if (btn) {
        btn.classList.add('active');
        this.selectedStatus = citation.review.status;
        if (citation.review.reason) {
          document.getElementById('review-reason').value = citation.review.reason;
          document.getElementById('reason-group').style.display = 'block';
        }
        this.updateSubmitButton();
      }
    }

    modal.classList.add('active');
  }

  closeModal() {
    document.getElementById('review-modal').classList.remove('active');
    this.currentCitation = null;
    this.selectedStatus = null;
  }

  openCitationDetail(citation) {
    this.currentCitation = citation;

    const modal = document.getElementById('citation-detail-modal');
    const title = document.getElementById('detail-modal-title');
    const content = document.getElementById('citation-detail-content');
    const reviewBtn = document.getElementById('detail-review-btn');

    title.textContent = citation.citation;

    const canlii = citation.canlii_data || {};
    const hf = citation.hf_result || {};

    const formatScore = (score) => {
      if (score === null || score === undefined) return 'Not analyzed';
      if (typeof score !== 'number') return 'Not analyzed';
      return Math.round(score * 100) + '%';
    };



    content.innerHTML = `
      <div class="detail-section">
        <h4>Source Information</h4>
        <div class="detail-grid">
          <div class="detail-item">
            <span class="label">Filename</span>
            <span class="value">${this.escapeHtml(citation.filename)}</span>
          </div>
          <div class="detail-item">
            <span class="label">Page</span>
            <span class="value">${citation.page || 'N/A'}</span>
          </div>
          <div class="detail-item">
            <span class="label">Court No.</span>
            <span class="value">${this.escapeHtml(citation.court_no || 'N/A')}</span>
          </div>
          <div class="detail-item">
            <span class="label">Pinpoints</span>
            <span class="value">${(citation.pinpoints || []).join(', ') || 'None'}</span>
          </div>
        </div>
      </div>
      
      ${canlii.title ? `
        <div class="detail-section">
          <h4>CanLII Data</h4>
          <div class="detail-grid">
            <div class="detail-item">
              <span class="label">Case Title</span>
              <span class="value">${this.escapeHtml(canlii.title)}</span>
            </div>
            <div class="detail-item">
              <span class="label">Decision Date</span>
              <span class="value">${canlii.decisionDate || 'N/A'}</span>
            </div>
            <div class="detail-item">
              <span class="label">Docket Number</span>
              <span class="value">${canlii.docketNumber || 'N/A'}</span>
            </div>
            <div class="detail-item">
              <span class="label">Database</span>
              <span class="value">${canlii.databaseId || 'N/A'}</span>
            </div>
          </div>
          ${canlii.keywords ? `
            <div class="detail-item" style="margin-top: 12px;">
              <span class="label">Keywords</span>
              <span class="value">${this.escapeHtml(canlii.keywords)}</span>
            </div>
          ` : ''}
          ${canlii.url ? `
            <div class="detail-item" style="margin-top: 12px;">
              <span class="label">URL</span>
              <span class="value"><a href="${canlii.url}" target="_blank" style="color: var(--primary-400);">${canlii.url}</a></span>
            </div>
          ` : ''}
        </div>
      ` : ''}
      
      ${hf.name_en ? `
        <div class="detail-section">
          <h4>HuggingFace Dataset</h4>
          <div class="detail-grid">
            <div class="detail-item">
              <span class="label">Case Name</span>
              <span class="value">${this.escapeHtml(hf.name_en)}</span>
            </div>
            <div class="detail-item">
              <span class="label">Dataset</span>
              <span class="value">${hf.dataset || 'N/A'}</span>
            </div>
          </div>
        </div>
      ` : ''}
      
      <div class="detail-section">
        <h4>AI Analysis</h4>
        <div class="detail-grid">
          <div class="detail-item">
            <span class="label">Relation Score</span>
            <span class="value">${formatScore(citation.gpt_relation_score)}</span>
          </div>
          <div class="detail-item">
            <span class="label">Pinpoint Score</span>
            <span class="value">${formatScore(citation.gpt_pinpoint_score)}</span>
          </div>

          <div class="detail-item">
            <span class="label">Reason Code</span>
            <span class="value">${citation.gpt_reason_code || 'N/A'}</span>
          </div>
          <div class="detail-item">
            <span class="label">Below Threshold</span>
            <span class="value">${citation.gpt_below_threshold ? 'Yes' : 'No'}</span>
          </div>
        </div>
        ${citation.gpt_relation_reasoning ? `
          <div class="detail-item" style="margin-top: 12px;">
            <span class="label">AI Reasoning</span>
            <p class="detail-text">${this.escapeHtml(citation.gpt_relation_reasoning)}</p>
          </div>
        ` : ''}
      </div>
      
      <div class="detail-section">
        <h4>Flags</h4>
        <div class="detail-grid">
          <div class="detail-item">
            <span class="label">Out of Jurisdiction</span>
            <span class="value">${citation.out_of_jurisdiction_flag ? 'Yes' : 'No'}</span>
          </div>
          <div class="detail-item">
            <span class="label">Age Mismatch</span>
            <span class="value">${citation.age_mismatch_flag ? 'Yes' : 'No'}</span>
          </div>
          <div class="detail-item">
            <span class="label">Self Citation</span>
            <span class="value">${citation.self_citation_docket_flag ? 'Yes' : 'No'}</span>
          </div>
        </div>
      </div>
      
      ${citation.paragraph ? `
        <div class="detail-section">
          <h4>Full Paragraph Context</h4>
          <p class="detail-text">${this.escapeHtml(citation.paragraph)}</p>
        </div>
      ` : ''}
      
      ${citation.review ? `
        <div class="detail-section">
          <h4>Review Status</h4>
          <div class="detail-grid">
            <div class="detail-item">
              <span class="label">Decision</span>
              <span class="value review-badge ${citation.review.status}">${this.getStatusLabel(citation.review.status)}</span>
            </div>
            ${citation.review.reason ? `
              <div class="detail-item">
                <span class="label">Reason</span>
                <span class="value">${this.escapeHtml(citation.review.reason)}</span>
              </div>
            ` : ''}
          </div>
        </div>
      ` : ''}
    `;

    reviewBtn.textContent = citation.review ? 'Update Review' : 'Review This Citation';
    reviewBtn.onclick = () => {
      this.closeCitationDetail();
      this.openReviewModal(citation);
    };

    modal.classList.add('active');
  }

  closeCitationDetail() {
    document.getElementById('citation-detail-modal').classList.remove('active');
  }

  updateSubmitButton() {
    const submitBtn = document.getElementById('submit-review');
    const reason = document.getElementById('review-reason').value.trim();

    const needsReason = this.selectedStatus === 'fraud' || this.selectedStatus === 'ignore';
    const isValid = this.selectedStatus && (!needsReason || reason.length > 0);

    submitBtn.disabled = !isValid;
  }

  async submitReview() {
    if (!this.currentCitation || !this.selectedStatus) return;

    const reason = document.getElementById('review-reason').value.trim();
    const reviewedCitation = this.currentCitation;
    const newStatus = this.selectedStatus;

    try {
      const res = await fetch('/api/reviews', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filename: reviewedCitation.filename,
          citation: reviewedCitation.citation,
          instance_id: reviewedCitation.instance_id,
          status: newStatus,
          reason: reason || null
        })
      });

      if (!res.ok) {
        throw new Error('Failed to save review');
      }

      this.closeModal();

      // Update local stats instantly
      this.updateStatsAfterReview(reviewedCitation.review?.status, newStatus);

      // Update UI instantly based on current view
      this.updateUIAfterReview(reviewedCitation, newStatus, reason);

    } catch (error) {
      console.error('Error submitting review:', error);
      alert('Failed to save review. Please try again.');
    }
  }

  // Update stats counters without server roundtrip
  updateStatsAfterReview(oldStatus, newStatus) {
    if (!this.stats.reviews) {
      this.stats.reviews = { total: 0, ok: 0, fraud: 0, ignore: 0 };
    }

    // If updating an existing review, decrement old status
    if (oldStatus) {
      this.stats.reviews[oldStatus] = Math.max(0, (this.stats.reviews[oldStatus] || 0) - 1);
    } else {
      // New review, increment total
      this.stats.reviews.total = (this.stats.reviews.total || 0) + 1;
    }

    // Increment new status
    this.stats.reviews[newStatus] = (this.stats.reviews[newStatus] || 0) + 1;

    // Update the UI counters
    document.getElementById('stat-reviewed').textContent = this.stats.reviews.total || 0;
    document.getElementById('dashboard-ok').textContent = this.stats.reviews.ok || 0;
    document.getElementById('dashboard-fraud').textContent = this.stats.reviews.fraud || 0;
    document.getElementById('dashboard-ignore').textContent = this.stats.reviews.ignore || 0;

    const totalCitations = this.stats.total_citation_instances || 1;
    const reviewed = this.stats.reviews.total || 0;
    const progress = Math.round((reviewed / totalCitations) * 100);
    document.getElementById('dashboard-progress').textContent = `${progress}%`;

    // Update flagged count (subtract 1 if we just reviewed a flagged item)
    const flaggedCountEl = document.getElementById('flagged-count');
    const currentFlaggedCount = parseInt(flaggedCountEl.textContent) || 0;
    if (currentFlaggedCount > 0) {
      flaggedCountEl.textContent = currentFlaggedCount - 1;
    }
  }

  // Update UI elements without reloading from server
  updateUIAfterReview(citation, status, reason) {
    const instanceId = citation.instance_id;

    // Find and remove/update the citation card in the current view
    if (this.currentView === 'flagged') {
      // Remove the reviewed card from flagged view with animation
      this.removeCitationCardFromView(instanceId);
    } else if (this.currentView === 'citations') {
      // Update the card in place
      this.updateCitationCardInPlace(citation, status, reason);
    } else if (this.currentView === 'file-detail') {
      // Update the card in place for file detail view
      this.updateCitationCardInPlace(citation, status, reason);
    } else if (this.currentView === 'reviewed') {
      // Refresh reviewed view since we're adding to it
      this.loadReviewedCitations(document.getElementById('reviewed-filter-status').value);
    } else if (this.currentView === 'dashboard') {
      // For dashboard, just update the recent sections lazily in background
      this.renderDashboard();
    }
  }

  // Remove a citation card with fade-out animation
  removeCitationCardFromView(instanceId) {
    const cards = document.querySelectorAll('.citation-card');
    for (const card of cards) {
      // Check if this card contains a button with matching instance_id
      const reviewBtn = card.querySelector('.btn-primary');
      if (reviewBtn) {
        const onclickAttr = reviewBtn.getAttribute('onclick');
        if (onclickAttr && onclickAttr.includes(instanceId)) {
          card.style.transition = 'opacity 0.3s, transform 0.3s, max-height 0.3s';
          card.style.opacity = '0';
          card.style.transform = 'translateX(20px)';
          card.style.maxHeight = card.offsetHeight + 'px';

          setTimeout(() => {
            card.style.maxHeight = '0';
            card.style.padding = '0';
            card.style.margin = '0';
            card.style.overflow = 'hidden';
          }, 150);

          setTimeout(() => {
            card.remove();
            // Check if container is now empty
            const container = document.getElementById('flagged-citations');
            if (container && container.querySelectorAll('.citation-card').length === 0) {
              container.innerHTML = this.renderEmptyState('No flagged citations pending review');
            }
          }, 400);
          break;
        }
      }
    }
  }

  // Update a citation card in place with new review status
  updateCitationCardInPlace(citation, status, reason) {
    const cards = document.querySelectorAll('.citation-card');
    for (const card of cards) {
      const reviewBtn = card.querySelector('.btn-primary');
      if (reviewBtn) {
        const onclickAttr = reviewBtn.getAttribute('onclick');
        if (onclickAttr && onclickAttr.includes(citation.instance_id)) {
          // Update the card classes
          card.classList.remove('flagged', 'reviewed-ok', 'reviewed-fraud', 'reviewed-ignore');
          card.classList.add(`reviewed-${status}`);

          // Update the review button text
          reviewBtn.textContent = 'Update Review';

          // Add or update review status section
          let reviewSection = card.querySelector('.citation-review-status');
          const reviewHTML = `
                        <div class="citation-review-status">
                            <span class="review-badge ${status}">
                                ${this.getStatusIcon(status)}
                                ${this.getStatusLabel(status)}
                            </span>
                            ${reason ? `<span class="review-reason">"${this.escapeHtml(reason)}"</span>` : ''}
                        </div>
                    `;

          if (reviewSection) {
            reviewSection.outerHTML = reviewHTML;
          } else {
            const cardBody = card.querySelector('.citation-body');
            if (cardBody) {
              cardBody.insertAdjacentHTML('beforeend', reviewHTML);
            }
          }

          // Add a brief highlight animation
          card.style.transition = 'box-shadow 0.3s, border-color 0.3s';
          card.style.boxShadow = '0 0 20px rgba(34, 197, 94, 0.4)';
          setTimeout(() => {
            card.style.boxShadow = '';
          }, 500);

          break;
        }
      }
    }
  }

  showView(viewName) {
    this.currentView = viewName;

    // Update navigation
    document.querySelectorAll('.nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.view === viewName);
    });

    // Update page title
    const titles = {
      'dashboard': 'Dashboard',
      'files': 'Files',
      'file-detail': 'File Details',
      'citations': 'All Citations',
      'flagged': 'Flagged Citations',
      'reviewed': 'Reviewed Citations'
    };
    document.getElementById('page-title').textContent = titles[viewName] || 'Citation Reviewer';

    // Show/hide views
    document.querySelectorAll('.view').forEach(view => {
      view.classList.toggle('active', view.id === `view-${viewName}`);
    });

    // Load view-specific data
    if (viewName === 'citations') {
      this.loadAllCitations();
    } else if (viewName === 'flagged') {
      this.loadFlaggedCitations();
    } else if (viewName === 'reviewed') {
      this.loadReviewedCitations();
    }

    // Update URL hash
    window.location.hash = viewName;
  }


  filterReviewedCitations(status) {
    this.loadReviewedCitations(status);
  }

  handleSearch(query) {
    // Simple client-side search for now
    query = query.toLowerCase().trim();

    if (!query) {
      this.renderFiles();
      return;
    }

    const filtered = this.files.filter(f =>
      f.filename.toLowerCase().includes(query) ||
      (f.court_no && f.court_no.toLowerCase().includes(query)) ||
      (f.style_of_cause && f.style_of_cause.toLowerCase().includes(query))
    );

    const tbody = document.getElementById('files-tbody');
    if (filtered.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="loading">No files match your search</td></tr>';
    } else {
      // Re-render with filtered files
      const originalFiles = this.files;
      this.files = filtered;
      this.renderFiles();
      this.files = originalFiles;
    }
  }

  checkUrlHash() {
    const hash = window.location.hash.replace('#', '');
    if (hash && ['dashboard', 'files', 'citations', 'flagged', 'reviewed'].includes(hash)) {
      this.showView(hash);
    }
  }

  getStatusIcon(status) {
    const icons = {
      ok: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="20 6 9 17 4 12"/></svg>',
      fraud: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
      ignore: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/></svg>'
    };
    return icons[status] || '';
  }

  getStatusLabel(status) {
    const labels = {
      ok: 'Verified OK',
      fraud: 'Fraudulent',
      ignore: 'Ignored'
    };
    return labels[status] || status;
  }

  renderEmptyState(message) {
    return `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"/>
          <line x1="8" y1="12" x2="16" y2="12"/>
        </svg>
        <h3>Nothing here</h3>
        <p>${this.escapeHtml(message)}</p>
      </div>
    `;
  }

  showError(message) {
    alert(message);
  }

  escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }
}

// Initialize app
window.app = new CitationReviewerApp();

// Handle browser back/forward
window.addEventListener('hashchange', () => {
  window.app.checkUrlHash();
});

// Update reason input listener
document.getElementById('review-reason').addEventListener('input', () => {
  window.app.updateSubmitButton();
});
