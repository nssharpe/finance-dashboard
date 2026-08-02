/* ============================================================
   Finance Dashboard — app.js
   Vanilla JS, no framework. Talks to FastAPI at same origin.
   ============================================================ */

'use strict';

// ============================================================
// Config
// ============================================================
const API = '';          // same-origin; prefix routes with /api/...
const TXN_LIMIT = 100;  // rows per page

// Palette for Analysis chart — 12 distinct colors
const CHART_COLORS = [
  '#2563EB','#16A34A','#DC2626','#D97706','#7C3AED',
  '#0891B2','#DB2777','#65A30D','#EA580C','#0284C7',
  '#9333EA','#B45309',
];

// ============================================================
// Utility helpers
// ============================================================

/** Format a number as USD currency. */
function fmtCurrency(n) {
  if (n == null || n === '') return '—';
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', minimumFractionDigits: 2,
  }).format(n);
}

/** Format an ISO date string as MM/DD/YYYY. */
function fmtDate(str) {
  if (!str) return '—';
  // Treat YYYY-MM-DD as local date to avoid UTC offset shifting the day
  const [y, m, d] = str.split('-');
  if (y && m && d) {
    return `${m}/${d}/${y}`;
  }
  // Fallback for timestamps
  return new Intl.DateTimeFormat('en-US').format(new Date(str));
}

/** Format a relative or absolute datetime string for the "last updated" label. */
function fmtLastRefresh(str) {
  if (!str) return null;
  const d = new Date(str);
  if (isNaN(d)) return str;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  }).format(d);
}

/** Debounce: return a fn that only fires after `ms` of silence. */
function debounce(fn, ms) {
  let timer;
  return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
}

/** Build a query string from an object, omitting null/empty values. */
function qs(params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== null && v !== undefined && v !== '') p.set(k, v);
  }
  const s = p.toString();
  return s ? '?' + s : '';
}

