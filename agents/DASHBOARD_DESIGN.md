# Dashboard Design — Overview, Market Monitor & Trading Window

> Design spec produced with the **ui-ux-pro-max** skill. Extends the existing
> neo-bank OLED frontend (`frontend/`, Alpine.js 3 + Chart.js 4, no build step).
> Three new top-level tabs are added alongside Spending / Portfolio / Limits:
> **Overview**, **Markets**, **Trading**.

---

## 1. Design System (carried over + extended)

The skill confirmed **Dark Mode (OLED)** as the correct style for a real-time
fintech/trading product, so the existing palette stays. We only **add** tokens
needed for market data (candlesticks, sparklines, intensity heat).

```css
:root {
    /* — existing, unchanged — */
    --bg-primary:#0a0a0a; --bg-card:#141414; --bg-card-hover:#1a1a1a;
    --bg-input:#1e1e1e;   --bg-elevated:#222;
    --text-primary:#fff;  --text-secondary:#8b8b8b; --text-muted:#555;
    --green:#00d084;      --red:#ff4757;            --amber:#f5a623;

    /* — NEW: market-data tokens — */
    --bull:#26a69a;       --bear:#ef5350;     /* candlesticks (skill-recommended) */
    --bull-dim:#26a69a26; --bear-dim:#ef535026;
    --grid:#1e1e1e;                            /* chart gridlines (low-contrast) */
    --spark-up:#00d084;   --spark-down:#ff4757;
    /* divergent heat scale: cold → neutral → hot */
    --heat-neg2:#ef5350; --heat-neg1:#b3463f; --heat-0:#2a2a2a;
    --heat-pos1:#1f7a63; --heat-pos2:#26a69a;
    --flash-up:#00d08433; --flash-down:#ff475733; /* tick-flash bg on price change */
}
```

**Why two greens/reds?** Portfolio P&L keeps the brand `--green/--red`. OHLC
candles use the calmer `--bull/--bear` so a wall of candles doesn't vibrate.

**Typography.** Keep Inter for UI. Add **tabular figures** everywhere numbers
align in columns — prices, %, basis points — to stop layout jitter on tick:

```css
.num { font-variant-numeric: tabular-nums; font-feature-settings:"tnum"; }
```

Numbers ≥ 10,000 use locale grouping (`1,234,567`); large notionals abbreviate
(`€1.2M`). Times are locale-aware + relative ("updated 12s ago").

**Motion.** Tick flashes ≤ 200ms, ease-out; everything respects
`prefers-reduced-motion` (freeze streaming, no flash). A global **Live/Pause**
control is mandatory for the streaming views (skill: `Real-Time Streaming`).

---

## 2. Navigation

Extend the existing pill top-bar. Six items still fit on desktop; on ≤768px the
bar scrolls horizontally and the active pill stays in view.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  Finance      Overview  Spending  Portfolio  Markets  Trading  Limits  ●Live│
└──────────────────────────────────────────────────────────────────────────┘
                 ▲active                                          ▲ global status
```

`●Live` dot: green = data fresh (< 60s), amber = stale, red = feed error. Click
toggles Live/Pause. It is the single source of truth for "is data moving."

---

## 3. View A — Overview (TR + Revolut unified)

**Goal:** one glance = total net worth + how it splits across the two accounts,
cash vs. invested, and what moved today. This is the home tab.

```
┌─ OVERVIEW ───────────────────────────────────── as of 08 Jun 2026, 14:32 ─┐
│                                                                            │
│  ┌── NET WORTH ───────────────────────────┐  ┌── TODAY ───────────────┐   │
│  │  € 84,210.55            ▲ +€612 +0.73% │  │  Invested  ▲ +€540     │   │
│  │  ╭───────────────────────────────────╮ │  │  Cash      ▲ +€72      │   │
│  │  │   30-day net-worth area sparkline │ │  │  Spending  −€38 today  │   │
│  │  ╰───────────────────────────────────╯ │  └────────────────────────┘   │
│  └────────────────────────────────────────┘                               │
│                                                                            │
│  ┌── ALLOCATION ─────────┐  ┌── ACCOUNTS ────────────────────────────┐    │
│  │      ╭─────╮          │  │  Trade Republic                         │    │
│  │     ╱ donut ╲   Inv   │  │   Invested      € 61,400   ▲ +0.9%     │    │
│  │    │  68/32  │  68% ● │  │   Cash          €  1,210              │    │
│  │     ╲       ╱   Cash  │  │  ─────────────────────────────────────  │    │
│  │      ╰─────╯    32% ● │  │  Revolut                                │    │
│  │  TR ● Revolut ●       │  │   Balance       € 18,900   ▲ +0.4%     │    │
│  │                       │  │   Cash (multi)  €  2,700  $ / £ chips  │    │
│  └───────────────────────┘  └────────────────────────────────────────┘    │
│                                                                            │
│  ┌── RECENT ACTIVITY (unified TR + Revolut ledger) ─────────────────────┐  │
│  │  Today    Rewe              Groceries   Revolut        −€38.20        │  │
│  │  Today    VWCE buy          Invest      Trade Republic −€500.00       │  │
│  │  Yест.    Salary            Income      Revolut       +€3,200.00      │  │
│  │                                                   [ View all → ]      │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

