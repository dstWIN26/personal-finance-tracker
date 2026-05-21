# Agent: FRONTEND

## Mission
Build the single-page dashboard. No npm, no build step — just HTML/CSS/JS that loads from CDN. Runs after BACKEND.

---

## Tech Used (all CDN, zero install)
- **Chart.js 4** — bar, line, pie charts
- **Alpine.js 3** — reactive state without a framework
- **Pico.css** — minimal classless CSS (looks great out of the box)

---

## `frontend/index.html`

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Finance Tracker</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/@picocss/pico@2/css/pico.min.css">
    <link rel="stylesheet" href="/static/css/style.css">
</head>
<body x-data="app()" x-init="init()">

<nav class="container-fluid">
    <ul><li><strong>Finance Tracker</strong></li></ul>
    <ul>
        <li><a href="#" @click.prevent="tab='spending'" :class="tab==='spending'?'active':''">Spending</a></li>
        <li><a href="#" @click.prevent="tab='portfolio'" :class="tab==='portfolio'?'active':''">Portfolio</a></li>
        <li><a href="#" @click.prevent="tab='limits'" :class="tab==='limits'?'active':''">Limits</a></li>
    </ul>
</nav>

<main class="container">

    <!-- ── SPENDING TAB ── -->
    <section x-show="tab === 'spending'">
        <div class="grid">
            <div>
                <h2>Spending Overview</h2>
                <small x-text="`Month to date: €${monthTotal.toFixed(2)}`"></small>
            </div>
            <!-- Filters -->
            <div>
                <div class="grid">
                    <input type="month" x-model="filters.month" @change="loadSpending()">
                    <select x-model="filters.category" @change="loadSpending()">
                        <option value="">All categories</option>
                        <template x-for="cat in categories">
                            <option :value="cat" x-text="cat"></option>
                        </template>
                    </select>
                    <select x-model="filters.source" @change="loadSpending()">
                        <option value="">All accounts</option>
                        <option value="revolut">Revolut</option>
                        <option value="trade_republic">Trade Republic</option>
                    </select>
                </div>
            </div>
        </div>

        <div class="grid">
            <article>
                <header>By Category</header>
                <canvas id="categoryChart" height="250"></canvas>
            </article>
            <article>
                <header>Daily Trend</header>
                <canvas id="dailyChart" height="250"></canvas>
            </article>
        </div>

        <article>
            <header>Top Merchants</header>
            <table>
                <thead><tr><th>Merchant</th><th>Count</th><th>Total</th></tr></thead>
                <tbody>
                    <template x-for="m in topMerchants">
                        <tr>
                            <td x-text="m.description || 'Unknown'"></td>
                            <td x-text="m.count"></td>
                            <td x-text="`€${m.total.toFixed(2)}`"></td>
                        </tr>
                    </template>
                </tbody>
            </table>
        </article>

        <article>
            <header>
                All Transactions
                <div style="display:inline-flex; gap:8px; float:right">
                    <input type="number" placeholder="Min €" x-model="filters.min" style="width:80px" @change="loadSpending()">
                    <input type="number" placeholder="Max €" x-model="filters.max" style="width:80px" @change="loadSpending()">
                </div>
            </header>
            <table>
                <thead><tr><th>Date</th><th>Description</th><th>Category</th><th>Source</th><th>Amount</th></tr></thead>
                <tbody>
                    <template x-for="tx in transactions">
                        <tr>
                            <td x-text="tx.date"></td>
                            <td x-text="tx.description"></td>
                            <td><span class="tag" x-text="tx.category"></span></td>
                            <td x-text="tx.source === 'revolut' ? '💳 Revolut' : '📈 TR'"></td>
                            <td :class="tx.amount < 0 ? 'negative' : 'positive'"
                                x-text="`€${Math.abs(tx.amount).toFixed(2)}`"></td>
                        </tr>
                    </template>
                </tbody>
            </table>
        </article>
    </section>

    <!-- ── PORTFOLIO TAB ── -->
    <section x-show="tab === 'portfolio'">
        <div class="grid">
            <article class="stat-card">
                <div class="stat-label">Total Value</div>
                <div class="stat-value" x-text="`€${portfolio.total_value?.toFixed(2) ?? '–'}`"></div>
            </article>
            <article class="stat-card">
                <div class="stat-label">Total P&L</div>
                <div class="stat-value" :class="portfolio.total_pl >= 0 ? 'positive' : 'negative'"
                     x-text="`€${portfolio.total_pl?.toFixed(2) ?? '–'}`"></div>
            </article>
        </div>

        <div class="grid">
            <article>
                <header>🏆 Top 5 Best Performers</header>
                <table>
                    <thead><tr><th>Name</th><th>P&L %</th><th>P&L €</th></tr></thead>
                    <tbody>
                        <template x-for="p in topPerformers.best">
                            <tr>
                                <td x-text="p.name"></td>
                                <td class="positive" x-text="`+${p.pl_pct?.toFixed(2)}%`"></td>
                                <td class="positive" x-text="`+€${p.pl_eur?.toFixed(2)}`"></td>
                            </tr>
                        </template>
                    </tbody>
                </table>
            </article>
            <article>
                <header>📉 Top 5 Worst Performers</header>
                <table>
                    <thead><tr><th>Name</th><th>P&L %</th><th>P&L €</th></tr></thead>
                    <tbody>
                        <template x-for="p in topPerformers.worst">
                            <tr>
                                <td x-text="p.name"></td>
                                <td class="negative" x-text="`${p.pl_pct?.toFixed(2)}%`"></td>
                                <td class="negative" x-text="`€${p.pl_eur?.toFixed(2)}`"></td>
                            </tr>
                        </template>
                    </tbody>
                </table>
            </article>
        </div>

        <article>
            <header>All Positions</header>
            <table>
                <thead><tr><th>Name</th><th>Qty</th><th>Buy Price</th><th>Current</th><th>P&L %</th><th>P&L €</th></tr></thead>
                <tbody>
                    <template x-for="p in portfolio.positions">
                        <tr>
                            <td x-text="p.name"></td>
                            <td x-text="p.quantity"></td>
                            <td x-text="`€${p.buy_price?.toFixed(2)}`"></td>
                            <td x-text="`€${p.current_price?.toFixed(2)}`"></td>
                            <td :class="p.pl_pct >= 0 ? 'positive' : 'negative'"
                                x-text="`${p.pl_pct?.toFixed(2)}%`"></td>
                            <td :class="p.pl_eur >= 0 ? 'positive' : 'negative'"
                                x-text="`€${p.pl_eur?.toFixed(2)}`"></td>
                        </tr>
                    </template>
                </tbody>
            </table>
        </article>
    </section>

    <!-- ── LIMITS TAB ── -->
    <section x-show="tab === 'limits'">
        <article>
            <header>Set Budget Limit</header>
            <div class="grid">
                <input type="text" placeholder="Category (leave blank for total)" x-model="newLimit.category">
                <input type="number" placeholder="Monthly limit (€)" x-model="newLimit.amount">
                <button @click="saveLimit()">Add Limit</button>
            </div>
        </article>

        <template x-for="lim in limitsStatus">
            <article>
                <header>
                    <span x-text="lim.category ? lim.category.toUpperCase() : 'TOTAL SPENDING'"></span>
                    <button class="contrast outline" style="float:right; padding:4px 10px"
                            @click="deleteLimit(lim.id)">Remove</button>
                </header>
                <div class="limit-row">
                    <span x-text="`€${lim.spent.toFixed(2)} / €${lim.amount.toFixed(2)}`"></span>
                    <span :class="lim.pct >= 100 ? 'alert-red' : lim.pct >= 80 ? 'alert-yellow' : ''"
                          x-text="`${lim.pct}%`"></span>
                </div>
                <progress :value="lim.spent" :max="lim.amount"
                          :class="lim.pct >= 100 ? 'limit-exceeded' : lim.pct >= 80 ? 'limit-warning' : ''">
                </progress>
                <small x-show="lim.pct >= 100" class="alert-red">⚠️ Limit exceeded — email alert sent</small>
                <small x-show="lim.pct >= 80 && lim.pct < 100" class="alert-yellow">⚠️ 80% threshold reached</small>
            </article>
        </template>
    </section>

