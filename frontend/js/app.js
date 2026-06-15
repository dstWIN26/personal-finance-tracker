function app() {
    return {
        tab: 'overview',
        transactions: [],
        topMerchants: [],
        categories: [],
        monthTotal: 0,
        portfolio: { positions: [], total_value: 0, total_pl: 0 },
        topPerformers: { best: [], worst: [] },
        limitsStatus: [],
        newLimit: { category: '', amount: '' },
        loading: false,
        error: null,
        filters: {
            month: new Date().toISOString().slice(0, 7),
            category: '',
            source: '',
            min: '',
            max: '',
        },

        // ── Overview + market state ──
        ov: { net_worth: 0, invested: 0, invested_pl: 0, cash: 0, today_spend: 0,
              allocation: { invested: 0, cash: 0 }, accounts: [], trend: [], recent: [] },
        indices: [],
        vix: null,
        bonds: { us: null, eu: null, spreads: {} },
        heatmap: [],
        watchlist: [],
        activeSymbol: '^GSPC',
        active: null,
        range: '1mo',
        live: true,
        feedAge: null,
        feedError: false,
        _lastFetch: null,
        _pollTimer: null,
        _ageTimer: null,

        async init() {
            // Honour ?tab= (the bank-link callback returns to /?tab=settings).
            const forcedTab = new URLSearchParams(location.search).get('tab');
            if (forcedTab) this.tab = forcedTab;
            await Promise.all([
                this.loadSpending(),
                this.loadPortfolio(),
                this.loadLimits(),
                this.loadOverview(),
                this.loadMarkets(),
                this.loadProfile(),
            ]);
            await this.loadTrading();
            if (forcedTab === 'settings') this.loadSettings();
            else if (!forcedTab && this.profile.default_tab && this.profile.default_tab !== 'overview') {
                this.tab = this.profile.default_tab;
            }
            this.startPolling();
            this._ageTimer = setInterval(() => {
                if (this._lastFetch) this.feedAge = Math.round((Date.now() - this._lastFetch) / 1000);
            }, 1000);
        },

        async loadSpending() {
            this.loading = true;
            this.error = null;
            try {
                const p = new URLSearchParams({ month: this.filters.month });
                if (this.filters.category) p.append('category', this.filters.category);
                if (this.filters.source)   p.append('source',   this.filters.source);
                if (this.filters.min)      p.append('min_amount', this.filters.min);
                if (this.filters.max)      p.append('max_amount', this.filters.max);

                const [txs, summary, merchants, daily] = await Promise.all([
                    fetch(`/spending?${p}`).then(r => r.ok ? r.json() : []),
                    fetch(`/spending/summary?month=${this.filters.month}`).then(r => r.ok ? r.json() : []),
                    fetch(`/spending/top-merchants?month=${this.filters.month}`).then(r => r.ok ? r.json() : []),
                    fetch(`/spending/daily?month=${this.filters.month}`).then(r => r.ok ? r.json() : []),
                ]);

                this.transactions = txs;
                this.topMerchants = merchants;
                this.monthTotal   = summary.reduce((s, r) => s + r.total, 0);
                this.categories   = [...new Set(txs.map(t => t.category).filter(Boolean))];

                renderCategoryChart(summary);
                renderDailyChart(daily);
            } catch (e) {
                this.error = 'Failed to load spending data.';
            } finally {
                this.loading = false;
            }
        },

        async loadPortfolio() {
            try {
                const [p, top] = await Promise.all([
                    fetch('/portfolio/').then(r => r.ok ? r.json() : { positions: [], total_value: 0, total_pl: 0 }),
                    fetch('/portfolio/top?n=5').then(r => r.ok ? r.json() : { best: [], worst: [] }),
                ]);
                this.portfolio     = p;
                this.topPerformers = top;
            } catch (e) {
                console.error('Portfolio load failed', e);
            }
        },

        async loadLimits() {
            try {
                this.limitsStatus = await fetch('/limits/status').then(r => r.ok ? r.json() : []);
            } catch (e) {
                console.error('Limits load failed', e);
            }
        },

        async saveLimit() {
            if (!this.newLimit.amount) return;
            try {
                await fetch('/limits/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        category: this.newLimit.category || null,
                        amount: parseFloat(this.newLimit.amount),
                    }),
                });
                this.newLimit = { category: '', amount: '' };
                await this.loadLimits();
            } catch (e) {
                console.error('Save limit failed', e);
            }
        },

        async deleteLimit(id) {
            if (!confirm('Remove this budget limit?')) return;
            try {
                await fetch(`/limits/${id}`, { method: 'DELETE' });
                await this.loadLimits();
            } catch (e) {
                console.error('Delete limit failed', e);
            }
        },

        // ═══════════════ OVERVIEW ═══════════════
        async loadOverview() {
            try {
                this.ov = await fetch('/overview/').then(r => r.ok ? r.json() : this.ov);
                this.$nextTick(() => {
                    renderNetWorthSpark(this.ov.trend);
                    renderAllocChart(this.ov.allocation);
                });
            } catch (e) { console.error('Overview load failed', e); }
        },

        // ═══════════════ MARKETS ═══════════════
        async loadMarkets() {
            try {
                const [indices, vix, bonds, heatmap] = await Promise.all([
                    fetch('/market/indices').then(r => r.ok ? r.json() : []),
                    fetch('/market/vix').then(r => r.ok ? r.json() : null),
                    fetch('/market/bonds').then(r => r.ok ? r.json() : this.bonds),
                    fetch('/market/heatmap').then(r => r.ok ? r.json() : []),
                ]);
                this.indices = indices;
                this.vix = (vix && vix.price != null) ? vix : null;
                this.bonds = bonds;
                this.heatmap = heatmap;
                this.feedError = !indices.length;
                this._lastFetch = Date.now(); this.feedAge = 0;
                this.$nextTick(() => renderIndexSparks(this.indices));
            } catch (e) {
                this.feedError = true;
                console.error('Markets load failed', e);
            }
        },

        // ═══════════════ TRADING ═══════════════
        async loadTrading() {
            try {
                const wl = await fetch('/market/watchlist').then(r => r.ok ? r.json() : []);
                this.watchlist = wl;
                await this.loadActive();
            } catch (e) { console.error('Trading load failed', e); }
        },

        async loadActive() {
            try {
                const hist = await fetch(`/market/history?symbol=${encodeURIComponent(this.activeSymbol)}&range=${this.range}`)
                    .then(r => r.ok ? r.json() : null);
                if (hist) {
                    this.active = { name: hist.name, symbol: hist.symbol, meta: hist.meta,
                                    price: hist.meta ? hist.meta.price : null,
                                    change: hist.meta ? hist.meta.change : null,
                                    change_pct: hist.meta ? hist.meta.change_pct : null };
                    this.$nextTick(() => renderCandle(hist.candles, this.range));
                }
            } catch (e) { console.error('Active symbol load failed', e); }
        },

        selectSymbol(sym) { this.activeSymbol = sym; this.loadActive(); },
        openTrading(sym) { this.tab = 'trading'; this.selectSymbol(sym); },
        setRange(r) { this.range = r; this.loadActive(); },

        // ── Live / pause polling ──
        startPolling() {
            this.stopPolling();
            const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            this._pollTimer = setInterval(() => {
                if (!this.live || document.hidden) return;
                this.loadMarkets();
                if (this.tab === 'trading') this.loadActive();
            }, reduced ? 60000 : 30000);
        },
        stopPolling() { if (this._pollTimer) clearInterval(this._pollTimer); },
        toggleLive() {
            this.live = !this.live;
            if (this.live) { this.loadMarkets(); this.startPolling(); }
        },

        // ── Formatting helpers ──
        eur(v)  {
            if (v == null) return '—';
            const sym = { EUR: '€', USD: '$', GBP: '£', CHF: 'CHF ' }[this.profile.base_currency] || '€';
            return sym + Number(v).toLocaleString(this.profile.locale || 'en-IE', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
        },
        fmt(v)  { return v == null ? '—' : Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); },
        signPct(v) { return v == null ? '—' : `${v >= 0 ? '▲' : '▼'} ${Math.abs(v).toFixed(2)}%`; },
        pct(part, whole) { return (!whole) ? '0%' : `${Math.round(part / whole * 100)}%`; },
        bp(v)   { return v == null ? '—' : v.toFixed(2) + '%'; },
        bpDelta(v) { return v == null ? '—' : `${v >= 0 ? '+' : ''}${v}bp`; },
        vixZone(v) { return v == null ? '' : v < 20 ? 'calm' : v < 30 ? 'warn' : 'fear'; },
        vixLabel(v) { return v == null ? '' : v < 20 ? 'calm' : v < 30 ? 'elevated' : 'fear'; },
        heatColor(p) {
            if (p == null) return 'var(--heat-0)';
            const c = Math.max(-2, Math.min(2, p)) / 2;       // clamp ±2% → ±1
            return c >= 0
                ? `rgba(38,166,154,${0.15 + c * 0.55})`
                : `rgba(239,83,80,${0.15 + (-c) * 0.55})`;
        },
        range52(m) {
            if (!m || m.week52_low == null || m.week52_high == null || m.week52_high === m.week52_low) return 50;
            return Math.max(0, Math.min(100, (m.price - m.week52_low) / (m.week52_high - m.week52_low) * 100));
        },
        nowLabel() { return new Date().toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); },

        // ── Account ──
        sessions: [],
        openSessions() { this.tab = 'security'; this.loadSessions(); },
        async loadSessions() {
            try { this.sessions = await fetch('/auth/sessions').then(r => r.ok ? r.json() : []); }
            catch (e) { console.error('Sessions load failed', e); }
        },
        async revokeSession(id) {
            try { await fetch(`/auth/sessions/${id}`, { method: 'DELETE' }); await this.loadSessions(); }
            catch (e) { console.error('Revoke failed', e); }
        },
        async revokeOthers() {
            if (!confirm('Sign out all other devices? They will need to log in again.')) return;
            try { await fetch('/auth/sessions/revoke-others', { method: 'POST' }); await this.loadSessions(); }
            catch (e) { console.error('Revoke-others failed', e); }
        },
        async logout() {
            try { await fetch('/auth/logout', { method: 'POST' }); } catch (e) { /* ignore */ }
            location.href = '/login';
        },
        async addPasskey() {
            try {
                await webauthnRegister(navigator.platform || 'This device');
                alert('Passkey enrolled on this device.');
            } catch (e) { alert('Could not enrol passkey: ' + (e.message || e)); }
        },

        // ── Settings & Profile ──
        integrations: { trade_republic: {}, enable_banking: {}, salt_edge_legacy: {}, banks: [] },
        profile: { display_name: '', base_currency: 'EUR', locale: 'en-IE', default_tab: 'overview' },
        aspsps: [], aspspQuery: '', aspspCountry: 'DE', aspspLoading: false, showPicker: false, settingsMsg: '',

        openSettings() { this.tab = 'settings'; this.settingsMsg = ''; this.loadSettings(); },
        openProfile()  { this.tab = 'profile';  this.settingsMsg = ''; this.loadProfile(); },

        async loadSettings() {
            try { this.integrations = await fetch('/settings/integrations').then(r => r.ok ? r.json() : this.integrations); }
            catch (e) { console.error('Settings load failed', e); }
        },

        // Pre-fill the picker for a named bank (country codes vary per bank, so we
        // let the user click the exact aggregator entry rather than guessing one).
        quickPick(name) { this.aspspQuery = name; this.showPicker = true; if (!this.aspsps.length) this.loadAspsps(); },

        async loadAspsps() {
            const c = (this.aspspCountry || '').trim().toUpperCase();
            if (c.length !== 2) { this.aspsps = []; return; }
            this.aspspLoading = true;
            try {
                const d = await fetch(`/settings/banks/aspsps?country=${encodeURIComponent(c)}`).then(r => r.ok ? r.json() : { aspsps: [] });
                this.aspsps = d.aspsps || [];
            } catch (e) { this.aspsps = []; }
            finally { this.aspspLoading = false; }
        },
        filteredAspsps() {
            const q = (this.aspspQuery || '').toLowerCase();
            return this.aspsps.filter(a => !q || (a.name || '').toLowerCase().includes(q)).slice(0, 50);
        },

        async connectBank(name, country) {
            this.settingsMsg = 'Starting bank authorisation…';
            try {
                const r = await fetch('/settings/banks/connect', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ aspsp_name: name, country: country }),
                });
                const d = await r.json().catch(() => ({}));
                if (r.ok && d.url) { window.location = d.url; }
                else { this.settingsMsg = d.detail || 'Could not start linking.'; }
            } catch (e) { this.settingsMsg = 'Network error starting the link.'; }
        },
        async unlinkBank(id) {
            if (!confirm('Unlink this bank? Already-synced data stays, but it stops updating.')) return;
            try { await fetch(`/settings/banks/${id}`, { method: 'DELETE' }); await this.loadSettings(); }
            catch (e) { console.error('Unlink failed', e); }
        },

        async loadProfile() {
            try { const p = await fetch('/settings/profile').then(r => r.ok ? r.json() : null); if (p) this.profile = p; }
            catch (e) { console.error('Profile load failed', e); }
        },
        async saveProfile() {
            this.settingsMsg = 'Saving…';
            try {
                const r = await fetch('/settings/profile', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.profile),
                });
                this.settingsMsg = r.ok ? 'Saved.' : 'Could not save.';
            } catch (e) { this.settingsMsg = 'Could not save.'; }
        },

        sourceLabel(src) {
            const map = { trade_republic: 'Trade Republic', revolut: 'Revolut', deutsche_bank: 'Deutsche Bank' };
            if (map[src]) return map[src];
            if (!src) return '—';
            return String(src).replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        },
    };
}

