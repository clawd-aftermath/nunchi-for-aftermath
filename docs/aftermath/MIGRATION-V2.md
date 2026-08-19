# Aftermath V2 migration

What changed when this repository moved from the Aftermath v1 API to V2, and
from Hyperliquid to Aftermath as the venue.

- **API**: `https://aftermath.finance`, the launched production mainnet host.
  As verified on 2026-08-19, it serves 15 native-USDC perpetual markets.
- **Skills**: `aftermath-api` v3.0.0, vendored at
  [`AFTERMATH_SKILLS_REF/`](../../AFTERMATH_SKILLS_REF/).
- **Adapter**: `cli/af_proxy.py` (1,727 lines, v1) → the `cli/af/` package.

---

## 1. Strategy coverage

All 19 registered strategies work through the one adapter. None were
individually ported.

`tests/test_af_v2_strategy_coverage.py` proves this: it walks the registry,
instantiates each strategy, drives three real ticks against the mock adapter,
routes any resulting orders through `place_order`, exercises the atomic requote
path, and asserts a verified cancel-all.

| Strategy | Status | Notes |
|---|---|---|
| `simple_mm` | works | requote via `cancel-and-place-orders` |
| `avellaneda_mm` | works | requote via `cancel-and-place-orders` |
| `grid_mm` | works | ladders via `place-scale-order` |
| `regime_mm` | works | requote via `cancel-and-place-orders` |
| `engine_mm` | works | requote via `cancel-and-place-orders` |
| `liquidation_mm` | works | requote via `cancel-and-place-orders` |
| `aggressive_taker` | works | IOC (`orderType` 3) |
| `basis_arb` | works | |
| `funding_arb` | works | funding from `estimatedFundingRate` |
| `funding_momentum` | works | |
| `mean_reversion` | works | |
| `momentum_breakout` | works | |
| `oi_divergence` | works | open interest from `marketState.openInterest` |
| `trend_follower` | works | |
| `simplified_ensemble` | works | |
| `claude_agent` | needs external service | Requires an Anthropic API key for its decision loop. Venue access is unaffected — it trades through the same adapter. |
| `hedge_agent` | needs external service | Needs the CFI hedge service. Its *second leg* also assumes another venue; on Aftermath alone it runs single-legged. |
| `cfi_hedge` | needs external service | Same as above. |
| `rfq_agent` | needs external service | Needs an RFQ counterparty feed. Aftermath exposes no RFQ endpoint — **this is the one genuine venue capability gap.** |

**Impossible on Aftermath: none**, with one caveat — `rfq_agent` has no
Aftermath equivalent for its quote-solicitation step because the API exposes no
RFQ surface. It loads and its execution path works; only its price *discovery*
has nowhere to come from.

The five market-making strategies that previously needed the proprietary
`quoting_engine` module now run in-tree, since the upstream merge brought
`quoting_engine/` with it.

---

## 2. Atomic vs. split operations

Every multi-step operation, and whether it is now one transaction.

| Operation | Transactions | Route | Reason |
|---|---|---|---|
| Requote (cancel + replace quotes) | **1** | `cancel-and-place-orders` | A split leaves the strategy unquoted or double-quoted in between, and can fail halfway. This is the default requote path for **all six** MM strategies. |
| Price ladder / grid | **1** | `place-scale-order` | N placements costs N gas payments and can leave a half-built grid. |
| Cancel batch | **1** | `cancel-orders` | Takes a market→orders map, so multiple markets cancel together. |
| Stop-loss / take-profit pair | **1** | `place-stop-orders` | The pair is placed as one unit. |
| Set leverage | **1** | `set-leverage` | Single operation already. |
| Allocate collateral | **1** | `allocate-collateral` | Single operation already. |
| Account onboarding (create + deposit + allocate) | **1** *(design)* | `create-account` with `txKind` | Composable as one PTB so a user cannot be stranded half-set-up. **Not exercised** — creation requires signing, which this build does not do. |
| Place order → observe fill → size next order | **several** | — | **Deliberately sequential.** The second decision depends on the first's outcome. Forcing atomicity here would change semantics, not just packaging. |
| Deposit collateral | **several, serialized** | `deposit-collateral` | Parallel deposits race on Sui object versions and fail with equivocation errors. Serialized under one lock rather than batched. |
| Cancel-all on shutdown | **1 per market** | `cancel-orders` | The route is per-market-map; one transaction per market, then a verified re-read. |

### TWAP: native orders vs. the client-side loop

V2 adds native TWAP (`create/edit/cancel-twap-orders`).
**`execution/twap.py` keeps its client-side loop.** Reasons:

1. It is venue-agnostic and shared with non-Aftermath code paths; replacing it
   would couple a generic executor to one venue.
2. It reacts to fills between slices — native TWAP schedules on-chain and does
   not expose that feedback to the strategy.
3. Native TWAP requires a pre-funded gas coin sized for *all* scheduled
   executions (`Σ chunksAmount`), which is a materially different funding model
   from the current one.

Native TWAP is a good fit for *fire-and-forget* execution and the adapter has
the route available. The recommendation is to expose it as an opt-in
(`execution_algo: "twap_native"`) rather than to silently swap the semantics of
the existing executor. **Not implemented in this pass.**

---

## 3. Hyperliquid: found vs. removed

HL is removed as a venue, not proxied underneath. The adapter keeps upstream
*call signatures* so strategies need no rewrite; beneath it, execution is 100%
Aftermath.

### Removed from the Aftermath execution path