/** Generic fetch wrapper. Returns parsed JSON or throws. */
async function apiFetch(path, options = {}) {
  const resp = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`;
    try { const j = await resp.json(); msg = j.detail || j.message || msg; } catch (_) {}
    throw new Error(msg);
  }
  return resp.json();
}

// ============================================================
// Toast notifications
// ============================================================
const toastContainer = document.getElementById('toast-container');

/**
 * Show a toast message.
 * @param {string} msg
 * @param {'success'|'error'|'info'} type
 * @param {number} duration  ms before auto-dismiss (0 = never)
 */
function toast(msg, type = 'info', duration = 4000) {
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  toastContainer.appendChild(el);
  if (duration > 0) setTimeout(() => el.remove(), duration);
  return el;
}

// ============================================================
// Status dot & last-updated label
// ============================================================
const statusDot   = document.getElementById('status-dot');
const lastUpdated = document.getElementById('last-updated');

function setConnectionStatus(connected, lastRefresh) {
  statusDot.className = connected ? 'connected' : 'disconnected';
  if (lastRefresh) {
    lastUpdated.textContent = `Updated ${fmtLastRefresh(lastRefresh)}`;
  } else {
    lastUpdated.textContent = connected ? 'Never refreshed' : 'Not connected';
  }
}

// ============================================================
// Setup / connection banner
// ============================================================
const setupBanner      = document.getElementById('setup-banner');
const setupTokenInput  = document.getElementById('setup-token-input');
const btnConnect       = document.getElementById('btn-connect');
const bannerDismiss    = document.getElementById('banner-dismiss');
const btnImportHistory = document.getElementById('btn-import-history');
const importSpinner    = document.getElementById('import-spinner');
const footerReimport   = document.getElementById('footer-reimport');

function showSetupBanner() {
  setupBanner.classList.remove('hidden');
}
function hideSetupBanner() {
  setupBanner.classList.add('hidden');
}

bannerDismiss.addEventListener('click', hideSetupBanner);

btnConnect.addEventListener('click', async () => {
  const token = setupTokenInput.value.trim();
  if (!token) { toast('Paste a Setup Token first.', 'error'); return; }
  btnConnect.disabled = true;
  try {
    const data = await apiFetch('/api/setup', {
      method: 'POST',
      body: JSON.stringify({ setup_token: token }),
    });
    if (data.ok) {
      toast(data.message || 'Connected! Click Refresh to load accounts.', 'success');
      hideSetupBanner();
      loadStatus();
    } else {
      toast(data.message || 'Setup failed.', 'error');
    }
  } catch (err) {
    toast(`Setup error: ${err.message}`, 'error');
  } finally {
    btnConnect.disabled = false;
  }
});

async function doImportHistory() {
  importSpinner.style.display = 'inline-block';
  btnImportHistory.disabled = true;
  const loadingToast = toast('Importing historical data… this may take a moment.', 'info', 0);
  try {
    const data = await apiFetch('/api/import-history', { method: 'POST' });
    loadingToast.remove();
    // Show a brief summary of whatever the backend returns
    const counts = Object.entries(data || {})
      .filter(([, v]) => typeof v === 'number')
      .map(([k, v]) => `${v} ${k}`)
      .join(', ');
    toast(`History imported${counts ? ': ' + counts : ''}.`, 'success');
    loadDashboard();
  } catch (err) {
    loadingToast.remove();
    toast(`Import error: ${err.message}`, 'error');
  } finally {
    importSpinner.style.display = 'none';
    btnImportHistory.disabled = false;
  }
}

btnImportHistory.addEventListener('click', doImportHistory);
footerReimport.addEventListener('click', doImportHistory);

// ============================================================
// #1 CSV Import
// ============================================================
document.querySelectorAll('.csv-import-btn').forEach(btn => {
  btn.addEventListener('click', async () => {
    const source = btn.dataset.source;
    const fileInput = document.querySelector(`.csv-file-input[data-source="${source}"]`);
    if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
      toast(`Select a ${source} CSV file first.`, 'error');
      return;
    }
    const spinner = btn.querySelector('.csv-spinner');
    btn.disabled = true;
    if (spinner) spinner.style.display = 'inline-block';
    const loadingToast = toast(`Importing ${source} CSV…`, 'info', 0);
    try {
      const fd = new FormData();
      fd.append('source', source);
      fd.append('file', fileInput.files[0]);
      const resp = await fetch(API + '/api/import-csv', { method: 'POST', body: fd });
      if (!resp.ok) {
        let msg = `HTTP ${resp.status}`;
        try { const j = await resp.json(); msg = j.detail || j.message || msg; } catch (_) {}
        throw new Error(msg);
      }
      const data = await resp.json();
      loadingToast.remove();
      const counts = Object.entries(data || {})
        .filter(([, v]) => typeof v === 'number')
        .map(([k, v]) => `${v} ${k}`)
        .join(', ');
      toast(`${source} import done${counts ? ': ' + counts : ''}.`, 'success');
      fileInput.value = '';
      reloadActiveTab();
    } catch (err) {
      loadingToast.remove();
      toast(`${source} import error: ${err.message}`, 'error');
    } finally {
      btn.disabled = false;
      if (spinner) spinner.style.display = 'none';
    }
  });
});

// ============================================================
// Refresh button
// ============================================================
const btnRefresh     = document.getElementById('btn-refresh');
const refreshSpinner = document.getElementById('refresh-spinner');

btnRefresh.addEventListener('click', doRefresh);

async function doRefresh() {
  btnRefresh.disabled = true;
  refreshSpinner.style.display = 'inline-block';
  try {
    const data = await apiFetch('/api/refresh', { method: 'POST' });
    if (!data.connected) {
      setConnectionStatus(false, null);
      showSetupBanner();
      toast('Not connected — paste a Setup Token to connect.', 'error');
      return;
    }
    const parts = [];
    if (data.accounts != null)          parts.push(`${data.accounts} account${data.accounts !== 1 ? 's' : ''}`);
    if (data.transactions_seen != null) parts.push(`${data.transactions_seen} transactions`);
    toast(`Updated — ${parts.join(', ') || 'done'}`, 'success');
    if (data.last_refresh) setConnectionStatus(true, data.last_refresh);
    // Reload whichever tab is active
    reloadActiveTab();
  } catch (err) {
    toast(`Refresh failed: ${err.message}`, 'error');
  } finally {
    btnRefresh.disabled = false;
    refreshSpinner.style.display = 'none';
  }
}

// ============================================================
// Status check on load
// ============================================================
async function loadStatus() {
  try {
    const data = await apiFetch('/api/status');
    setConnectionStatus(data.connected, data.last_refresh);
    if (!data.connected) {
      showSetupBanner();
    } else {
      hideSetupBanner();
    }
    return data;
  } catch (err) {
    statusDot.className = 'disconnected';
    lastUpdated.textContent = 'Server unreachable';
    toast('Could not reach server.', 'error');
    return null;
  }
}

// ============================================================
// Settings gear — toggle banner (re-connect / import affordance)
// ============================================================
document.getElementById('btn-settings').addEventListener('click', () => {
  if (setupBanner.classList.contains('hidden')) {
    showSetupBanner();
  } else {
    hideSetupBanner();
  }
});

// ============================================================
// Tab navigation
// ============================================================
const tabBtns   = document.querySelectorAll('.tab-btn');
const tabPanels = document.querySelectorAll('.tab-panel');

// Track whether each tab has been loaded
const tabLoaded = { dashboard: false, transactions: false, analysis: false, insights: false };

tabBtns.forEach(btn => {
  btn.addEventListener('click', () => {
    const tab = btn.dataset.tab;
    tabBtns.forEach(b => b.classList.toggle('active', b === btn));
    tabPanels.forEach(p => p.classList.toggle('active', p.id === `tab-${tab}`));
    if (!tabLoaded[tab]) loadTab(tab);
  });
});

function loadTab(tab) {
  tabLoaded[tab] = true;
  if (tab === 'dashboard')    loadDashboard();
  if (tab === 'transactions') loadTransactions();
  if (tab === 'analysis')     loadAnalysis();
  if (tab === 'insights')     loadInsights();
}

function reloadActiveTab() {
  const activeBtn = document.querySelector('.tab-btn.active');
  if (!activeBtn) return;
  const tab = activeBtn.dataset.tab;
  loadTab(tab);
}

/** Switch to a tab programmatically (mirrors a tab button click). */
function activateTab(tab) {
  tabBtns.forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  tabPanels.forEach(p => p.classList.toggle('active', p.id === `tab-${tab}`));
  if (!tabLoaded[tab]) loadTab(tab);
}

/** Jump to the Transactions tab with filters pre-applied. */
async function goToTransactions({ q = '', category = '', start = '', end = '' } = {}) {
  activateTab('transactions');
  // Ensure dropdowns exist before setting values.
  if (txnAccount.options.length <= 1) await populateAccountDropdown();
  if (txnCategory.options.length <= 1) await populateCategories();

  txnAccount.value  = '';
  txnCategory.value = category || '';
  txnSearch.value   = q || '';
  txnStart.value    = start || '';
  txnEnd.value      = end || '';
  txnSort.value     = 'date_desc';
  txnOffset = 0;
  fetchTransactions();
}

// ============================================================
// Categories cache (shared across tabs)
// ============================================================
let categoriesCache = [];

async function ensureCategories() {
  if (categoriesCache.length) return categoriesCache;
  try {
    const data = await apiFetch('/api/categories');
    categoriesCache = data.categories || [];
  } catch (_) {
    categoriesCache = [];
  }
  return categoriesCache;
}

// ============================================================
// Chart instance registry — destroy before recreate to avoid leaks
// ============================================================
const chartRegistry = {};

function destroyChartInRegistry(key) {
  if (chartRegistry[key]) {
    chartRegistry[key].destroy();
    chartRegistry[key] = null;
  }
}

// Tufte-ish base options for line charts (reused across all line charts)
function makeLineBaseOptions(opts = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { display: opts.showLegend || false },
      tooltip: {
        callbacks: {
          label: ctx => ` ${ctx.dataset.label ? ctx.dataset.label + ': ' : ''}${fmtCurrency(ctx.raw)}`,
        },
      },
    },
    scales: {
      x: {
        display: opts.showAxes !== false,
        grid: { display: false },
        ticks: {
          font: { size: 11, family: 'inherit' },
          maxTicksLimit: opts.maxXTicks || 8,
        },
      },
      y: {
        display: opts.showAxes !== false,
        grid: { color: '#F3F4F6' },
        ticks: {
          font: { size: 11, family: 'inherit' },
          callback: v => '$' + (Math.abs(v) >= 1000 ? (v / 1000).toFixed(0) + 'k' : v),
        },
        border: { display: false },
      },
    },
    ...opts.extra,
  };
}

// ============================================================
// TAB 1 — Dashboard
// ============================================================
const netWorthValue   = document.getElementById('net-worth-value');
const netWorthRefresh = document.getElementById('net-worth-refresh');
const accountGroupsEl = document.getElementById('account-groups');
const dashboardAlert  = document.getElementById('dashboard-alert');

// Net-worth chart range state
let nwRange = 'all';

// Wire up range buttons
document.querySelectorAll('.nw-range-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    nwRange = btn.dataset.range;
    document.querySelectorAll('.nw-range-btn').forEach(b => b.classList.toggle('active', b === btn));
    fetchAndRenderNetworthChart();
  });
});

async function loadDashboard() {
  accountGroupsEl.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  try {
    const data = await apiFetch('/api/accounts');
    renderDashboard(data);
    // After accounts render, fetch net-worth chart
    fetchAndRenderNetworthChart();
  } catch (err) {
    const msg = document.createElement('div');
    msg.className = 'empty-state';
    msg.textContent = `Error loading accounts: ${err.message}`;
    accountGroupsEl.replaceChildren(msg);
  }
}

function computeRangeStart() {
  if (nwRange === 'all') return null;
  const d = new Date();
  if (nwRange === '1y') d.setFullYear(d.getFullYear() - 1);
  if (nwRange === '5y') d.setFullYear(d.getFullYear() - 5);
  return d.toISOString().slice(0, 10);
}

async function fetchAndRenderNetworthChart() {
  const start = computeRangeStart();
  const params = start ? { start } : {};
  destroyChartInRegistry('networth');
  try {
    const data = await apiFetch('/api/networth/history' + qs(params));
    renderNetworthChart(data);
  } catch (err) {
    // Non-fatal: chart just stays empty
    console.warn('Net-worth history error:', err.message);
  }
}

// #3 How many distinct values a series carries (rounded to the cent). A line
// needs at least two distinct points to convey anything over time.
function distinctValueCount(arr) {
  return new Set((arr || []).map(v => Math.round((v || 0) * 100))).size;
}

// #3 Drop a "not enough data yet" placeholder into a chart wrapper in place of
// an empty (or misleadingly flat) canvas.
function renderChartPlaceholder(wrapEl, have, opts = {}) {
  if (!wrapEl) return;
  if (opts.compact) {
    wrapEl.innerHTML =
      '<div class="chart-placeholder compact">A value-over-time chart will appear ' +
      'once more data is logged.</div>';
    return;
  }
  const more = Math.max(1, 2 - have);
  wrapEl.innerHTML =
    '<div class="chart-placeholder">A graph of value over time will show here once ' +
    `<strong>${more}</strong> more data point${more !== 1 ? 's' : ''} ` +
    `${more !== 1 ? 'are' : 'is'} logged.` +
    '<span class="chart-placeholder-sub">History builds up each time you Refresh.</span></div>';
}

function renderNetworthChart(data) {
  destroyChartInRegistry('networth');
  const wrap = document.getElementById('networth-chart-wrap');
  if (!wrap) return;
  const { dates, net_worth } = data;

  if (typeof Chart === 'undefined') {
    wrap.innerHTML =
      '<div class="chart-placeholder">The chart library could not be loaded ' +
      '(no internet connection?).</div>';
    return;
  }

  const distinct = distinctValueCount(net_worth);
  if (!dates || dates.length < 2 || distinct < 2) {
    renderChartPlaceholder(wrap, distinct);
    return;
  }

  // Ensure a fresh canvas (a prior placeholder may have removed it).
  wrap.innerHTML = '<canvas id="networth-chart"></canvas>';
  const canvas = document.getElementById('networth-chart');
  const ctx = canvas.getContext('2d');

  chartRegistry['networth'] = new Chart(ctx, {
    type: 'line',
    data: {
      labels: dates,
      datasets: [{
        label: 'Net Worth',
        data: net_worth,
        borderColor: '#2563EB',
        backgroundColor: '#2563EB12',
        fill: true,
        tension: 0.3,
        borderWidth: 2,
        pointRadius: 0,
        pointHitRadius: 8,
      }],
    },
    options: makeLineBaseOptions({ showAxes: true, maxXTicks: 8 }),
  });
}

function renderDashboard(data) {
  // Net worth
  const nw = data.net_worth ?? 0;
  netWorthValue.textContent = fmtCurrency(nw);
  netWorthValue.className = nw < 0 ? 'negative' : '';
  if (data.last_refresh) {
    netWorthRefresh.textContent = `as of ${fmtLastRefresh(data.last_refresh)}`;
  }

  if (!data.groups || data.groups.length === 0) {
    accountGroupsEl.innerHTML = '<div class="empty-state">No accounts found. Refresh to load.</div>';
    dashboardAlert.innerHTML = '';
    return;
  }

  // #6 Red alert banner logic
  let bofaBalance = null;
  let creditTotal = 0;
  for (const group of data.groups) {
    if (group.type === 'depository') {
      for (const acct of (group.accounts || [])) {
        if (acct.name && acct.name.includes('Adv Plus Banking')) {
          bofaBalance = acct.balance;
        }
      }
    }
    if (group.type === 'credit') {
      creditTotal = Math.abs(group.total || 0);
    }
  }
  if (bofaBalance !== null && (bofaBalance - creditTotal) <= 5000) {
    dashboardAlert.innerHTML = `
      <div class="dashboard-alert-banner">
        &#9888; Credit card balances (${fmtCurrency(creditTotal)}) are within $5k of your Bank of America pay-from balance (${fmtCurrency(bofaBalance)}).
      </div>`;
  } else {
    dashboardAlert.innerHTML = '';
  }

  accountGroupsEl.innerHTML = '';

  for (const group of data.groups) {
    if (!group.accounts || group.accounts.length === 0) continue;

    const groupEl = document.createElement('div');
    groupEl.className = 'account-group card';

    const header = document.createElement('div');
    header.className = 'account-group-header';
    header.innerHTML = `
      <span class="account-group-label">${escHtml(group.label)}</span>
      <span class="account-group-total ${group.total < 0 ? 'negative' : ''}">
        ${fmtCurrency(group.total)}
      </span>`;
    groupEl.appendChild(header);

    // #3 Mini group chart placeholder — filled after net-worth history loads
    const miniChartWrap = document.createElement('div');
    miniChartWrap.className = 'mini-chart-wrap';
    const miniCanvas = document.createElement('canvas');
    miniCanvas.id = `mini-chart-${group.type}`;
    miniChartWrap.appendChild(miniCanvas);
    groupEl.appendChild(miniChartWrap);

    if (group.type === 'investment') {
      const note = document.createElement('div');
      note.className = 'mini-chart-note';
      note.textContent = 'Investment history accrues from each Refresh (no historical market values).';
      groupEl.appendChild(note);
    }

    const list = document.createElement('div');
    list.className = 'account-list';

    for (const acct of group.accounts) {
      const card = document.createElement('div');
      card.className = 'account-card';
      card.setAttribute('role', 'button');
      card.setAttribute('tabindex', '0');
      card.title = `View ${acct.name} details`;
      card.dataset.accountId = acct.id;
      card.dataset.accountName = acct.name;
      card.innerHTML = `
        <div class="account-card-left">
          <div class="account-name">${escHtml(acct.name)}</div>
          <div class="account-org">${escHtml(acct.org || '')}</div>
        </div>
        <div class="account-card-right">
          <div class="account-balance ${acct.balance < 0 ? 'negative' : ''}">
            ${fmtCurrency(acct.balance)}
          </div>
          <div class="account-date">${acct.balance_date ? 'as of ' + fmtDate(acct.balance_date) : ''}</div>
        </div>`;

      // #5 Clickable account card
      const openDetail = () => openAccountDetail(acct.id, acct.name);
      card.addEventListener('click', openDetail);
      card.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openDetail(); }
      });

      list.appendChild(card);
    }
    groupEl.appendChild(list);
    accountGroupsEl.appendChild(groupEl);
  }

  // Fetch net-worth history and render mini charts for each group type
  fetchNetworthForMiniCharts();
}

async function fetchNetworthForMiniCharts() {
  try {
    const data = await apiFetch('/api/networth/history');
    const { dates, by_type } = data;
    if (!dates || !by_type) return;
    if (typeof Chart === 'undefined') return; // net-worth chart shows the reason

    for (const type of ['depository', 'credit', 'investment']) {
      const canvas = document.getElementById(`mini-chart-${type}`);
      if (!canvas) continue;
      const wrap = canvas.parentElement; // .mini-chart-wrap
      const series = by_type[type];

      // #3 Placeholder when there isn't enough movement to plot a line yet
      // (e.g. investment balances with no historical snapshots → flat).
      if (!series || series.length < 2 || distinctValueCount(series) < 2) {
        renderChartPlaceholder(wrap, distinctValueCount(series), { compact: true });
        continue;
      }

      destroyChartInRegistry(`mini-${type}`);
      const ctx = canvas.getContext('2d');
      chartRegistry[`mini-${type}`] = new Chart(ctx, {
        type: 'line',
        data: {
          labels: dates,
          datasets: [{
            data: series,
            borderColor: '#2563EB',
            backgroundColor: 'transparent',
            borderWidth: 1.5,
            pointRadius: 0,
            tension: 0.3,
          }],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { display: false }, tooltip: { enabled: false } },
          scales: {
            x: { display: false },
            y: { display: false },
          },
          animation: false,
        },
      });
    }
  } catch (err) {
    // Mini charts are non-fatal
    console.warn('Mini chart fetch error:', err.message);
  }
}

// ============================================================
// TAB 2 — Transactions
// ============================================================
const txnStart     = document.getElementById('txn-start');
const txnEnd       = document.getElementById('txn-end');
const txnAccount   = document.getElementById('txn-account');
const txnCategory  = document.getElementById('txn-category');
const txnSearch    = document.getElementById('txn-search');
const txnSort      = document.getElementById('txn-sort');
const txnSummary   = document.getElementById('txn-summary');
const txnTbody     = document.getElementById('txn-tbody');
const btnPrev      = document.getElementById('btn-prev');
const btnNext      = document.getElementById('btn-next');
const paginationInfo = document.getElementById('pagination-info');
const btnTxnRefresh = document.getElementById('btn-txn-refresh');

// ---- Bulk selection state ----
const txnBulkBar      = document.getElementById('txn-bulk-bar');
const txnBulkCategory = document.getElementById('txn-bulk-category');
const btnBulkApply    = document.getElementById('btn-bulk-apply');
const txnBulkCount    = document.getElementById('txn-bulk-count');
const btnBulkClear    = document.getElementById('btn-bulk-clear');
const txnSelectAll    = document.getElementById('txn-select-all');

/** Set of selected transaction id strings for the current page. */
const selectedTxnIds = new Set();

/** Sync the bulk bar visibility and count label to `selectedTxnIds`. */
function updateBulkBar() {
  const n = selectedTxnIds.size;
  if (n > 0) {
    txnBulkBar.style.display = '';
    txnBulkCount.textContent = `${n} selected`;
  } else {
    txnBulkBar.style.display = 'none';
    txnBulkCount.textContent = '';
  }
  // Sync select-all indeterminate state
  const total = txnTbody.querySelectorAll('.txn-select').length;
  txnSelectAll.indeterminate = n > 0 && n < total;
  txnSelectAll.checked = total > 0 && n === total;
}

/** Populate the bulk category dropdown (once, then it's cached). */
async function ensureBulkCategoryOptions() {
  if (txnBulkCategory.options.length > 1) return;
  const cats = await ensureCategories();
  for (const c of cats) {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    txnBulkCategory.appendChild(opt);
  }
}

// Select-all header checkbox
txnSelectAll.addEventListener('change', () => {
  const checked = txnSelectAll.checked;
  txnTbody.querySelectorAll('.txn-select').forEach(cb => {
    cb.checked = checked;
    const id = cb.dataset.id;
    if (checked) selectedTxnIds.add(id);
    else         selectedTxnIds.delete(id);
  });
  updateBulkBar();
});

// Clear button
btnBulkClear.addEventListener('click', () => {
  selectedTxnIds.clear();
  txnTbody.querySelectorAll('.txn-select').forEach(cb => { cb.checked = false; });
  txnSelectAll.checked = false;
  txnSelectAll.indeterminate = false;
  txnBulkCategory.value = '';
  btnBulkApply.disabled = true;
  updateBulkBar();
});

// Enable Apply button when a category is chosen
txnBulkCategory.addEventListener('change', () => {
  btnBulkApply.disabled = !txnBulkCategory.value;
});

// Bulk category apply
btnBulkApply.addEventListener('click', async () => {
  const category = txnBulkCategory.value;
  if (!category) { toast('Choose a category first.', 'error'); return; }
  if (selectedTxnIds.size === 0) { toast('No transactions selected.', 'error'); return; }
  const ids = [...selectedTxnIds];
  btnBulkApply.disabled = true;
  txnBulkCategory.disabled = true;
  try {
    const result = await apiFetch('/api/transactions/bulk-category', {
      method: 'POST',
      body: JSON.stringify({ txn_ids: ids, category }),
    });
    toast(`Recategorized ${result.count} transaction${result.count !== 1 ? 's' : ''} as "${category}".`, 'success');
    txnBulkCategory.value = '';
    fetchTransactions(); // clears selection via selectedTxnIds.clear() at top
  } catch (err) {
    toast(`Bulk recategorize failed: ${err.message}`, 'error');
  } finally {
    btnBulkApply.disabled = true;
    txnBulkCategory.disabled = false;
  }
});

let txnOffset = 0;
let txnTotal  = 0;
let txnSum    = 0;

async function loadTransactions() {
  // Populate account dropdown if empty
  if (txnAccount.options.length <= 1) await populateAccountDropdown();
  // Populate category dropdown if empty
  if (txnCategory.options.length <= 1) await populateCategories();
  fetchTransactions();
}

async function populateAccountDropdown() {
  try {
    const data = await apiFetch('/api/accounts');
    txnAccount.innerHTML = '<option value="">All accounts</option>';
    for (const group of (data.groups || [])) {
      if (!group.accounts?.length) continue;
      const optGroup = document.createElement('optgroup');
      optGroup.label = group.label;
      for (const acct of group.accounts) {
        const opt = document.createElement('option');
        opt.value = acct.id;
        opt.textContent = acct.name;
        optGroup.appendChild(opt);
      }
      txnAccount.appendChild(optGroup);
    }
  } catch (_) {}
}

async function populateCategories() {
  const cats = await ensureCategories();
  txnCategory.innerHTML = '<option value="">All categories</option>';
  for (const c of cats) {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    txnCategory.appendChild(opt);
  }
}

async function fetchTransactions() {
  // Clear selection on every fetch (page change, filter change, etc.)
  selectedTxnIds.clear();
  txnSelectAll.checked = false;
  txnSelectAll.indeterminate = false;
  txnBulkCategory.value = '';
  btnBulkApply.disabled = true;
  txnBulkBar.style.display = 'none';

  txnTbody.innerHTML = '<tr><td colspan="6" class="empty-state"><span class="spinner"></span></td></tr>';
  txnSummary.textContent = '';

  // Ensure the bulk category dropdown is populated
  ensureBulkCategoryOptions();

  const sortMap = {
    date_desc:   'date_desc',
    date_asc:    'date_asc',
    amount_desc: 'amount_desc',
    amount_asc:  'amount_asc',
  };

  const params = {
    start:      txnStart.value    || null,
    end:        txnEnd.value      || null,
    account_id: txnAccount.value  || null,
    category:   txnCategory.value || null,
    q:          txnSearch.value   || null,
    sort:       sortMap[txnSort.value] || 'date_desc',
    limit:      TXN_LIMIT,
    offset:     txnOffset,
  };

  try {
    const data = await apiFetch('/api/transactions' + qs(params));
    txnTotal = data.total ?? 0;
    txnSum   = data.sum   ?? 0;
    await renderTxnRows(txnTbody, data.transactions || [], { selectable: true });
    renderPagination();
    const from = txnOffset + 1;
    const to   = Math.min(txnOffset + TXN_LIMIT, txnTotal);
    txnSummary.innerHTML =
      `Showing <strong>${txnTotal === 0 ? 0 : from}–${to}</strong> of <strong>${txnTotal}</strong> transactions&emsp;` +
      `Sum: <strong class="${txnSum < 0 ? 'negative' : 'positive'}">${fmtCurrency(txnSum)}</strong>`;
  } catch (err) {
    const td = document.createElement('td');
    td.colSpan = 6;
    td.className = 'empty-state';
    td.textContent = `Error: ${err.message}`;
    const tr = document.createElement('tr');
    tr.appendChild(td);
    txnTbody.replaceChildren(tr);
  }
}

// ============================================================
// #2 Shared transaction row renderer (Transactions tab + account detail)
// ============================================================
// Cache for amazon items per txn id
const amazonItemsCache = {};

async function renderTxnRows(tbody, txns, { selectable = false } = {}) {
  const colSpan = selectable ? 6 : 5;
  if (txns.length === 0) {
    tbody.innerHTML = `<tr><td colspan="${colSpan}" class="empty-state">No transactions found.</td></tr>`;
    return;
  }

  const cats = await ensureCategories();
  tbody.innerHTML = '';

  for (const txn of txns) {
    const tr = document.createElement('tr');
    tr.dataset.id = txn.id;

    // Prepend checkbox cell for the selectable (main Transactions tab) variant
    if (selectable) {
      const cbCell = document.createElement('td');
      cbCell.style.textAlign = 'center';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.className = 'txn-select';
      cb.dataset.id = txn.id;
      if (selectedTxnIds.has(txn.id)) cb.checked = true;
      cb.addEventListener('change', () => {
        if (cb.checked) selectedTxnIds.add(txn.id);
        else            selectedTxnIds.delete(txn.id);
        updateBulkBar();
      });
      cbCell.appendChild(cb);
      tr.appendChild(cbCell);
    }

    // Build category <select>
    const catSel = buildCategorySelect(cats, txn.category, txn.id, tr);

    const amtClass = txn.amount < 0 ? 'negative' : 'positive';

    // Description cell with optional edited marker + amazon expand
    const descCell = document.createElement('td');
    descCell.className = 'txn-desc-cell';

    // "Create a rule from this" affordance (appears on row hover/focus).
    descCell.appendChild(buildRuleChip(txn));

    // Edited marker
    if (txn.description_overridden) {
      const marker = document.createElement('span');
      marker.className = 'desc-edited-marker';
      marker.title = 'Description has been edited';
      marker.textContent = '✎'; // ✎
      descCell.appendChild(marker);
    }

    // Amazon expand toggle (#1)
    if (txn.has_amazon) {
      const toggle = document.createElement('span');
      toggle.className = 'amazon-toggle';
      toggle.textContent = '▸'; // ▸
      toggle.title = 'Show Amazon items';
      toggle.setAttribute('role', 'button');
      toggle.setAttribute('tabindex', '0');
      toggle.addEventListener('click', e => {
        e.stopPropagation();
        toggleAmazonDetail(txn.id, tr, toggle);
      });
      toggle.addEventListener('keydown', e => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); toggleAmazonDetail(txn.id, tr, toggle); }
      });
      descCell.appendChild(toggle);
    }

    // Description text — click to edit (#2)
    const descSpan = document.createElement('span');
    descSpan.className = 'txn-desc-text';
    descSpan.textContent = txn.description || '';
    descSpan.title = 'Click to edit description';
    descSpan.setAttribute('role', 'button');
    descSpan.setAttribute('tabindex', '0');

    descSpan.addEventListener('click', () => startEditDescription(descSpan, txn, tr));
    descSpan.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); startEditDescription(descSpan, txn, tr); }
    });
    descCell.appendChild(descSpan);

    // Build date cell
    const dateCell = document.createElement('td');
    dateCell.className = 'muted';
    dateCell.textContent = fmtDate(txn.posted);
    if (txn.pending) {
      const badge = document.createElement('span');
      badge.style.cssText = 'font-size:.7rem;color:var(--yellow);margin-left:.3rem';
      badge.textContent = 'pending';
      dateCell.appendChild(badge);
    }

    const acctCell = document.createElement('td');
    acctCell.textContent = txn.account_name || '';

    const catCell = document.createElement('td');
    catCell.appendChild(catSel);

    const amtCell = document.createElement('td');
    amtCell.className = `num ${amtClass}`;
    amtCell.textContent = fmtCurrency(txn.amount);

    tr.appendChild(dateCell);
    tr.appendChild(acctCell);
    tr.appendChild(descCell);
    tr.appendChild(catCell);
    tr.appendChild(amtCell);

    tbody.appendChild(tr);
  }
}

// #2 Inline description edit
function startEditDescription(descSpan, txn, tr) {
  // Prevent double-editing
  if (descSpan.querySelector('input')) return;
  const currentText = txn.description || '';

  const input = document.createElement('input');
  input.type = 'text';
  input.value = currentText;
  input.className = 'desc-edit-input';
  input.style.cssText = 'width:100%;border:1px solid var(--accent);border-radius:3px;padding:.15rem .3rem;font:inherit;font-size:.875rem;';

  // Replace the span content temporarily
  const originalContent = descSpan.textContent;
  descSpan.textContent = '';
  descSpan.appendChild(input);
  input.focus();
  input.select();

  let saved = false;

  async function save() {
    if (saved) return;
    saved = true;
    const newDesc = input.value.trim();
    descSpan.textContent = originalContent; // revert optimistically

    if (newDesc === currentText) return; // no change

    try {
      const result = await apiFetch(`/api/transactions/${txn.id}/description`, {
        method: 'POST',
        body: JSON.stringify({ description: newDesc }),
      });
      const finalDesc = result.description || newDesc;
      txn.description = finalDesc;
      descSpan.textContent = finalDesc;

      // Show edited marker if not already there
      const cell = descSpan.closest('td');
      if (!cell.querySelector('.desc-edited-marker')) {
        const marker = document.createElement('span');
        marker.className = 'desc-edited-marker';
        marker.title = 'Description has been edited';
        marker.textContent = '✎';
        cell.insertBefore(marker, descSpan);
      }

      tr.classList.remove('flash-success');
      void tr.offsetWidth;
      tr.classList.add('flash-success');
    } catch (err) {
      descSpan.textContent = originalContent;
      toast(`Description update failed: ${err.message}`, 'error');
    }
  }

  function revert() {
    if (saved) return;
    saved = true;
    descSpan.textContent = originalContent;
  }

  input.addEventListener('keydown', e => {
    e.stopPropagation();
    if (e.key === 'Enter') { e.preventDefault(); save(); }
    if (e.key === 'Escape') { e.preventDefault(); revert(); }
  });
  input.addEventListener('blur', save);
}

// #1 Amazon item expand/collapse
async function toggleAmazonDetail(txnId, tr, toggle) {
  const existing = tr.nextSibling;
  if (existing && existing.classList && existing.classList.contains('amazon-detail-row')) {
    existing.remove();
    toggle.textContent = '▸';
    toggle.title = 'Show Amazon items';
    return;
  }

  toggle.textContent = '▾'; // ▾
  toggle.title = 'Hide Amazon items';

  const detailRow = document.createElement('tr');
  detailRow.className = 'amazon-detail-row';
  const detailTd = document.createElement('td');
  // Match colspan to the number of cells in the parent row
  detailTd.colSpan = tr.cells.length || 5;
  detailTd.className = 'amazon-detail-cell';

  if (amazonItemsCache[txnId]) {
    detailTd.appendChild(buildAmazonTable(amazonItemsCache[txnId]));
  } else {
    detailTd.innerHTML = '<span class="spinner"></span> Loading…';
    try {
      const data = await apiFetch(`/api/transactions/${txnId}/amazon-items`);
      amazonItemsCache[txnId] = data.items || [];
      detailTd.innerHTML = '';
      detailTd.appendChild(buildAmazonTable(amazonItemsCache[txnId]));
    } catch (err) {
      detailTd.textContent = `Error loading items: ${err.message}`;
    }
  }

  detailRow.appendChild(detailTd);
  tr.after(detailRow);
}

function buildAmazonTable(items) {
  if (!items || items.length === 0) {
    const msg = document.createElement('span');
    msg.className = 'muted';
    msg.style.fontSize = '.82rem';
    msg.textContent = 'No items found.';
    return msg;
  }
  const table = document.createElement('table');
  table.className = 'amazon-items-table';
  table.innerHTML = `<thead><tr>
    <th>Product</th><th>Qty</th><th class="num">Total</th><th>Order Date</th>
  </tr></thead>`;
  const tbody = document.createElement('tbody');
  for (const item of items) {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escHtml(item.product_name || '')}</td>
      <td>${escHtml(String(item.quantity ?? ''))}</td>
      <td class="num">${fmtCurrency(item.total_owed)}</td>
      <td class="muted">${fmtDate(item.order_date)}</td>`;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

function buildCategorySelect(cats, currentCategory, txnId, tr) {
  const sel = document.createElement('select');
  sel.className = 'cat-select';

  // Always include "Uncategorized" as the empty option
  const blankOpt = document.createElement('option');
  blankOpt.value = '';
  blankOpt.textContent = '—';
  sel.appendChild(blankOpt);

  for (const c of cats) {
    const opt = document.createElement('option');
    opt.value = c;
    opt.textContent = c;
    if (c === currentCategory) opt.selected = true;
    sel.appendChild(opt);
  }
  if (currentCategory && !cats.includes(currentCategory)) {
    // Unexpected category from DB — add it so it shows correctly
    const opt = document.createElement('option');
    opt.value = currentCategory;
    opt.textContent = currentCategory;
    opt.selected = true;
    sel.appendChild(opt);
  }

  sel.addEventListener('change', async () => {
    const newCat = sel.value;
    try {
      await apiFetch(`/api/transactions/${txnId}/category`, {
        method: 'POST',
        body: JSON.stringify({ category: newCat }),
      });
      tr.classList.remove('flash-success');
      // Trigger reflow to restart animation
      void tr.offsetWidth;
      tr.classList.add('flash-success');
    } catch (err) {
      toast(`Category update failed: ${err.message}`, 'error');
      // Revert select
      sel.value = sel.dataset.prev || '';
    }
    sel.dataset.prev = sel.value;
  });
  sel.dataset.prev = currentCategory || '';
  return sel;
}

function renderPagination() {
  const from = txnOffset + 1;
  const to   = Math.min(txnOffset + TXN_LIMIT, txnTotal);
  paginationInfo.textContent = txnTotal === 0 ? '' : `${from}–${to} of ${txnTotal}`;
  btnPrev.disabled = txnOffset === 0;
  btnNext.disabled = txnOffset + TXN_LIMIT >= txnTotal;
}

btnPrev.addEventListener('click', () => {
  txnOffset = Math.max(0, txnOffset - TXN_LIMIT);
  fetchTransactions();
});
btnNext.addEventListener('click', () => {
  txnOffset += TXN_LIMIT;
  fetchTransactions();
});
btnTxnRefresh.addEventListener('click', () => {
  txnOffset = 0;
  fetchTransactions();
});

// Search debounce
txnSearch.addEventListener('input', debounce(() => { txnOffset = 0; fetchTransactions(); }, 300));

// Other filter changes reset to page 1
[txnStart, txnEnd, txnAccount, txnCategory, txnSort].forEach(el => {
  el.addEventListener('change', () => { txnOffset = 0; fetchTransactions(); });
});

// ============================================================
// #5 Account detail view
// ============================================================
const accountDetailSection = document.getElementById('account-detail');
const mainSection          = document.getElementById('main');
const btnAccountBack       = document.getElementById('btn-account-back');
const accountDetailName    = document.getElementById('account-detail-name');
const accountDetailBalance = document.getElementById('account-detail-balance');
const acctDetailStart      = document.getElementById('acct-detail-start');
const acctDetailEnd        = document.getElementById('acct-detail-end');
const accountDetailTbody   = document.getElementById('account-detail-txns');

let currentDetailAccountId   = null;
let previousTab              = 'dashboard';

btnAccountBack.addEventListener('click', () => {
  closeAccountDetail();
});

function openAccountDetail(accountId, accountName) {
  // Track which tab we came from
  const activeBtn = document.querySelector('.tab-btn.active');
  previousTab = activeBtn ? activeBtn.dataset.tab : 'dashboard';

  currentDetailAccountId = accountId;
  accountDetailName.textContent = accountName;
  accountDetailBalance.textContent = '';

  // Clear date inputs
  acctDetailStart.value = '';
  acctDetailEnd.value = '';

  mainSection.style.display = 'none';
  document.getElementById('tabs').style.display = 'none';
  accountDetailSection.style.display = 'block';

  destroyChartInRegistry('account-detail');
  loadAccountDetail();
}

function closeAccountDetail() {
  accountDetailSection.style.display = 'none';
  mainSection.style.display = '';
  document.getElementById('tabs').style.display = '';
  destroyChartInRegistry('account-detail');
  currentDetailAccountId = null;
}

async function loadAccountDetail() {
  if (!currentDetailAccountId) return;

  accountDetailTbody.innerHTML = '<tr><td colspan="5" class="empty-state"><span class="spinner"></span></td></tr>';
  destroyChartInRegistry('account-detail');

  const chartWrap = document.getElementById('account-chart-wrap');
  chartWrap.innerHTML = '<div style="height:180px;display:flex;align-items:center;justify-content:center"><span class="spinner"></span></div>';

  const start = acctDetailStart.value || null;
  const end   = acctDetailEnd.value   || null;

  // Fetch history
  try {
    const histData = await apiFetch(`/api/accounts/${currentDetailAccountId}/history` + qs({ start, end }));
    const acct = histData.account || {};
    accountDetailName.textContent = acct.name || accountDetailName.textContent;
    accountDetailBalance.textContent = fmtCurrency(acct.balance);
    accountDetailBalance.className = (acct.balance || 0) < 0 ? 'account-detail-balance negative' : 'account-detail-balance';

    // Render history chart
    chartWrap.innerHTML = '<canvas id="account-chart"></canvas>';
    if (histData.dates && histData.dates.length > 0) {
      const canvas = document.getElementById('account-chart');
      const ctx = canvas.getContext('2d');
      chartRegistry['account-detail'] = new Chart(ctx, {
        type: 'line',
        data: {
          labels: histData.dates,
          datasets: [{
            label: acct.name || 'Balance',
            data: histData.balances,
            borderColor: '#2563EB',
            backgroundColor: '#2563EB12',
            fill: true,
            tension: 0.3,
            borderWidth: 2,
            pointRadius: 0,
            pointHitRadius: 8,
          }],
        },
        options: makeLineBaseOptions({ showAxes: true, maxXTicks: 8 }),
      });
    } else {
      chartWrap.innerHTML = '<div class="empty-state">No history data available.</div>';
    }
  } catch (err) {
    chartWrap.innerHTML = `<div class="empty-state">History error: ${escHtml(err.message)}</div>`;
  }

  // Fetch transactions for this account
  try {
    const txnParams = {
      account_id: currentDetailAccountId,
      limit: 100,
      sort: 'date_desc',
    };
    if (start) txnParams.start = start;
    if (end)   txnParams.end   = end;
    const txnData = await apiFetch('/api/transactions' + qs(txnParams));
    await renderTxnRows(accountDetailTbody, txnData.transactions || []);
  } catch (err) {
    accountDetailTbody.innerHTML = `<tr><td colspan="5" class="empty-state">Error: ${escHtml(err.message)}</td></tr>`;
  }
}

// Re-fetch when date inputs change
[acctDetailStart, acctDetailEnd].forEach(el => {
  el.addEventListener('change', () => { loadAccountDetail(); });
});

// ============================================================
// TAB 3 — Analysis
// ============================================================
const analysisStartYear = document.getElementById('analysis-start-year');
const analysisEndYear   = document.getElementById('analysis-end-year');
const btnAnalysisLoad   = document.getElementById('btn-analysis-load');
const analysisTableWrap = document.getElementById('analysis-table-wrap');
const chartModeButtons  = document.querySelectorAll('.chart-mode-btn');

let analysisData  = null;
let chartMode     = 'total'; // 'total' | 'category'

// Number of named category series in the "By category" chart; the rest are
// bundled into the dashed "Other" line.
const ANALYSIS_TOP_N = 8;

/** Rank categories by total spend across the loaded years and split into the
 *  named (top-N) and bundled ("Other") sets — same logic the chart uses. */
function splitTopCategories(data) {
  const { years, categories, matrix } = data;
  const catTotals = categories.map(cat => ({
    cat,
    total: years.reduce((sum, y) => sum + Math.abs(matrix?.[cat]?.[y] ?? 0), 0),
  }));
  catTotals.sort((a, b) => b.total - a.total);
  return {
    topCats:  catTotals.slice(0, ANALYSIS_TOP_N).map(c => c.cat),
    restCats: catTotals.slice(ANALYSIS_TOP_N).map(c => c.cat),
  };
}

chartModeButtons.forEach(btn => {
  btn.addEventListener('click', () => {
    chartMode = btn.dataset.mode;
    chartModeButtons.forEach(b => b.classList.toggle('active', b === btn));
    if (analysisData) renderAnalysisChart(analysisData);
  });
});

btnAnalysisLoad.addEventListener('click', () => loadAnalysis());

async function loadAnalysis() {
  const startYear = parseInt(analysisStartYear.value, 10) || 2013;
  const endYear   = parseInt(analysisEndYear.value, 10)   || 2025;

  btnAnalysisLoad.disabled = true;
  analysisTableWrap.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  destroyChartInRegistry('analysis');

  try {
    const data = await apiFetch(`/api/analysis${qs({ start_year: startYear, end_year: endYear })}`);
    analysisData = data;
    renderAnalysisChart(data);
    renderAnalysisTable(data);
  } catch (err) {
    const msg = document.createElement('div');
    msg.className = 'empty-state';
    msg.textContent = `Error: ${err.message}`;
    analysisTableWrap.replaceChildren(msg);
    toast(`Analysis error: ${err.message}`, 'error');
  } finally {
    btnAnalysisLoad.disabled = false;
  }
}

function renderAnalysisChart(data) {
  destroyChartInRegistry('analysis');
  const { years, categories, matrix, totals_by_year } = data;
  if (!years || years.length === 0) return;

  const ctx = document.getElementById('analysis-chart').getContext('2d');

  // Common Tufte-ish chart defaults
  const baseOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        position: 'bottom',
        labels: {
          boxWidth: 12,
          padding: 14,
          font: { size: 11, family: 'inherit' },
        },
      },
      tooltip: {
        callbacks: {
          label: ctx => ` ${ctx.dataset.label}: ${fmtCurrency(ctx.raw)}`,
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: { font: { size: 11, family: 'inherit' } },
      },
      y: {
        grid: { color: '#F3F4F6' },
        ticks: {
          font: { size: 11, family: 'inherit' },
          callback: v => '$' + (Math.abs(v) >= 1000 ? (v/1000).toFixed(0) + 'k' : v),
        },
        border: { display: false },
      },
    },
  };

  if (chartMode === 'total') {
    const totals = years.map(y => Math.abs(totals_by_year?.[y] ?? 0));
    chartRegistry['analysis'] = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: years.map(String),
        datasets: [{
          label: 'Total Spending',
          data: totals,
          backgroundColor: '#2563EB',
          borderRadius: 3,
          borderSkipped: false,
        }],
      },
      options: {
        ...baseOptions,
        plugins: {
          ...baseOptions.plugins,
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: ctx => ` ${fmtCurrency(ctx.raw)}`,
            },
          },
        },
      },
    });
  } else {
    const { topCats, restCats } = splitTopCategories(data);

    const datasets = topCats.map((cat, i) => ({
      label: cat,
      data: years.map(y => Math.abs(matrix?.[cat]?.[y] ?? 0) || null),
      borderColor: CHART_COLORS[i % CHART_COLORS.length],
      backgroundColor: CHART_COLORS[i % CHART_COLORS.length] + '18',
      tension: 0.3,
      borderWidth: 2,
      pointRadius: 3,
      spanGaps: true,
    }));

    if (restCats.length > 0) {
      datasets.push({
        label: 'Other',
        data: years.map(y =>
          restCats.reduce((sum, cat) => sum + Math.abs(matrix?.[cat]?.[y] ?? 0), 0) || null
        ),
        borderColor: '#9CA3AF',
        backgroundColor: '#9CA3AF18',
        tension: 0.3,
        borderWidth: 1.5,
        borderDash: [4, 3],
        pointRadius: 2,
        spanGaps: true,
      });
    }

    // Clicking a point drills into that category + year's transactions.
    const categoryClickOptions = {
      ...baseOptions,
      onHover: (evt, els) => {
        const tgt = evt?.native?.target;
        if (tgt) tgt.style.cursor = els.length ? 'pointer' : 'default';
      },
      onClick: (evt, els, chart) => {
        if (!els.length) return;
        const { datasetIndex, index } = els[0];
        const cat  = chart.data.datasets[datasetIndex].label;
        const year = years[index];
        if (cat === 'Other') {
          openOtherBreakdown(year);
          return;
        }
        openCategoryDrilldown(cat, year);
      },
    };

    chartRegistry['analysis'] = new Chart(ctx, {
      type: 'line',
      data: { labels: years.map(String), datasets },
      options: categoryClickOptions,
    });
  }
}