/* Any 401 from the API (expired/closed session) → bounce to the login page. */
(function () {
    const _fetch = window.fetch;
    window.fetch = async (...args) => {
        const res = await _fetch(...args);
        if (res.status === 401 && !location.pathname.startsWith('/login')) {
            location.href = '/login';
        }
        return res;
    };
})();

/* ── Chart.js global defaults for dark theme ── */
Chart.defaults.color = '#8b8b8b';
Chart.defaults.borderColor = '#1e1e1e';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 12;

let categoryChartInst, dailyChartInst;

function renderCategoryChart(data) {
    if (categoryChartInst) categoryChartInst.destroy();
    const ctx = document.getElementById('categoryChart');
    if (!ctx) return;
    categoryChartInst = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d.category || 'other'),
            datasets: [{
                label: 'Spent',
                data: data.map(d => d.total),
                backgroundColor: '#ffffff',
                borderRadius: 6,
                maxBarThickness: 32,
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#555', font: { size: 11 } },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: '#1e1e1e' },
                    ticks: { color: '#555', callback: v => `€${v}` },
                },
            },
        },
    });
}

function renderDailyChart(data) {
    if (dailyChartInst) dailyChartInst.destroy();
    const ctx = document.getElementById('dailyChart');
    if (!ctx) return;
    dailyChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.map(d => d.date.slice(5)),  // show MM-DD only
            datasets: [{
                label: 'Daily spend',
                data: data.map(d => d.total),
                borderColor: '#00d084',
                borderWidth: 2,
                fill: true,
                backgroundColor: createGradient(ctx),
                tension: 0.35,
                pointRadius: 0,
                pointHitRadius: 12,
                pointHoverRadius: 4,
                pointHoverBackgroundColor: '#00d084',
            }],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { color: '#555', font: { size: 11 }, maxTicksLimit: 10 },
                },
                y: {
                    beginAtZero: true,
                    grid: { color: '#1e1e1e' },
                    ticks: { color: '#555', callback: v => `€${v}` },
                },
            },
        },
    });
}