| Item | Where it was | Now |
|---|---|---|
| `AftermathProxy` importing `HLFill` from `parent/hl_proxy.py` | v1 adapter | `AfFill`, defined in `cli/af/proxy.py`. Structurally identical, so the engine is unchanged; imports nothing HL. |
| HL instrument suffixes / `xyz:` HIP-3 prefixes | `_normalise_instrument` | Stripped, not translated. `cli/af/markets.py::base_asset` drops any `dex:` prefix. |
| Dex-local asset-ID math (`100000 + dex_index*10000 + meta_index`) | HL adapter | Gone. Market ids are on-chain object ids resolved from `/api/perpetuals/all-markets` and never constructed. |
| `marginPct` + leverage sizing | HL sizing | Isolated margin with explicit `allocate-collateral`. |
| `sendAsset` / `destinationDex` | HL transfers | Gone. Aftermath uses `transfer-collateral` between accounts. |
| Cross-margin assumption | `crossMarginSummary` in account state | Absent. `capabilities().supports_cross_margin is False`. |
| `HL_TESTNET`, `INSTRUMENT=ETH-PERP` | `railway.toml` | Replaced with `AF_*` variables and `ETH-AF-PERP`. |
| `AF_BASE_URL` → legacy host setting | README, env | `AF_API_BASE_URL` → `https://aftermath.finance`. |
| `SUI_PRIVATE_KEY` as the documented secret | README | `AF_WALLET_KEY`; `SUI_PRIVATE_KEY` still read as a fallback. |
| v1 skill copy at `skills/aftermath-perpetuals/` | in-repo | Removed; superseded by `AFTERMATH_SKILLS_REF/` (v3.0.0). |

Enforced by `tests/test_af_v2_hosts.py`, which scans `cli/af/` for HL tokens
(comments and docstrings stripped first, so prose describing the removal is
allowed) and separately walks the AST to prove no import resolves to an HL
module.

### Still present, deliberately, OUTSIDE the Aftermath path

Upstream Nunchi is a Hyperliquid bot and the merge brought its full HL surface
with it: `adapters/hl_adapter.py`, `cli/hl_adapter.py`, `parent/hl_proxy.py`,
`cli/commands/{trade,account,margin,funding,…}.py`, and roughly 40 other
modules, plus `HL_PRIVATE_KEY` / `HL_TESTNET` in ~120 places.

**These were left intact.** Removing them would delete the `hl *` command
surface, its tests, and upstream's own venue abstraction — a much larger change
than this task, and one that would make future upstream merges painful. What
matters for the brief is satisfied: the Aftermath execution path
(`strategies/* → engine → order_manager → cli/af/*`) contains zero HL, and no
strategy can reach a Hyperliquid venue through the Aftermath adapter.

If full excision is wanted, it is a separate, mechanical piece of work with a
clear boundary: delete the `hl`-prefixed commands and the three HL adapter
modules, then let the test suite name what breaks.

---

## 4. v3.0.0 breaking changes — applied

| Old (broken) | New | Where |
|---|---|---|
| `integratorAddress` (address string) | `integratorId` (u32 **number**) | `proxy._normalise_builder_code` — rejects the address form with an explanatory error |
| `builderCode.takerFee` | `builderCode.integratorFee` | same |
| config `maxTakerFee` | `maxIntegratorFee` | not used by this repo |
| `stopLossIndexPrice` / `takeProfitIndexPrice` | `stopLossPrice` / `takeProfitPrice` | `proxy.place_trigger_order` |
| candle `intervalMs` / `interval_ms` | `resolution` (CCXT-style string) | `proxy.get_candles`, `proxy._resolution` |
| `GET /ws/market-candles/{id}/{ms}` | `marketCandles` subscription on `/ws/updates` | documented; streaming not implemented |
| `/api/rewards/points` → `{points}` int | `{totalPoints}` float | not used by this repo |
| `/api/rewards/history` unauthenticated | needs `bytes` + `signature` | not used by this repo |

### Removed response fields — all reads deleted

`marketParams.gasPriceTwapPeriodMs`, `forceCancelFee`, `gasPriceTakerFee`,
`zScoreThreshold` (→ `priorityTakerFee`); position `makerFee` / `takerFee`;
vault `totalCollateral` / `totalCollateralUsd`; liquidation `forceCancelFeesUsd`.

`cli/af/markets.py::Market` surfaces only fields that exist in v3.0.0, so a
removed field cannot be silently read as `None`. Price-feed ids are handled as
numeric (`u32`), not address strings.

### Deterministic ordering — respected

Markets sort by symbol, positions by market id, pending bids and asks each by
order id. The adapter preserves the API's order rather than re-sorting. Inner
stop-order ordering is unspecified and is not depended on.

---

## 5. Things that are NOT done

Stated plainly rather than approximated:

1. **Nothing was signed, submitted or broadcast.** No signer ships; `_submit`
   raises while disarmed. Build → preview → inspect paths are implemented and
   unit-tested; the sign and reconcile-after-submit legs are unexercised
   against the live chain.
2. **No live trading verification.** A read-only production probe on 2026-08-19
   returned 15 native-USDC markets, but no order shape has been signed,
   submitted, or round-tripped against a live book.
3. **Native TWAP not wired** — see §2.
4. **Single-PTB onboarding is designed, not implemented.** `create-account`
   with `deferShare` returns deferred PTB argument references
   (`{accountArg, adminCapArg, sharePolicyArg}`); the adapter does not assume a
   bare `{txKind}`, but composing the full onboarding PTB requires signing.
5. **`/api/wallet/*` is unused.** It is in the spec but 404s live, so SUI
   balance is not fetched; `doctor` reports `self`-mode balance as unknown
   rather than guessing.
6. **Hyperliquid not excised repo-wide** — see §3.
7. **WebSocket streams not implemented.** REST polling per tick.
8. **`AF_WALLET_ADDRESS` is not derived from the key.** Deriving it would mean
   reading the secret, which this build does not do. `doctor` reports it as a
   required setting instead.