function renderAnalysisTable(data) {
  const { years, categories, matrix, totals_by_year } = data;
  if (!years || years.length === 0) {
    analysisTableWrap.innerHTML = '<div class="empty-state">No data.</div>';
    return;
  }

  const lastYear = years[years.length - 1];
  const prevYear = years.length >= 2 ? years[years.length - 2] : null;
  const showPct  = prevYear != null;

  const table = document.createElement('table');
  table.id = 'analysis-table';

  const thead = document.createElement('thead');
  let hRow = '<tr><th>Category</th>';
  for (const y of years) hRow += `<th class="num">${y}</th>`;
  if (showPct) hRow += `<th class="num">${lastYear} vs ${prevYear}</th>`;
  hRow += '</tr>';
  thead.innerHTML = hRow;
  table.appendChild(thead);

  const tbody = document.createElement('tbody');

  for (const cat of categories) {
    const tr = document.createElement('tr');
    let cells = `<td>${escHtml(cat)}</td>`;
    for (const y of years) {
      const val = matrix?.[cat]?.[y];
      if (val != null) {
        cells += `<td class="num drill-cell" data-cat="${escHtml(cat)}" data-year="${y}" `
               + `title="View ${escHtml(cat)} transactions in ${y}">${fmtCurrency(Math.abs(val))}</td>`;
      } else {
        cells += `<td class="num"><span style="color:var(--gray-300)">—</span></td>`;
      }
    }
    if (showPct) {
      const curr = matrix?.[cat]?.[lastYear];
      const prev = matrix?.[cat]?.[prevYear];
      if (curr != null && prev != null && Math.abs(prev) > 0) {
        const pct = ((Math.abs(curr) - Math.abs(prev)) / Math.abs(prev)) * 100;
        const sign = pct >= 0 ? '+' : '';
        const cls  = pct > 5 ? 'pct-pos' : pct < -5 ? 'pct-neg' : '';
        cells += `<td class="num ${cls}">${sign}${pct.toFixed(1)}%</td>`;
      } else {
        cells += `<td class="num"><span style="color:var(--gray-300)">—</span></td>`;
      }
    }
    tr.innerHTML = cells;
    tbody.appendChild(tr);
  }

  // Total row
  const totalTr = document.createElement('tr');
  totalTr.className = 'total-row';
  let totalCells = '<td>Total</td>';
  for (const y of years) {
    const val = totals_by_year?.[y];
    totalCells += `<td class="num">${val != null ? fmtCurrency(Math.abs(val)) : '—'}</td>`;
  }
  if (showPct) {
    const curr = totals_by_year?.[lastYear];
    const prev = totals_by_year?.[prevYear];
    if (curr != null && prev != null && Math.abs(prev) > 0) {
      const pct  = ((Math.abs(curr) - Math.abs(prev)) / Math.abs(prev)) * 100;
      const sign = pct >= 0 ? '+' : '';
      const cls  = pct > 5 ? 'pct-pos' : pct < -5 ? 'pct-neg' : '';
      totalCells += `<td class="num ${cls}">${sign}${pct.toFixed(1)}%</td>`;
    } else {
      totalCells += '<td class="num">—</td>';
    }
  }
  totalTr.innerHTML = totalCells;
  tbody.appendChild(totalTr);

  table.appendChild(tbody);

  // Drill-down: click a category-year cell to inspect its transactions.
  table.addEventListener('click', e => {
    const cell = e.target.closest('td.drill-cell');
    if (!cell) return;
    openCategoryDrilldown(cell.dataset.cat, parseInt(cell.dataset.year, 10));
  });

  analysisTableWrap.innerHTML = '';
  analysisTableWrap.appendChild(table);
}