function createGradient(canvas) {
    const ctx2d = canvas.getContext('2d');
    const gradient = ctx2d.createLinearGradient(0, 0, 0, canvas.height || 220);
    gradient.addColorStop(0, 'rgba(0, 208, 132, 0.25)');
    gradient.addColorStop(1, 'rgba(0, 208, 132, 0.00)');
    return gradient;
}

/* ════════════ Overview + Market charts ════════════ */
const _noMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let netWorthInst, allocInst, candleInst;
const sparkInsts = {};

function renderNetWorthSpark(trend) {
    if (netWorthInst) netWorthInst.destroy();
    const ctx = document.getElementById('netWorthSpark');
    if (!ctx || !trend) return;
    netWorthInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels: trend.map(d => d.day),
            datasets: [{
                data: trend.map(d => d.total),
                borderColor: '#00d084', borderWidth: 2, fill: true,
                backgroundColor: createGradient(ctx), tension: 0.35,
                pointRadius: 0, pointHitRadius: 12,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            animation: !_noMotion(),
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => '€' + c.parsed.y.toFixed(2) } } },
            scales: { x: { display: false }, y: { display: false } },
        },
    });
}

function renderAllocChart(alloc) {
    if (allocInst) allocInst.destroy();
    const ctx = document.getElementById('allocChart');
    if (!ctx) return;
    allocInst = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Invested', 'Cash'],
            datasets: [{
                data: [alloc.invested || 0, alloc.cash || 0],
                backgroundColor: ['#00d084', '#8b8b8b'],
                borderColor: '#141414', borderWidth: 3,
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false, cutout: '68%',
            animation: !_noMotion(),
            plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `${c.label}: €${c.parsed.toFixed(2)}` } } },
        },
    });
}