</main>

<script src="https://cdn.jsdelivr.net/npm/alpinejs@3/dist/cdn.min.js" defer></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="/static/js/app.js"></script>
</body>
</html>
```

---

## `frontend/js/app.js`
```javascript
function app() {
    return {
        tab: 'spending',
        transactions: [],
        topMerchants: [],
        categories: [],
        monthTotal: 0,
        portfolio: {},
        topPerformers: { best: [], worst: [] },
        limitsStatus: [],
        newLimit: { category: '', amount: '' },
        filters: {
            month: new Date().toISOString().slice(0, 7),
            category: '',
            source: '',
            min: '',
            max: '',
        },

        async init() {
            await Promise.all([this.loadSpending(), this.loadPortfolio(), this.loadLimits()]);
        },

        async loadSpending() {
            const p = new URLSearchParams({ month: this.filters.month });
            if (this.filters.category) p.append('category', this.filters.category);
            if (this.filters.source)   p.append('source',   this.filters.source);
            if (this.filters.min)      p.append('min_amount', this.filters.min);
            if (this.filters.max)      p.append('max_amount', this.filters.max);

            const [txs, summary, merchants, daily] = await Promise.all([
                fetch(`/spending?${p}`).then(r => r.json()),
                fetch(`/spending/summary?month=${this.filters.month}`).then(r => r.json()),
                fetch(`/spending/top-merchants?month=${this.filters.month}`).then(r => r.json()),
                fetch(`/spending/daily?month=${this.filters.month}`).then(r => r.json()),
            ]);

            this.transactions = txs;
            this.topMerchants = merchants;
            this.monthTotal   = summary.reduce((s, r) => s + r.total, 0);
            this.categories   = [...new Set(txs.map(t => t.category).filter(Boolean))];

            renderCategoryChart(summary);
            renderDailyChart(daily);
        },

        async loadPortfolio() {
            const [p, top] = await Promise.all([
                fetch('/portfolio/').then(r => r.json()),
                fetch('/portfolio/top?n=5').then(r => r.json()),
            ]);
            this.portfolio = p;
            this.topPerformers = top;
        },

        async loadLimits() {
            this.limitsStatus = await fetch('/limits/status').then(r => r.json());
        },

        async saveLimit() {
            if (!this.newLimit.amount) return;
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
        },

        async deleteLimit(id) {
            await fetch(`/limits/${id}`, { method: 'DELETE' });
            await this.loadLimits();
        },
    };
}