// ============================================================
// Analysis — category drill-down (replaces the breakdown table)
// ============================================================

/** Fetch and show the transactions making up one category in one year. */
async function openCategoryDrilldown(cat, year) {
  analysisTableWrap.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  const params = {
    category: cat,
    start: `${year}-01-01`,
    end: `${year}-12-31`,
    sort: 'amount_asc',      // largest spend (most negative) first
    limit: 2000,
  };
  try {
    const data = await apiFetch('/api/transactions' + qs(params));
    renderCategoryDrilldown(cat, year, data);
  } catch (err) {
    const msg = document.createElement('div');
    msg.className = 'empty-state';
    msg.textContent = `Error: ${err.message}`;
    analysisTableWrap.replaceChildren(msg);
    toast(`Drill-down error: ${err.message}`, 'error');
  }
}

/** Show which categories are bundled into the chart's "Other" line for a
 *  given year, so the user can see what "Other" actually contains. Each row
 *  drills further into that category + year's transactions. */
function openOtherBreakdown(year) {
  if (!analysisData) return;
  const { restCats } = splitTopCategories(analysisData);
  const matrix = analysisData.matrix || {};

  const rows = restCats
    .map(cat => ({ cat, value: Math.abs(matrix?.[cat]?.[year] ?? 0) }))
    .filter(r => r.value > 0)
    .sort((a, b) => b.value - a.value);

  analysisTableWrap.innerHTML = '';

  const head = document.createElement('div');
  head.className = 'drilldown-head';

  const back = document.createElement('button');
  back.className = 'btn btn-ghost btn-sm';
  back.innerHTML = '&#8592; Back';
  back.title = 'Back to the year-over-year breakdown';
  back.addEventListener('click', () => loadAnalysis());

  const title = document.createElement('div');
  title.className = 'drilldown-title';
  title.innerHTML = `“Other” categories <span class="drilldown-year">· ${year}</span>`;

  head.appendChild(back);
  head.appendChild(title);
  analysisTableWrap.appendChild(head);

  const total = rows.reduce((s, r) => s + r.value, 0);
  const summary = document.createElement('div');
  summary.className = 'drilldown-summary';
  summary.innerHTML =
    `<strong>${rows.length}</strong> categor${rows.length !== 1 ? 'ies' : 'y'} ` +
    `bundled into “Other”&emsp;Total: <strong>${fmtCurrency(total)}</strong>`;
  analysisTableWrap.appendChild(summary);

  if (rows.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent = 'No bundled categories with spending in this year.';
    analysisTableWrap.appendChild(empty);
    return;
  }

  const table = document.createElement('table');
  table.id = 'analysis-table';
  table.innerHTML = `<thead><tr><th>Category</th><th class="num">${year}</th></tr></thead>`;
  const tbody = document.createElement('tbody');
  for (const r of rows) {
    const tr = document.createElement('tr');
    tr.innerHTML =
      `<td>${escHtml(r.cat)}</td>` +
      `<td class="num drill-cell" data-cat="${escHtml(r.cat)}" data-year="${year}" ` +
      `title="View ${escHtml(r.cat)} transactions in ${year}">${fmtCurrency(r.value)}</td>`;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  table.addEventListener('click', e => {
    const cell = e.target.closest('td.drill-cell');
    if (!cell) return;
    openCategoryDrilldown(cell.dataset.cat, parseInt(cell.dataset.year, 10));
  });
  analysisTableWrap.appendChild(table);
}

function renderCategoryDrilldown(cat, year, data) {
  const txns = data.transactions || [];
  const total = data.total ?? txns.length;
  const sum   = data.sum ?? 0;

  analysisTableWrap.innerHTML = '';

  // Header: back button, title, search box.
  const head = document.createElement('div');
  head.className = 'drilldown-head';

  const back = document.createElement('button');
  back.className = 'btn btn-ghost btn-sm';
  back.innerHTML = '&#8592; Back';
  back.title = 'Back to the year-over-year breakdown';
  back.addEventListener('click', () => loadAnalysis());

  const title = document.createElement('div');
  title.className = 'drilldown-title';
  title.innerHTML = `${escHtml(cat)} <span class="drilldown-year">· ${year}</span>`;

  const search = document.createElement('input');
  search.type = 'text';
  search.className = 'drilldown-search';
  search.placeholder = 'Filter descriptions…';

  head.appendChild(back);
  head.appendChild(title);
  head.appendChild(search);
  analysisTableWrap.appendChild(head);

  // Summary line.
  const summary = document.createElement('div');
  summary.className = 'drilldown-summary';
  summary.innerHTML =
    `<strong>${total}</strong> transaction${total !== 1 ? 's' : ''}` +
    `&emsp;Total: <strong>${fmtCurrency(Math.abs(sum))}</strong>` +
    (total >= 2000 ? '&emsp;(showing first 2000)' : '');
  analysisTableWrap.appendChild(summary);

  if (txns.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'empty-state';
    empty.textContent =
      'No individual transactions stored for this category and year ' +
      '(years before 2018 use imported annual summary totals).';
    analysisTableWrap.appendChild(empty);
    return;
  }

  // Table (reuses the shared transaction-row renderer, so rows are editable).
  const wrap = document.createElement('div');
  wrap.className = 'table-wrap';
  const table = document.createElement('table');
  table.className = 'txn-grid';
  table.innerHTML = `<thead><tr>
    <th>Date</th><th>Account</th><th>Description</th><th>Category</th><th class="num">Amount</th>
  </tr></thead>`;
  const tbody = document.createElement('tbody');
  table.appendChild(tbody);
  wrap.appendChild(table);
  analysisTableWrap.appendChild(wrap);

  renderTxnRows(tbody, txns);

  // Client-side filter over the rendered rows.
  search.addEventListener('input', debounce(() => {
    const term = search.value.trim().toLowerCase();
    for (const tr of tbody.querySelectorAll('tr')) {
      // Skip amazon detail rows — keep them tied to their parent's visibility.
      if (tr.classList.contains('amazon-detail-row')) continue;
      const desc = (tr.querySelector('.txn-desc-text')?.textContent || '').toLowerCase();
      tr.style.display = !term || desc.includes(term) ? '' : 'none';
    }
  }, 200));
}

// ============================================================
// TAB 4 — Insights (Subscriptions · Anomalies · Top Merchants)
// ============================================================

const subscriptionsBody  = document.getElementById('subscriptions-body');
const subscriptionsSummary = document.getElementById('subscriptions-summary');
const anomaliesBody      = document.getElementById('anomalies-body');
const anomaliesPeriod    = document.getElementById('anomalies-period');
const merchantsBody      = document.getElementById('merchants-body');
const merchantsStart     = document.getElementById('merchants-start');
const merchantsEnd       = document.getElementById('merchants-end');
const merchantsCategory  = document.getElementById('merchants-category');
const btnMerchantsLoad   = document.getElementById('btn-merchants-load');

async function loadInsights() {
  // Fire all three in parallel; each renders independently as it resolves.
  loadSubscriptions();
  loadAnomalies();
  loadMerchantRules();
  loadMerchants();

  // Populate merchant category dropdown
  const cats = await ensureCategories();
  merchantsCategory.innerHTML = '<option value="">All categories</option>';
  for (const c of cats) {
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    merchantsCategory.appendChild(opt);
  }
}

// ---- Subscriptions ----

async function loadSubscriptions() {
  subscriptionsBody.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  subscriptionsSummary.textContent = '';
  try {
    const data = await apiFetch('/api/subscriptions');
    renderSubscriptions(data);
  } catch (err) {
    subscriptionsBody.innerHTML = `<div class="empty-state">Error: ${escHtml(err.message)}</div>`;
  }
}

const CADENCE_LABEL = { monthly: 'Monthly', biweekly: 'Every 2 wks', quarterly: 'Quarterly', annual: 'Annual' };

function buildSubCard(sub) {
  const card = document.createElement('div');
  card.className = 'sub-card' + (sub.inactive ? ' sub-card-inactive' : '');
  card.setAttribute('role', 'button');
  card.setAttribute('tabindex', '0');
  card.title = `View ${sub.display_name} transactions`;

  const badges = [];
  if (sub.is_new && !sub.inactive)        badges.push('<span class="badge badge-new">New</span>');
  if (sub.price_changed && !sub.inactive) badges.push('<span class="badge badge-warn">Price changed</span>');
  if (sub.inactive)                       badges.push('<span class="badge badge-inactive">Inactive</span>');

  const perCharge = sub.cadence === 'monthly'
    ? fmtCurrency(sub.current_amount)
    : `${fmtCurrency(sub.current_amount)} · ${fmtCurrency(sub.monthly_cost)}<span class="muted-label">/mo</span>`;

  card.innerHTML = `
    <div class="sub-name">${escHtml(sub.display_name)}</div>
    <div class="sub-meta">
      <span class="sub-cadence">${escHtml(CADENCE_LABEL[sub.cadence] || sub.cadence)}</span>
      <span class="sub-cat muted-label">${escHtml(sub.category || '')}</span>
    </div>
    <div class="sub-amount">${perCharge}</div>
    <div class="sub-footer">
      <span class="muted-label">Last ${fmtDate(sub.last_date)}</span>
      ${badges.join('')}
    </div>`;

  // Click → Transactions tab, filtered to this merchant.
  const go = () => goToTransactions({ q: sub.search || sub.name });
  card.addEventListener('click', go);
  card.addEventListener('keydown', e => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
  });
  return card;
}

