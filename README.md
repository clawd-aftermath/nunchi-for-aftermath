<p align="center">
  <img src="assets/logo.png" alt="Nunchi" width="480" />
</p>

<h3 align="center">Autonomous Trading Agent for Hyperliquid</h3>

<p align="center">
  14 strategies &bull; APEX multi-slot orchestrator &bull; REFLECT nightly review &bull; MCP server &bull; Agent Skills
</p>

<p align="center">
  <a href="https://docs.nunchi.trade"><strong>Docs</strong></a> &nbsp;&bull;&nbsp;
  <a href="https://yex.nunchi.trade"><strong>App</strong></a> &nbsp;&bull;&nbsp;
  <a href="https://research.nunchi.trade"><strong>Research</strong></a> &nbsp;&bull;&nbsp;
  <a href="https://discord.gg/nunchi"><strong>Discord</strong></a> &nbsp;&bull;&nbsp;
  <a href="https://x.com/nunchi"><strong>X</strong></a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white" alt="Python" />
  <img src="https://img.shields.io/badge/strategies-14-C9A84C" alt="Strategies" />
  <img src="https://img.shields.io/badge/tests-483%20passing-brightgreen" alt="Tests" />
  <img src="https://img.shields.io/badge/license-MIT-blue" alt="License" />
  <img src="https://img.shields.io/badge/MCP-24%20tools-8A2BE2" alt="MCP" />
</p>

<p align="center">
  <a href="https://auth.nunchi.trade">
    <img src="https://img.shields.io/badge/Launch%20via-Nunchi%20Auth-C9A84C" alt="Launch via Nunchi Auth" height="36" />
  </a>
</p>

---