let categoryChartInst, dailyChartInst;

function renderCategoryChart(data) {
    if (categoryChartInst) categoryChartInst.destroy();
    const ctx = document.getElementById('categoryChart');
    categoryChartInst = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.map(d => d.category),
            datasets: [{ label: 'Spent (€)', data: data.map(d => d.total),
                backgroundColor: '#4f46e5' }],
        },
        options: { responsive: true, plugins: { legend: { display: false } } },
    });
}

function renderDailyChart(data) {
    if (dailyChartInst) dailyChartInst.destroy();
    const ctx = document.getElementById('dailyChart');
    dailyChartInst = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.map(d => d.date),
            datasets: [{ label: 'Daily spend (€)', data: data.map(d => d.total),
                borderColor: '#10b981', fill: true, backgroundColor: '#10b98120' }],
        },
        options: { responsive: true, plugins: { legend: { display: false } } },
    });
}
```

---

## `frontend/css/style.css`
```css
.positive { color: #10b981; }
.negative { color: #ef4444; }
.alert-red    { color: #e53e3e; font-weight: bold; }
.alert-yellow { color: #d69e2e; font-weight: bold; }

.stat-card { text-align: center; }
.stat-label { color: var(--muted-color); font-size: .85rem; text-transform: uppercase; }
.stat-value { font-size: 2rem; font-weight: bold; margin-top: 4px; }

.tag {
    background: var(--secondary-background);
    border-radius: 4px;
    padding: 2px 8px;
    font-size: .8rem;
}

.limit-row { display: flex; justify-content: space-between; margin-bottom: 6px; }
progress.limit-warning::-webkit-progress-value { background: #d69e2e; }
progress.limit-exceeded::-webkit-progress-value { background: #e53e3e; }

nav a.active { font-weight: bold; border-bottom: 2px solid var(--primary); }
```