function renderSubscriptions(data) {
  const { subscriptions, total_monthly, total_annual } = data;

  if (!subscriptions || subscriptions.length === 0) {
    subscriptionsBody.innerHTML = '<div class="empty-state">No recurring charges detected yet — data builds up over time.</div>';
    subscriptionsSummary.textContent = '';
    return;
  }

  const active   = subscriptions.filter(s => !s.inactive);
  const inactive = subscriptions.filter(s => s.inactive);

  subscriptionsSummary.innerHTML =
    `<strong>${active.length} active</strong>&ensp;·&ensp;` +
    `${fmtCurrency(total_monthly)}<span class="muted-label">/mo</span>&ensp;·&ensp;` +
    `${fmtCurrency(total_annual)}<span class="muted-label">/yr</span>`;

  subscriptionsBody.innerHTML = '';

  const grid = document.createElement('div');
  grid.className = 'sub-grid';
  for (const sub of active) grid.appendChild(buildSubCard(sub));
  subscriptionsBody.appendChild(grid);

  if (inactive.length === 0) return;

  // Collapsible inactive section.
  const toggle = document.createElement('button');
  toggle.className = 'btn btn-ghost btn-sm sub-inactive-toggle';
  toggle.textContent = `Show ${inactive.length} inactive ▾`;

  const inactiveGrid = document.createElement('div');
  inactiveGrid.className = 'sub-grid';
  inactiveGrid.style.display = 'none';
  inactiveGrid.style.marginTop = '.75rem';
  for (const sub of inactive) inactiveGrid.appendChild(buildSubCard(sub));

  let shown = false;
  toggle.addEventListener('click', () => {
    shown = !shown;
    inactiveGrid.style.display = shown ? '' : 'none';
    toggle.textContent = shown
      ? `Hide ${inactive.length} inactive ▴`
      : `Show ${inactive.length} inactive ▾`;
  });

  subscriptionsBody.appendChild(toggle);
  subscriptionsBody.appendChild(inactiveGrid);
}