Ship market-making, momentum, arbitrage, and LLM-powered strategies on [Hyperliquid](https://hyperliquid.xyz) perps and [YEX](https://yex.nunchi.trade) yield markets. Full autonomous stack: Guard trailing stops, Radar opportunity screening, Pulse momentum detection, APEX orchestrator, REFLECT performance review. Works as a standalone CLI, a [Claude Code](https://docs.anthropic.com/en/docs/claude-code) skill, an [OpenClaw](https://agentskills.io) AgentSkill, or an MCP server.

---

## Quick Start

```bash
git clone https://github.com/Nunchi-trade/agent-cli.git && cd agent-cli
bash scripts/bootstrap.sh        # Creates venv, installs, validates
```

### Agent-Friendly (Zero Prompts)

```bash
hl wallet auto --save-env        # Create wallet + save creds (no prompts)
hl setup claim-usdyp             # Claim testnet USDyP
hl builder approve               # Approve builder fee (one-time)
hl run avellaneda_mm --mock --max-ticks 3   # Validate
hl apex run --mock --max-ticks 5            # Full pipeline test
```

### Manual Setup

```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=true           # default

hl setup check                   # Validate environment
hl builder approve               # Approve builder fee
hl run engine_mm -i ETH-PERP --tick 10
```

### Mainnet

```bash
export HL_PRIVATE_KEY=0x...
export HL_TESTNET=false

hl builder approve --mainnet
hl run engine_mm -i ETH-PERP --tick 10 --mainnet
hl apex run --mainnet
```

### Funding Hedge

Propose a read-only BTCSWP funding-rate hedge from the CLI or any MCP client. The default `hl hedge propose` path reads the current account position; passing `--perp-notional` switches to pure sizing mode with no account fetch or order execution.

```bash
hl hedge info --json
hl hedge propose --asset BTC --side long --perp-notional 150000 --funding-apr 42
hl hedge propose --asset BTC --side long --perp-notional 150000 --funding-rate-8h 0.0003 --json
hl hedge backtest --csv funding.csv --asset BTC --side long --perp-notional 150000
```

Backtest CSVs need a `funding_rate_8h`, `perp_funding_rate_8h`, `funding_rate`, or `rate` column. Add `hedge_rate_8h`, `btcswp_rate_8h`, or `btcswp_funding_rate_8h` when you have realized BTCSWP rates; otherwise the backtest uses an idealized offset.

MCP tools: `funding_hedge_info`, `funding_hedge_propose`, `funding_hedge_backtest`

---

## Aftermath Finance (Sui)

Every strategy in this repository runs on Aftermath Finance perpetuals on Sui.
There is no Hyperliquid in the Aftermath execution path — Aftermath is the
venue, not a facade over another one.

One adapter (`cli/af/`) exposes the same interface the engine already used, so
**strategy code is unchanged**. Add a wallet and go.

> **Nothing is armed.** Strategies ship disabled and this build carries no
> signer, so no transaction can be signed or submitted. See
> [Arming](#arming) below.

### Quickstart

```bash
# 1. Install dependencies (base + the additive Aftermath extras)
pip install -r requirements.txt
pip install -r requirements-af.txt

# 2. Configure — copy the template and edit one line
cp .env.example .env
#    set AF_WALLET_ADDRESS to your Sui address

# 3. Preflight. This is the first thing to run; it exits non-zero on failure.
nunchi doctor

# 4. Run any strategy offline — no network, no keys
nunchi af simple_mm -i ETH-AF-PERP --mock --max-ticks 5
```

`nunchi doctor` prints a pass/fail table covering the API host, spec
compatibility, wallet, account discovery, gas mode and its prerequisites,
market resolution and arming posture.

### The API

| | |
|---|---|
| Base URL | `https://v2-preview.aftermath.finance` |
| Override | `AF_API_BASE_URL` |
| Defined in | `cli/af/config.py` — **the one host constant** |

That hostname **is production mainnet**, despite reading like a staging
environment. The legacy v1 API host is retired and no longer serves the API at
all, so anything still pointed at it fails silently rather than loudly.

Every call site reads the single constant, and
`tests/test_af_v2_hosts.py` fails the build if a second hostname appears
anywhere in the tree.

### Gas is your choice

One setting, three modes. The default requires no SUI at all.

| `AF_GAS_MODE` | Who pays | Needs |
|---|---|---|
| `sponsored` *(default)* | A gas pool, via `/api/gas-pool/*` | A pool granted to your wallet |
| `self` | Your own SUI | SUI in the wallet |
| `dynamic` | Any coin you choose, via `/api/dynamic-gas` | `AF_GAS_COIN_TYPE` |

In `dynamic` mode you also choose **which coin pays** — set `AF_GAS_COIN_TYPE`
to e.g. the USDC coin type to pay gas in USDC.

The gas budget is always set explicitly (`AF_GAS_BUDGET_MIST`); auto-estimation
under-counts the storage cost of created objects and surfaces later as
`InsufficientGas` on a transaction that simulated cleanly. `doctor` validates
the active mode's prerequisites and never silently switches modes.

### Instrument naming

| Canonical | Also accepted |
|---|---|
| `ETH-AF-PERP` | `ETH-PERP`, `ETH`, `ETH/USDC:USDC` |
| `BTC-AF-PERP` | `BTC-PERP`, `BTC` |

Market **ids** are on-chain object ids resolved from
`/api/perpetuals/all-markets` and validated strictly by the API. They are never
constructed from a ticker, and there is no dex-local asset-id arithmetic or
`xyz:` namespace prefix — those are Hyperliquid concepts and are gone.

### Isolated margin

Aftermath is **isolated margin**. Unallocated account collateral protects
nothing:

```
wallet USDC -> deposit -> account (unallocated) -> ALLOCATE -> position margin
```

The adapter performs allocation itself (`_ensure_collateral_allocated`), so
strategies written against a cross-margin venue keep working without knowing
about it.

### Atomic operations

The adapter uses Aftermath's multi-operation primitives rather than issuing one
transaction per action:

| Operation | Transactions | Why |
|---|---|---|
| Requote (cancel + replace) | **1** | `cancel-and-place-orders`. A split cancel-then-place leaves the strategy unquoted or double-quoted in between, and can fail halfway. |
| Price ladder | **1** | `place-scale-order`. N placements costs N gas payments and can leave a half-built grid. |
| Cancel batch | **1** | `cancel-orders` takes a market→orders map. |

### Arming

Nothing trades until you say so, explicitly:

```bash
export AF_ARMED=true      # and wire a signer
```

Every mutating path funnels through one method (`AftermathProxy._submit`), so
there is exactly one place enforcing this and exactly one place to audit.
Builds, previews and inspections all run normally while disarmed — which is
what makes the pipeline verifiable without broadcasting anything.

### Transaction pipeline

```
build -> preview gate -> INSPECT -> (sign) -> reconcile
```

- **Preview gate** — where a `previews/*` counterpart exists it runs first, and
  a preview error blocks the build entirely. Previews can return HTTP 200 with
  an error body, so they are parsed as tagged unions and fail closed.
- **Inspection** — a builder response is untrusted input. Sender, gas
  configuration, sponsorship and target package are all checked before a
  signature could exist. The gate is structural: `sign_inspected()` accepts
  only an `InspectedTx`, and only `inspect()` can produce one.
- **Reconcile** — a 200 from submit means "accepted", not "applied". State is
  re-read and compared against intent.

### Safety, in the adapter

These live below the strategy layer so they apply to every strategy at once and
cannot be bypassed by one that forgets to check:

- **Margin health zones** off the position's API-reported `marginRatio` against
  `marginRatioMaintenance` — `>2x` safe, `1.5–2x` warning, `1–1.5x` danger,
  `<1x` liquidation.
- **Two-tier circuit breakers** — soft limits warn, hard limits halt, and a
  tripped breaker stays tripped until reset.
- **Heartbeat kill switch** — no server-side dead-man switch exists, so the bot
  owns one. Cancellation is *verified* by re-reading the book, never assumed.
- **SIGINT/SIGTERM** handlers cancel all orders and exit non-zero if any survive.
- **Serialized writes** — parallel deposits race on Sui object versions.
- **State refresh after every mutation** — state goes stale the instant a
  transaction lands.

### Reference skills

The official Aftermath skills (`aftermath-api` v3.0.0) are vendored **verbatim**
at [`AFTERMATH_SKILLS_REF/`](AFTERMATH_SKILLS_REF/), pinned to an exact upstream
commit in [`PINNED.md`](AFTERMATH_SKILLS_REF/PINNED.md).

They are kept unedited so the next sync is a diff — which means their
known-wrong URLs are left in place on purpose.
[`README-DELTA.md`](AFTERMATH_SKILLS_REF/README-DELTA.md) catalogues every one
and what this repository does instead. **Take their patterns, never their
hostnames.**

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `AF_WALLET_ADDRESS` | — | Your Sui address (public). The only required value. |
| `AF_WALLET_KEY` | — | Wallet secret. Only needed to sign; never read by this build. |
| `AF_API_BASE_URL` | `https://v2-preview.aftermath.finance` | API host |
| `AF_GAS_MODE` | `sponsored` | `sponsored` \| `self` \| `dynamic` |
| `AF_GAS_COIN_TYPE` | USDC | Coin that pays gas in `dynamic` mode |
| `AF_GAS_BUDGET_MIST` | `50000000` | Explicit gas budget (0.05 SUI) |
| `AF_COLLATERAL_COIN_TYPE` | USDC | Collateral coin; auto-discovered accounts use this |
| `AF_LEVERAGE` | `5` | Default leverage |
| `AF_ARMED` | `false` | Master arming switch |
| `AF_INTEGRATOR_ID` | — | Builder code integrator id (**u32 number** in v3.0.0) |
| `AF_INTEGRATOR_FEE` | — | Builder code fee share |
| `AF_HEARTBEAT_TIMEOUT_S` | `90` | Kill-switch silence threshold |
| `AF_SETTLE_MS` | `1500` | Settle delay after a mutation |

See [`.env.example`](.env.example) for the annotated template.

### Architecture

```
strategies/*  (never touch the venue)
        |
        v
TradingEngine / OrderManager
        |
        v
AftermathProxy  (cli/af/proxy.py)   <-- the ONE adapter
        |
   +----+----+----+----+----+
   |    |    |    |    |    |
  api  gas  tx safety ids markets
        |
        v
Aftermath V2 API (https://v2-preview.aftermath.finance)
```

`AftermathMockProxy` (`cli/af/mock.py`) is an interface-identical twin, so every
strategy runs with zero network and zero keys. Parity is enforced by
`tests/test_af_v2_adapter.py`, not by discipline.

### Documentation

| Document | Description |
|---|---|
| [V2 Migration](docs/aftermath/MIGRATION-V2.md) | What changed from v1, strategy coverage, atomic-vs-split |
| [Gasless Trading](docs/aftermath/gasless-trading.md) | Gas pool setup, USDC-as-gas, sponsored transactions |
| [Market Maker Economics](docs/aftermath/market-maker-economics.md) | Fee tiers, gas costs, PTBs |
| [Market Makers Guide](docs/aftermath/market-makers.md) | Integration guide and example requests |

### Known limitations

- **Not exercised against live markets.** Aftermath lists no markets yet
  (pre-relaunch), so trading paths are correct against the spec but unproven
  against real books. Zero markets is treated as expected and warned about,
  never as an outage.
- **No signer ships with this build.** Build, preview and inspect paths run;
  signing and submission are deliberately absent.
- **The `/api/wallet/*` family is in the spec but 404s live** — balance lookups
  degrade gracefully rather than failing.
- **Trigger-order cancellation** takes a stop-order id from `stop-order-datas`;
  the create path returns a transaction result, not the object id.
- **No WebSocket orderbook** — REST polling per tick. The `marketCandles`
  subscription on `/api/perpetuals/ws/updates` would reduce latency.

---

## Strategies

14 built-in strategies across four categories. Every strategy extends `BaseStrategy` with a single `on_tick()` method — no shared state, no hidden coupling between strategies.

### Market Making

Provide two-sided liquidity and earn the spread. These strategies quote bids and asks around a fair value estimate, managing inventory risk through skew and sizing adjustments.

| Strategy | Description | Key Parameters | When to Use |
|----------|-------------|----------------|-------------|
| `engine_mm` | Production quoting engine — composite 4-signal fair value, dynamic spreads (fee + vol + toxicity + event), inventory skew, multi-level quote ladder. Auto-halts on oracle staleness. *Requires `quoting_engine` module.* | `base_size`, `num_levels` | Primary MM strategy. Handles all market conditions including volatile regimes and stale data. |
| `avellaneda_mm` | Avellaneda-Stoikov optimal market maker. Reservation price adjusts with inventory; optimal spread from risk aversion `gamma` and order flow intensity `k`. Vol-bin classifier + drawdown amplifier. | `gamma`, `k`, `base_size` | When you want theoretically grounded inventory-aware quoting with well-understood parameters. |
| `regime_mm` | Vol-regime adaptive — classifies market into 4 volatility regimes (quiet/normal/volatile/extreme), switches spread width, sizing, and aggressiveness per regime. *Requires `quoting_engine` module.* | `base_size` | Volatile markets where a single spread width doesn't work. Auto-adapts without manual tuning. |
| `simple_mm` | Symmetric bid/ask quoting at fixed spread around mid. No inventory adjustment. | `spread_bps`, `size` | Testnet validation, baseline benchmarking, or low-vol stable pairs. |
| `grid_mm` | Fixed-interval grid levels above and below mid. Places N orders at equal spacing. *Requires `quoting_engine` module.* | `grid_spacing_bps`, `num_levels`, `size_per_level` | Range-bound markets where you want to accumulate and distribute across a price band. |
| `liquidation_mm` | Provides liquidity during cascade/liquidation events. Detects OI drops and widens spreads to capture forced-seller flow. *Requires `quoting_engine` module.* | `oi_drop_threshold_pct`, `cascade_spread_mult` | Liquidation-heavy markets. Only active during cascade conditions — sits idle otherwise. |

### Arbitrage

Exploit pricing dislocations across venues, instruments, or time horizons.

| Strategy | Description | Key Parameters | When to Use |
|----------|-------------|----------------|-------------|
| `funding_arb` | Cross-venue funding rate arbitrage — captures funding divergence between HL and external venues. Quoting-engine powered with bias from funding delta. *Requires `quoting_engine` module.* | `divergence_threshold_bps`, `max_bias_bps` | When funding rates diverge between venues. Works well on high-funding instruments. |
| `basis_arb` | Trades implied basis from funding rate — enters when annualized basis (contango/backwardation) exceeds threshold. | `basis_threshold_bps`, `size` | Capturing contango/backwardation dislocations. Pairs well with funding_arb. |

### Signal / Directional

Enter positions based on technical signals or momentum indicators.

| Strategy | Description | Key Parameters | When to Use |
|----------|-------------|----------------|-------------|
| `momentum_breakout` | Enters on volume + price breakout above/below N-period range. Requires both price and volume confirmation. | `lookback`, `breakout_threshold_bps`, `size` | Trending markets with clear breakout patterns. |
| `mean_reversion` | Trades when price deviates from SMA beyond a threshold. | `window`, `threshold_bps`, `size` | Range-bound markets with predictable mean-reversion behavior. |
| `aggressive_taker` | Crosses the spread with directional bias. Sinusoidal amplitude modulation. | `size`, `bias_amplitude` | When you have strong directional conviction and want immediate fills. |

### Infrastructure / Risk

Supporting strategies for portfolio management, block liquidity, and autonomous decision-making.

| Strategy | Description | Key Parameters | When to Use |
|----------|-------------|----------------|-------------|
| `hedge_agent` | Inventory exposure reducer. Fires when net notional exceeds threshold. This is not the BTCSWP funding-rate hedge; use `hl hedge propose` / `hl hedge backtest` for that. | `notional_threshold` | Always-on risk overlay. Pairs with any MM or signal strategy. |
| `rfq_agent` | Block-size dark RFQ liquidity — quotes for large orders with wider spreads. | `min_size`, `spread_bps` | Institutional/block flow. Provides hidden liquidity for large counterparties. |
| `claude_agent` | Multi-model LLM trading agent. Sends market snapshot to an LLM (Gemini, Claude, or OpenAI), receives structured trade decisions. | `model`, `base_size` | Experimental/research. Autonomous decision-making using LLM reasoning. |

### Quoting Engine Pipeline

The engine-powered strategies (`engine_mm`, `funding_arb`, `regime_mm`, `liquidation_mm`) share a common pipeline:

```
Market Data -> Composite Fair Value -> Dynamic Spread -> Inventory Skew -> Multi-Level Ladder -> Orders
               (4-signal blend)       (fee+vol+tox)     (price+size)     (exponential decay)
```

### LLM Agent (Multi-Model)

| Provider | Models | Env Variable |
|----------|--------|-------------|
| Google Gemini | `gemini-2.0-flash` (default), `gemini-2.5-pro` | `GEMINI_API_KEY` |
| Anthropic Claude | `claude-haiku-4-5-20251001`, `claude-sonnet-4-20250514` | `ANTHROPIC_API_KEY` |
| OpenAI | `gpt-4o`, `gpt-4o-mini`, `o3-mini` | `OPENAI_API_KEY` |

---

## Skills

Built on the open [Agent Skills](https://agentskills.io) standard. Each skill is self-contained with instructions, scripts, and references.

| Skill | What it does | Install |
|-------|-------------|---------|
| **[Onboard](#onboard)** | Step-by-step first-time setup — from zero to first trade. Decision trees, verification at each step, error recovery. | [`SKILL.md`](skills/onboard/SKILL.md) |
| **[APEX Strategy](#apex--autonomous-multi-slot-strategy)** | Fully autonomous 2-3 slot trading. Composes Radar + Pulse + Guard. Proven on testnet: signal detection, entry, trailing stop, exit. | [`SKILL.md`](skills/apex/SKILL.md) |
| **[Radar](#radar--opportunity-radar)** | 4-stage funnel screening all HL perps. Scores 0-400 across market structure, technicals, funding, and BTC macro. | [`SKILL.md`](skills/radar/SKILL.md) |
| **[Pulse](#pulse--emerging-pulse-detector)** | Detects sudden capital inflow via OI delta, volume surge, funding flips. IMMEDIATE signals at 100 confidence. | [`SKILL.md`](skills/pulse/SKILL.md) |
| **[Guard (Dynamic Stop Loss)](#guard--dynamic-stop-loss)** | 2-phase trailing stop with tiered profit-locking. ROE-based triggers that auto-account for leverage. | [`SKILL.md`](skills/guard/SKILL.md) |
| **[REFLECT](#reflect--performance-review)** | Nightly self-improvement loop. Analyzes every trade, finds patterns, generates actionable recommendations. | [`SKILL.md`](skills/reflect/SKILL.md) |

### Install a skill (agents)

Grab the raw URL and go:

```
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/onboard/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/apex/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/radar/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/pulse/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/guard/SKILL.md
https://raw.githubusercontent.com/Nunchi-trade/agent-cli/main/skills/reflect/SKILL.md
```

### Install a skill (OpenClaw / ClawHub)

```bash
clawhub install nunchi-trade/yex-trader
```

### Install a skill (Claude Code)

```bash
git clone https://github.com/Nunchi-trade/agent-cli.git ~/agent-cli
cd ~/agent-cli && pip install -e .
mkdir -p ~/.claude/skills/yex-trader
cp ~/agent-cli/cli/skill.md ~/.claude/skills/yex-trader/SKILL.md
```

---

## Autonomous Trading Stack

### Onboard

First-time setup skill that walks an agent from zero to first trade in 9 steps. Decision trees at each step, verification commands, error recovery tables.

```bash
# The onboard skill automates this entire flow:
bash scripts/bootstrap.sh          # Step 1: Environment
hl wallet auto --save-env          # Step 2: Wallet
hl setup claim-usdyp               # Step 4: Fund account
hl builder approve                 # Step 5: Builder fee
hl run avellaneda_mm --mock --max-ticks 3  # Step 6: Validate
```

**[Download SKILL.md](skills/onboard/SKILL.md)**

---

### Guard — Dynamic Stop Loss

Trailing stop system with tiered profit-locking. Protects profits while letting winners run.

**Two phases:**
- **Phase 1 (Let it breathe)** — Wide retrace tolerance while position builds. Auto-cut at 90 min if no graduation; weak-peak early cut at 45 min if peak ROE < 3%.
- **Phase 2 (Lock the bag)** — Tiered profit floors that ratchet up as ROE grows. Exchange-level stop loss synced to Hyperliquid as crash safety net.

| Preset | Phase 1 Retrace | Tiers | Stagnation TP |
|--------|----------------|-------|---------------|
| `moderate` | 3% | 6 tiers (10-100% ROE) | No |
| `tight` | 5% | 4 tiers (10-75% ROE) | Yes (8% ROE, 1h) |

```bash
hl guard run -i ETH-PERP --preset tight
```

**[Download SKILL.md](skills/guard/SKILL.md)**

---

### Radar — Opportunity Radar

Multi-factor screening engine that evaluates all HL perps for trade setups. 4-stage funnel, scores 0-400.

| Pillar | Weight | Signals |
|--------|--------|---------|
| Market Structure | 35 | Volume, OI, liquidity |
| Technicals | 30 | RSI, EMA, patterns, hourly trend |
| Funding | 20 | Rate extremes, direction bias |
| BTC Macro | 15 | Trend alignment, regime filter |

```bash
hl radar once --mock    # Single scan
hl radar run --mock     # Continuous (every 15 min)
```

**[Download SKILL.md](skills/radar/SKILL.md)**

---

### Pulse — Emerging Momentum Detector

Detects assets with sudden capital inflow using OI, volume, funding, and price signals. Runs every 60 seconds.

**5-tier signal taxonomy** for entry classification, plus informational signals for Radar scoring:

| Tier | Signal | Trigger | Confidence |
|------|--------|---------|------------|
| 1 | `FIRST_JUMP` | First asset in sector with OI + volume breakout | 100 |
| 2 | `CONTRIB_EXPLOSION` | OI +15% **AND** volume 5x (simultaneous extreme) | 95 |
| 3 | `IMMEDIATE_MOVER` | OI +15% **OR** volume 5x (either extreme) | 80 |
| 4 | `NEW_ENTRY_DEEP` | OI grows 8%+ but volume stays low — smart money accumulation | 65 |
| 5 | `DEEP_CLIMBER` | Sustained OI climb 5%+ per window over 3+ consecutive scans | 55 |
| — | `VOLUME_SURGE` | 4h volume / average > 3x | 70 |
| — | `OI_BREAKOUT` | OI jumps 8%+ above baseline | 60 |
| — | `FUNDING_FLIP` | Funding rate reverses or accelerates 50%+ | 50 |

```bash
hl pulse once --mock      # Single scan
hl pulse run --mock       # Continuous (every 60s)
```

**[Download SKILL.md](skills/pulse/SKILL.md)**

---

### APEX — Autonomous Multi-Slot Strategy

The top-level orchestrator. Composes Radar + Pulse + Guard into a single autonomous strategy managing 2-3 concurrent positions.

**Tick schedule** (60s base):
- Every tick: Fetch prices, update ROEs, check Guard, run Pulse, evaluate entry/exit
- Every 5 ticks: Watchdog health check
- Every 15 ticks: Run opportunity radar

**Entry priority** (tier-based):

| Priority | Source | Condition |
|----------|--------|-----------|
| 1 | FIRST_JUMP | First sector mover (tier 1) |
| 2 | CONTRIB_EXPLOSION | Simultaneous extreme OI + volume (tier 2) |
| 3 | Smart money | Pulse confidence > 90 |
| 4 | IMMEDIATE_MOVER | Either extreme metric (tier 3) |
| 5 | Radar | Score > 170 |
| 6 | NEW_ENTRY_DEEP | Limit-order accumulation (tier 4) |
| 7 | DEEP_CLIMBER | Sustained OI trend (tier 5) |

**Presets:**

| Preset | Slots | Leverage | Radar Threshold | Daily Loss Limit |
|--------|-------|----------|-------------------|------------------|
| `default` | 3 | 10x | 170 | $500 |
| `conservative` | 2 | 5x | 190 | $250 |
| `aggressive` | 3 | 15x | 150 | $1,000 |

```bash
hl apex run --mock --max-ticks 10          # Mock test
hl apex run                                 # Live testnet
hl apex run --preset conservative --mainnet # Live mainnet
```

**[Download SKILL.md](skills/apex/SKILL.md)**

---

### REFLECT — Performance Review

Nightly self-improvement loop. Reads trade history, computes metrics, detects patterns, generates actionable recommendations.

| Metric | Description |
|--------|-------------|
| Win Rate | % of round trips with positive net PnL |
| FDR | Fee Drag Ratio — fees as % of gross wins |
| Direction Split | Long vs short win rates and PnL |
| Holding Periods | Bucketed by <5m, 5-15m, 15-60m, 1-4h, 4h+ |
| Monster Dependency | % of net PnL from best single trade |

```bash
hl reflect run --since 2026-03-01
hl reflect report
hl reflect history -n 10
```

**[Download SKILL.md](skills/reflect/SKILL.md)**

### REFLECT Self-Improvement Loop

When running inside APEX, REFLECT executes automatically every 240 ticks (~4 hours) and at a configurable UTC hour (default 04:00). It reads the trade log, computes performance metrics, and **auto-adjusts APEX parameters** based on findings:

| Finding | Automatic Adjustment |
|---------|---------------------|
| FDR > 30% (fees eating profits) | Raise radar threshold, disable immediate mover entries |
| Win rate < 40% | Tighten both radar and movers confidence thresholds |
| 5+ consecutive losses | Reduce daily loss limit by 20% |
| Direction imbalance (e.g. longs losing) | Limit same-direction slots |
| Fees exceed gross PnL | **Emergency mode**: disable auto-entries, raise all thresholds |
| Profitable + healthy | Slightly relax thresholds toward defaults |

All adjustments have guardrail bounds — parameters can't swing wildly. Disable with `reflect_auto_adjust: false` in APEX config.

**Scheduled tasks** (built into APEX tick loop):
- **Daily PnL reset** at UTC midnight — clears daily loss tracking
- **REFLECT comprehensive report** at UTC 04:00 — full performance review with markdown report saved to `data/apex/reflect/`

---

### Production Safety

Built-in safety systems that protect positions even when the runner process crashes.

#### Exchange-Level Stop Loss Sync

Guard places a **trigger order directly on Hyperliquid** as a safety net. If the runner crashes, the exchange-side stop loss remains active. Synced on entry, tier ratchet, and startup — intentionally left in place on shutdown.

```
Position Entry → Place SL trigger order at Phase 1 floor
Tier Ratchet   → Cancel old SL, place new at higher tier floor
Position Close → Cancel SL trigger order
Runner Crash   → Exchange SL stays active (that's the point)
```

#### Clearinghouse Reconciliation

Bidirectional reconciliation between APEX slots and Hyperliquid positions. Detects orphaned exchange positions, orphaned slots, and size mismatches. Runs on startup and periodically via watchdog.

```bash
hl apex reconcile             # Check for discrepancies
hl apex reconcile --fix       # Auto-adopt orphans, fix sizes
```

| Discrepancy | Severity | Auto-Fix |
|-------------|----------|----------|
| Orphan exchange position | Critical | Adopt into empty slot + create Guard |
| Orphan slot (no position) | Warning | Mark slot closed |
| Size mismatch >10% | Critical | Update slot to match exchange |

#### Risk Guardian

Graduated risk response with three states and automatic transitions:

```
OPEN ──(2 consecutive losses)──→ COOLDOWN ──(trigger again)──→ CLOSED
  ↑                                  │                            │
  └──────(auto-expiry 30 min)────────┘                            │
  └────────────────────(daily reset)──────────────────────────────┘
```

| State | Entries | Exits | Trigger |
|-------|---------|-------|---------|
| `OPEN` | Allowed | Allowed | Default |
| `COOLDOWN` | **Blocked** | Allowed | 2+ consecutive losses or drawdown >= 50% of limit |
| `CLOSED` | **Blocked** | **Blocked** | Daily loss limit hit |

Exchange-level stop losses remain active in all states.

#### Rotation Cooldown

Anti-churn protection:
- **Minimum hold (45 min)** — Conviction collapse and stagnation exits blocked until 45 min. Guard hard stops and daily loss still override.
- **Slot cooldown (5 min)** — Closed slots can't be reused for 5 minutes.

#### State Archiving

Closed position state files archived to `data/archive/{YYYY-MM-DD}/` on close. Trade audit trail (`trades.jsonl`) is never archived.

```bash
hl apex archive               # Archive all closed state files
hl apex archive --days 7      # Only older than 7 days
hl apex archive --dry-run     # Preview without moving
```

#### ALO Fee Optimization

Entry orders default to **ALO (post-only)** for maker rebates (~3 bps savings per round-trip). Falls back to GTC if ALO is rejected. Exits and Guard closes always use IOC.

---

### Autoresearch-Powered REFLECT

Connects REFLECT to an autonomous optimization loop. A backtest harness replays historical trades against config variants, and an iterative agent loop finds parameter improvements.

```bash
python3 scripts/backtest_apex.py --config apex_config.json --trades data/cli/trades.jsonl
```

REFLECT auto-generates research directions:

| Finding | Suggested Direction |
|---------|-------------------|
| FDR > 30% | Raise `radar_score_threshold` in [170, 250] |
| Win rate < 40% | Sweep `pulse_confidence_threshold` in [70, 95] |
| Direction imbalance | Set `max_same_direction` to 1 |
| Healthy + profitable | Try lowering `radar_score_threshold` in [140, 170] |

---

## Commands

```bash
# Core trading
hl run <strategy> [options]       # Start autonomous trading
hl status [--watch]               # Show positions, PnL, risk
hl trade <inst> <side> <size>     # Place a single order
hl account                        # Show HL account state
hl strategies                     # List all strategies
hl skills list                    # Discover installed skills

# Autonomous stack
hl apex run [options]             # APEX multi-slot orchestrator
hl apex reconcile [--fix]         # Reconcile state vs exchange
hl apex archive [--days N]        # Archive closed state files
hl radar run [options]            # Opportunity radar
hl pulse run [options]            # Pulse momentum detector
hl guard run -i ETH-PERP [options] # Guard trailing stop
hl reflect run [--since DATE]        # Performance review
hl hedge info [--json]             # Funding hedge profiles and schemas
hl hedge propose [options]         # BTCSWP funding hedge proposal
hl hedge backtest --csv <path>     # Local funding hedge cashflow backtest

# Infrastructure
hl builder approve [--mainnet]    # Approve builder fee
hl wallet auto [--save-env]       # Create wallet (agent-friendly)
hl setup check                    # Validate environment
hl setup bootstrap                # Auto-setup venv + install
hl setup claim-usdyp              # Claim testnet USDyP
hl mcp serve                      # Start MCP server
```

---

## MCP Server

Expose all trading tools via [Model Context Protocol](https://modelcontextprotocol.io) for AI agent integration.

### Hosted MCP (Recommended)

Use the hosted Nunchi MCP when you want a Robinhood-style setup: paste one URL into your AI client, authenticate, and grant scoped tool access. Your AI client receives a scoped MCP token, not your exchange private key.

```bash
https://agent.nunchi.trade/mcp/trading
```

Client setup:

```bash
# Cursor
Settings -> Cursor Settings -> Tools & MCPs -> Connect
MCP URL: https://agent.nunchi.trade/mcp/trading

# Claude Code
claude mcp add nunchi-trading --transport http https://agent.nunchi.trade/mcp/trading

# Codex CLI
codex mcp add nunchi-trading --url https://agent.nunchi.trade/mcp/trading
```

After connecting, authenticate in the browser consent flow and start with:

```text
Run setup_check, then show my account status and available strategies.
```

Hosted access defaults to read-only/testnet. Write tools (`trade`, `run_strategy`, `apex_run`) and mainnet access require explicit consent and gateway-enforced limits.

### Local MCP (Development Only)

```bash
hl mcp serve                      # stdio transport (default)
hl mcp serve --transport sse      # SSE transport
```

**24 tools exposed:** `strategies`, `builder_status`, `wallet_list`, `wallet_auto`, `setup_check`, `funding_hedge_info`, `funding_hedge_propose`, `funding_hedge_backtest`, `account`, `status`, `trade`, `run_strategy`, `radar_run`, `apex_status`, `apex_run`, `reflect_run`, `schedule_cancel`, `emergency_close_all`, `order_status`, `funding_rates`, `agent_memory`, `trade_journal`, `judge_report`, `obsidian_context`

Fast tools (strategies, builder, wallet, setup, memory, journal, judge) call Python directly — zero subprocess overhead. Local MCP is for development and agent harness testing only; hosted deployment goes through Nunchi Auth and the subscription-gated hosted-agent flow.

### HTTP API & SSE

Every deployed agent also exposes an HTTP REST API and SSE real-time feed for dashboards, monitoring, and external integrations. A separate leaderboard microservice tracks agent PnL rankings.

**[Full API Reference →](docs/api-reference.md)**  
**[July 2026 Exhibit A Testing Plan →](docs/EXHIBIT_A_TESTING_PLAN.md)**

---

## Hosted Agent Deployment

The only supported hosted deployment path is **Nunchi-hosted**: users pay in web-auth, bind an agent wallet, and Nunchi provisions and manages the agent on Nunchi-owned Railway infrastructure.

Start here:

[Launch a hosted agent through Nunchi Auth](https://auth.nunchi.trade)

User flow:

1. Open web-auth and connect a wallet.
2. Bind or create an agent wallet.
3. Pay for the hosted-agent subscription with Stripe-supported payment methods or USDC.
4. Deploy the hosted agent from the wallet binding page.
5. Refresh status and open the hosted endpoint returned by web-auth.

Hosted agents use Nunchi-owned inference credentials by default:

| Variable | Default | Description |
|----------|---------|-------------|
| `AI_PROVIDER` | `openrouter` | OpenAI-compatible hosted inference |
| `AI_MODEL` | `openrouter/auto` | Low-latency/cost-aware default for chat and tool use |
| `NUNCHI_REFLECT_MODEL` | `openrouter/fusion` | Higher-confidence model for REFLECT/research-style analysis |

Users do not need Railway credentials, Railway project access, or provider API keys. Billing, entitlement checks, wallet binding, secret injection, lifecycle controls, and hosted endpoint discovery all live in web-auth. This repository intentionally does not include Dockerfiles, `railway.toml`, or public deployment templates.

---

## YEX Yield Markets

[YEX](https://yex.nunchi.trade) (Nunchi HIP-3) yield perpetuals on Hyperliquid:

| Instrument | HL Coin | Description |
|------------|---------|-------------|
| VXX-USDYP | yex:VXX | Volatility index yield perp |
| US3M-USDYP | yex:US3M | US 3M Treasury rate yield perp |
| BTCSWP-USDYP | yex:BTCSWP | BTC interest rate swap yield perp — tracks the BTC-denominated swap curve |

```bash
hl run avellaneda_mm -i VXX-USDYP --tick 15
hl run funding_arb -i US3M-USDYP --tick 30
hl run engine_mm -i BTCSWP-USDYP --tick 10
```

---

## Architecture

```
cli/           CLI commands and trading engine
  commands/    Subcommand modules (run, apex, radar, pulse, guard, reflect, house, ...)
  mcp_server.py  MCP server (24 tools via FastMCP)
  hl_adapter.py  Direct HL API adapter (live + mock)
  builder_fee.py Builder fee config (HL native BuilderInfo)
  keystore.py    Encrypted keystore (geth-compatible)
  strategy_registry.py  Strategy + YEX market definitions
strategies/    14 trading strategy implementations
modules/       Pure logic modules (zero I/O)
  apex_engine.py     APEX decision engine
  radar_engine.py    Opportunity radar
  pulse_engine.py    Pulse momentum detector (5-tier signal taxonomy)
  trailing_stop.py   Guard trailing stop (Phase 1 auto-cut)
  reflect_engine.py  Performance analysis
  reconciliation.py  Clearinghouse reconciliation engine
  archiver.py        State file archiving
skills/        Agent Skills (SKILL.md + runners)
  onboard/     First-time setup guide
  apex/        APEX orchestrator
  radar/       Opportunity radar
  pulse/       Pulse momentum detector
  guard/       Dynamic stop loss
  reflect/        Performance review
sdk/           Strategy base class and model registry
parent/        HL API proxy, position tracking, risk management
scripts/       Backtest harness, bootstrap
tests/         Test suite (483 tests)
```

---

## Custom Strategies

Create a Python file that subclasses `BaseStrategy`:

```python
from sdk.strategy_sdk.base import BaseStrategy
from common.models import MarketSnapshot, StrategyDecision

class MyStrategy(BaseStrategy):
    def __init__(self, lookback=10, threshold=0.5, size=0.1, **kwargs):
        super().__init__(strategy_id="my_strategy")
        self.lookback, self.threshold, self.size = lookback, threshold, size
        self._prices = []

    def on_tick(self, snapshot, context=None):
        mid = snapshot.mid_price
        self._prices.append(mid)
        if len(self._prices) < self.lookback:
            return []

        pct = (mid - self._prices[-self.lookback]) / self._prices[-self.lookback] * 100
        if abs(pct) > self.threshold:
            return [StrategyDecision(
                action="place_order",
                instrument=snapshot.instrument,
                side="buy" if pct > 0 else "sell",
                size=self.size,
                limit_price=round(snapshot.ask if pct > 0 else snapshot.bid, 2),
            )]
        return []
```

```bash
hl run my_strategies.my_strategy:MyStrategy -i ETH-PERP --tick 10
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `HL_PRIVATE_KEY` | Yes* | Hyperliquid private key |
| `HL_KEYSTORE_PASSWORD` | Alt* | Password for encrypted keystore |
| `HL_TESTNET` | No | `true` (default) or `false` for mainnet |
| `BUILDER_ADDRESS` | No | Override builder fee address |
| `BUILDER_FEE_TENTHS_BPS` | No | Override fee rate (default: 100 = 10 bps) |
| `ANTHROPIC_API_KEY` | No | For `claude_agent` with Claude |
| `GEMINI_API_KEY` | No | For `claude_agent` with Gemini |
| `OPENAI_API_KEY` | No | For `claude_agent` with OpenAI |

\* Either `HL_PRIVATE_KEY` or a keystore with `HL_KEYSTORE_PASSWORD` is required.

---

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v                  # 483 tests
```

## Attribution 

Inspired by openclaw, senpi, and claude code. 
---

## Links

- **Docs** — [docs.nunchi.trade](https://docs.nunchi.trade)
- **YEX App** — [yex.nunchi.trade](https://yex.nunchi.trade)
- **Research** — [research.nunchi.trade](https://research.nunchi.trade)
- **Discord** — [discord.gg/nunchi](https://discord.gg/nunchi)
- **X** — [@nunchi](https://x.com/nunchi)
- **GitHub** — [Nunchi-trade](https://github.com/Nunchi-trade)
- **Agent Skills Standard** — [agentskills.io](https://agentskills.io)

---

<p align="center">
  <sub>Built by <a href="https://nunchi.trade">Nunchi</a> &bull; MIT License</sub>
</p>