**Components & data**
- **Net Worth hero** = `Σ positions.current_value` (TR) + `Σ balances latest`
  (TR cash + Revolut). Delta vs. yesterday's snapshot. Area sparkline from the
  `balances` time-series (`chart-type: Trend → Area`, fill 20%).
- **Today card** — three stat rows, color by sign, `color-not-only` → arrow glyph.
- **Allocation donut** — invested vs cash, segmented by source. ≤ 4 slices so a
  donut is allowed (skill: `no-pie-overuse` only bites > 5). Legend interactive.
- **Accounts panel** — grouped per source (`field-grouping`). Revolut multi-
  currency shown as small currency chips (€/$/£) with base-currency total.
- **Recent activity** — reuse the existing transactions table component, capped
  at ~8 rows with "View all →" deep-linking into Spending.

**Layout:** desktop = 2-col CSS grid (`grid-2`), hero spans full width; ≤768px
everything stacks single-column, hero → Today → Allocation → Accounts → Activity.

---

## 4. View B — Markets (Market Monitor)

**Goal:** the macro weather report — VIX, US + EU bonds, and the major equity
indices in one scannable board. Read-only, auto-refreshing, glanceable.

```
┌─ MARKETS ──────────────────────────────────── ●Live · updated 9s ago ─────┐
│  ╭ ticker tape: ^GSPC +0.6% · ^NDX +0.9% · ^GDAXI +0.3% · VIX 14.2 -3% … ╮ │
│  ╰──────────────────────────────────────────────────────── scrolling ◀ ─╯ │
│                                                                            │
│  ┌── RISK ────────────┐ ┌── INDICES ──────────────────────────────────┐   │
│  │  VIX               │ │  S&P 500    5,431.２  ▲ +0.61%  ╱╲╱ spark    │   │
│  │      14.2          │ │  Nasdaq100 19,210.4  ▲ +0.92%  ╱╲╱ spark    │   │
│  │   ▼ -3.1%  "calm"  │ │  Dow        38,900.1  ▲ +0.20%  ╱╲╱ spark    │   │
│  │  ╭ gauge 0──50 ╮   │ │  DAX       18,420.7  ▲ +0.31%  ╲╱╲ spark    │   │
│  │  │ green zone  │   │ │  Euro Stoxx 5,021.3  ▲ +0.18%  ╱╲╱ spark    │   │
│  │  ╰─────────────╯   │ │  FTSE 100   8,240.9  ▼ -0.05%  ╲╱╲ spark    │   │
│  │  30d VIX line ╱╲   │ │           (click a row → opens in Trading)   │   │
│  └────────────────────┘ └─────────────────────────────────────────────┘   │
│                                                                            │
│  ┌── BONDS / RATES ──────────────────────────────────────────────────────┐ │
│  │            2Y       5Y      10Y      30Y      Δ1d                       │ │
│  │  US 🇺🇸   4.71%    4.38%   4.29%    4.45%    ▲ +3bp     ╱ yield-curve  │ │
│  │  DE 🇩🇪   2.88%    2.41%   2.52%    2.79%    ▼ -1bp     ╱ mini line    │ │
│  │  ──────────────────────────────────────────────────────────────────   │ │
│  │  US 10Y − DE 10Y spread  177bp   ▲ +4bp                                │ │
│  │  US 10Y − 2Y (inversion) −42bp   ▼  (inverted)                        │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
│                                                                            │
│  ┌── SECTOR / REGION HEATMAP ───────────────────────────────────────────┐  │
│  │  ▓ Tech +1.2  ▓ Fin +0.4  ░ Energy -0.6  ▓ Health +0.3  ░ Util -0.2   │  │
│  │  (divergent red→green tiles, % label inside each — color-not-only)    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

**Components & data**
- **Ticker tape** (skill: `Ticker Tape` secondary of Real-Time Streaming) — thin
  marquee of all watched symbols. Pauses on hover/`prefers-reduced-motion`;
  duplicated content for seamless loop. `aria-hidden` (decorative; data is in
  the cards below).
- **VIX panel** — big number KPI + a **gauge** (0–50, green < 20 / amber 20–30 /
  red > 30) and a 30-day context line. The number, not the gauge, is the source
  of truth (`current value as large text KPI`).
- **Indices list** — one row per index: name, last, % change (signed color +
  arrow), and a **sparkline** (intraday). Whole row is a button → opens that
  symbol in Trading. `number-tabular` so columns stay aligned on tick.
- **Bonds/Rates** — a compact yield matrix (2/5/10/30Y) for **US and DE/EU**, a
  mini yield-curve line per region, plus the two numbers macro people actually
  watch: **US–DE 10Y spread** and **US 2s10s inversion** (flagged red when
  inverted). bp deltas with tabular figures.
- **Heatmap** (skill: `Heatmap/Intensity`) — sector or regional performance as
  divergent tiles. % printed inside each tile so it never relies on color alone;
  legend shows the −/0/+ scale.

**Empty/error/loading:** each card has its own skeleton shimmer and a per-card
"Couldn't load · Retry" state — one dead feed must not blank the whole board
(`error-state-chart`, `empty-data-state`).

**Layout:** desktop grid — Risk (1/3) + Indices (2/3) on row 1, Bonds full-width
row 2, Heatmap full-width row 3. ≤768px: tape → VIX → Indices → Bonds (curve
matrix becomes horizontally scrollable) → Heatmap (wraps to 2-col).

---

## 5. View C — Trading Window

**Goal:** focused deep-dive on one instrument with the full price chart, the
other relevant context rails docked around it. This is the "sit and watch"
screen. Driven by a symbol selected here or handed off from Markets.

```
┌─ TRADING ──────────────────────────────────────────── ●Live · ⏸ Pause ────┐
│  ┌ WATCHLIST ─┐ ┌── S&P 500  ·  ^GSPC ───────────────────────────────────┐ │
│  │ ^GSPC  ▲.6 │ │  5,431.20   ▲ +32.9  +0.61%        [1D 1W 1M 3M 1Y MAX]│ │
│  │ ^NDX   ▲.9 │ │  O 5,402  H 5,440  L 5,398  Prev 5,398.3   Vol 2.1B    │ │
│  │ ^GDAXI ▲.3 │ │                                                         │ │
│  │ VIX   ▼3.1 │ │   ┌───────────────────────────────────────────────┐    │ │
│  │ US10Y ▲3bp │ │   │                                               │    │ │
│  │ DE10Y ▼1bp │ │   │        CANDLESTICK  (bull #26a69a /           │    │ │
│  │ ─────────  │ │   │                      bear #ef5350)            │    │ │
│  │ + add      │ │   │        crosshair + OHLC tooltip on hover      │    │ │
│  │            │ │   │                                               │    │ │
│  │ (click to  │ │   ├───────────────────────────────────────────────┤    │ │
│  │  load into │ │   │   volume bars (40% opacity)                   │    │ │
│  │  main)     │ │   └───────────────────────────────────────────────┘    │ │
│  │            │ │   timeframe ▲   ·   ⤢ line/candle toggle              │ │
│  └────────────┘ └─────────────────────────────────────────────────────────┘ │
│                                                                            │
│  ┌── CONTEXT RAIL (right or below) ─────────────────────────────────────┐  │
│  │  VIX 14.2 ▼  · US 2s10s −42bp · US–DE 177bp · DXY 104.1 · Brent 82.4 │  │
│  │  Day range  ├─────────●────────────┤  52w range ├──────────●───────┤ │  │
│  │  Your position: 12 sh · avg €5,120 · ▲ +€3,720 (+6.2%)   [if held]  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

**Components & data**
- **Watchlist rail** — persistent left column of pinned symbols (indices, VIX,
  key yields). Active row highlighted (`nav-state-active`). Click → loads main
  chart. Drag to reorder; "+ add" to append. State persisted to localStorage.
- **Instrument header** — name + symbol, big last price with **tick-flash**
  (green/red bg pulse on change), signed change abs + %, and the OHLC + prev
  close + volume strip. `[1D 1W 1M 3M 1Y MAX]` timeframe segmented control.
- **Main chart** = **Candlestick** with volume subpanel (skill: `Stock/Trading
  OHLC`). Canvas-rendered, max ~500 candles visible, crosshair + OHLC tooltip,
  line/candle toggle. Non-financial-friendly fallback: a line view + an OHLC
  data table behind a toggle (`A11y Fallback`).
- **Context rail** — the cross-asset numbers that explain the move: VIX, 2s10s,
  US–DE spread, DXY, Brent. Plus **day-range / 52-week-range** position bars and
  — if the instrument is something you actually hold in the TR portfolio — your
  position P&L inline (ties Trading back to Portfolio data).
- **Live/Pause** governs streaming; paused = chart frozen, header shows last
  value + "paused" badge. Required by `Real-Time Streaming` rules.

**Layout:** desktop = watchlist (left, ~220px) + chart (center, fluid) + context
(right rail OR docked under chart on narrower desktops). ≤768px: header → chart
(reduced ticks) → context (horizontal scroll chips) → watchlist collapses to a
top dropdown/symbol-search.

---

## 6. Chart inventory (Chart.js mapping)

| View | Element | Chart type | Notes |
|------|---------|-----------|-------|
| Overview | Net-worth trend | Area (line + 20% fill) | from `balances` series |
| Overview | Allocation | Donut | ≤4 slices, interactive legend |
| Markets | Ticker tape | CSS marquee | decorative, `aria-hidden` |
| Markets | VIX | Gauge + context line | number is source of truth |
| Markets | Index rows | Sparkline (line, no axes) | one per row, intraday |
| Markets | Yield curve | Mini multi-point line | per region (US, DE) |
| Markets | Sectors | Heatmap (divergent) | % label in tile |
| Trading | Main | Candlestick + volume | Canvas; `chartjs-chart-financial` plugin |
| Trading | Ranges | Range/position bars | day + 52w |

**Library note:** Chart.js core covers line/area/bar/doughnut/sparkline/gauge
(via doughnut). Candlesticks need the CDN plugin **`chartjs-chart-financial`**
(+ a date adapter, e.g. `chartjs-adapter-luxon`) — both load from CDN, keeping
the **no-build** constraint. Every `renderXChart()` destroys its prior instance,
matching the existing `app.js` pattern.

---

## 7. Backend gaps (not yet built)

Markets & Trading need market data the current backend doesn't expose. Suggested
new read-only routes (mirrors the existing `routes/` style, cached server-side
to respect provider rate limits):

```
GET /market/quotes?symbols=^GSPC,^NDX,^GDAXI,^VIX   → last, change, change_pct, spark[]
GET /market/bonds                                    → US & DE tenors + spreads
GET /market/history?symbol=^GSPC&range=1M&interval=1d→ OHLCV[] for candlesticks
GET /market/heatmap?group=sector                     → tiles [{name, pct}]
```

A scheduler job refreshes quotes (e.g. every 30–60s during market hours) into a
small `market_cache` table; the frontend polls these endpoints on the Live tick.
Provider choice (free tier: Stooq / Yahoo unofficial / Twelve Data / Finnhub) is
an integration decision for `INTEGRATIONS.md` — out of scope for this layout spec.

---

## 8. Accessibility & polish checklist (per skill pre-delivery)

- [ ] All P&L / change values pair color **with an arrow/sign** (`color-not-only`).
- [ ] Tabular figures on every numeric column; no width jitter on tick.
- [ ] `prefers-reduced-motion`: freeze ticker, streaming, and flashes.
- [ ] Mandatory **Live/Pause** control for all streaming views.
- [ ] Per-card skeleton + "Retry" error state; no full-board blanking.
- [ ] Candlestick view has line-fallback + sortable OHLC data table.
- [ ] Touch: index/watchlist rows ≥ 44px tap height; heatmap tiles tappable.
- [ ] Charts: visible legend, hover/tap tooltip with exact values, gridlines low-contrast.
- [ ] Focus rings on all rows/segmented controls; keyboard-navigable watchlist.
- [ ] Verify contrast of `--bull/--bear` text on `#141414` ≥ 3:1 (large) / 4.5:1 (small).
- [ ] Responsive at 375 / 768 / 1024 / 1440.
```