// ---- Anomaly Digest ----

async function loadAnomalies() {
  anomaliesBody.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  anomaliesPeriod.textContent = '';
  try {
    const data = await apiFetch('/api/anomalies');
    renderAnomalies(data);
  } catch (err) {
    anomaliesBody.innerHTML = `<div class="empty-state">Error: ${escHtml(err.message)}</div>`;
  }
}

function renderAnomalies(data) {
  const { as_of, category_spikes, new_merchants, large_transactions } = data;

  anomaliesPeriod.textContent = `as of ${fmtDate(as_of)}`;

  // 30-day window the spike numbers cover, for drill-through.
  const endDate = as_of;
  const startDate = (() => {
    const d = new Date(as_of + 'T00:00:00');
    d.setDate(d.getDate() - 30);
    return d.toISOString().slice(0, 10);
  })();

  const hasAny = category_spikes.length || new_merchants.length || large_transactions.length;
  if (!hasAny) {
    anomaliesBody.innerHTML = '<div class="empty-state">Nothing unusual detected in the last 30 days.</div>';
    return;
  }

  const wrap = document.createElement('div');
  wrap.className = 'anomaly-wrap';

  // --- Category spikes ---
  if (category_spikes.length) {
    const block = document.createElement('div');
    block.className = 'anomaly-block';
    block.innerHTML = '<div class="anomaly-subtitle">Category Spikes</div>';
    const table = document.createElement('table');
    table.className = 'anomaly-table';
    table.innerHTML = `<thead><tr>
      <th>Category</th>
      <th class="num">Last 30 days</th>
      <th class="num">Prior avg/mo</th>
      <th class="num">Change</th>
    </tr></thead>`;
    const tbody = document.createElement('tbody');
    for (const s of category_spikes) {
      const sign = s.pct_change >= 0 ? '+' : '';
      const tr = document.createElement('tr');
      tr.className = 'anomaly-row-click';
      tr.title = `View ${s.category} transactions, last 30 days`;
      tr.innerHTML = `
        <td>${escHtml(s.category)}</td>
        <td class="num">${fmtCurrency(s.current_30d)}</td>
        <td class="num muted-label">${fmtCurrency(s.prior_monthly_avg)}</td>
        <td class="num pct-pos">${sign}${s.pct_change.toFixed(1)}%</td>`;
      tr.addEventListener('click', () =>
        goToTransactions({ category: s.category, start: startDate, end: endDate }));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    block.appendChild(table);
    wrap.appendChild(block);
  }

  // --- New merchants ---
  if (new_merchants.length) {
    const block = document.createElement('div');
    block.className = 'anomaly-block';
    block.innerHTML = '<div class="anomaly-subtitle">New Merchants <span class="muted-label">(last 45 days, not seen before)</span></div>';
    const table = document.createElement('table');
    table.className = 'anomaly-table';
    table.innerHTML = `<thead><tr>
      <th>Merchant</th><th>Category</th><th class="num">Spent</th><th class="num">Times</th><th>First seen</th>
    </tr></thead>`;
    const tbody = document.createElement('tbody');
    for (const m of new_merchants) {
      const tr = document.createElement('tr');
      tr.className = 'anomaly-row-click';
      tr.title = `View ${m.description} transactions`;
      tr.innerHTML = `
        <td>${escHtml(m.description)}</td>
        <td class="muted-label">${escHtml(m.category || '')}</td>
        <td class="num">${fmtCurrency(m.total_spent)}</td>
        <td class="num muted-label">${m.count}</td>
        <td class="muted-label">${fmtDate(m.first_seen)}</td>`;
      tr.addEventListener('click', () => goToTransactions({ q: m.description }));
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    block.appendChild(table);
    wrap.appendChild(block);
  }

  // --- Large transactions ---
  if (large_transactions.length) {
    const block = document.createElement('div');
    block.className = 'anomaly-block';
    block.innerHTML = '<div class="anomaly-subtitle">Large Transactions <span class="muted-label">(> $1,000, last 45 days)</span></div>';
    const table = document.createElement('table');
    table.className = 'anomaly-table';
    table.innerHTML = `<thead><tr>
      <th>Date</th><th>Description</th><th>Category</th><th class="num">Amount</th>
    </tr></thead>`;
    const tbody = document.createElement('tbody');
    for (const t of large_transactions) {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td class="muted-label">${fmtDate(t.posted)}</td>
        <td>${escHtml(t.description)}</td>
        <td class="muted-label">${escHtml(t.category || '')}</td>
        <td class="num negative">${fmtCurrency(Math.abs(t.amount))}</td>`;
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    block.appendChild(table);
    wrap.appendChild(block);
  }

  anomaliesBody.innerHTML = '';
  anomaliesBody.appendChild(wrap);
}

// ---- Top Merchants ----

btnMerchantsLoad.addEventListener('click', () => loadMerchants());

async function loadMerchants() {
  merchantsBody.innerHTML = '<div class="empty-state"><span class="spinner"></span></div>';
  const params = {
    start:    merchantsStart.value    || null,
    end:      merchantsEnd.value      || null,
    category: merchantsCategory.value || null,
    limit:    50,
  };
  try {
    const data = await apiFetch('/api/merchants' + qs(params));
    renderMerchants(data);
  } catch (err) {
    merchantsBody.innerHTML = `<div class="empty-state">Error: ${escHtml(err.message)}</div>`;
  }
}

function renderMerchants(data) {
  const { merchants } = data;
  if (!merchants || merchants.length === 0) {
    merchantsBody.innerHTML = '<div class="empty-state">No merchant data found for the selected filters.</div>';
    return;
  }

  const maxTotal = Math.max(...merchants.map(m => m.total));

  const table = document.createElement('table');
  table.className = 'merchants-table';
  table.innerHTML = `<thead><tr>
    <th>Merchant</th><th>Category</th><th class="num">Times</th>
    <th class="num">Total</th><th class="mbar-th"></th>
  </tr></thead>`;
  const tbody = document.createElement('tbody');

  for (const m of merchants) {
    const barPct = maxTotal > 0 ? (m.total / maxTotal * 100).toFixed(1) : 0;
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="merchant-name">${escHtml(m.description)}</td>
      <td class="muted-label">${escHtml(m.category || '')}</td>
      <td class="num muted-label">${m.count}</td>
      <td class="num merchant-total">${fmtCurrency(m.total)}</td>
      <td class="mbar-cell"><div class="mbar" style="width:${barPct}%"></div></td>`;
    tbody.appendChild(tr);
  }

  table.appendChild(tbody);
  merchantsBody.innerHTML = '';
  merchantsBody.appendChild(table);
}

// ============================================================
// XSS safety — escape HTML in user-sourced strings
// ============================================================
function escHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ============================================================
// Bootstrap on DOMContentLoaded
// ============================================================
document.addEventListener('DOMContentLoaded', async () => {
  const status = await loadStatus();

  // Load the default tab (Dashboard)
  loadTab('dashboard');

  // Pre-load categories in background so the Transactions tab feels instant
  ensureCategories();
});