function renderIndexSparks(indices) {
    indices.forEach(q => {
        const ctx = document.getElementById(`spark-${q.symbol}`);
        if (!ctx || !q.spark || q.spark.length < 2) return;
        if (sparkInsts[q.symbol]) sparkInsts[q.symbol].destroy();
        const up = q.change_pct >= 0;
        sparkInsts[q.symbol] = new Chart(ctx, {
            type: 'line',
            data: {
                labels: q.spark.map((_, i) => i),
                datasets: [{
                    data: q.spark,
                    borderColor: up ? '#00d084' : '#ff4757',
                    borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false,
                }],
            },
            options: {
                responsive: false, animation: false,
                plugins: { legend: { display: false }, tooltip: { enabled: false } },
                scales: { x: { display: false }, y: { display: false } },
                elements: { line: { borderCapStyle: 'round' } },
            },
        });
    });
}

function renderCandle(candles, range) {
    if (candleInst) candleInst.destroy();
    const ctx = document.getElementById('candleChart');
    if (!ctx || !candles || !candles.length) return;
    const data = candles.map(c => ({ x: c.t, o: c.o, h: c.h, l: c.l, c: c.c }));
    const unit = (range === '1d' || range === '5d') ? 'hour'
        : (range === '1y' || range === 'max') ? 'month' : 'day';
    candleInst = new Chart(ctx, {
        type: 'candlestick',
        data: {
            datasets: [{
                label: 'OHLC',
                data,
                color: { up: '#26a69a', down: '#ef5350', unchanged: '#8b8b8b' },
                borderColor: { up: '#26a69a', down: '#ef5350', unchanged: '#8b8b8b' },
            }],
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            animation: !_noMotion(),
            plugins: { legend: { display: false } },
            scales: {
                x: { type: 'time', time: { unit }, grid: { color: '#1e1e1e' }, ticks: { color: '#555', maxTicksLimit: 8 } },
                y: { position: 'right', grid: { color: '#1e1e1e' }, ticks: { color: '#555' } },
            },
        },
    });
}
