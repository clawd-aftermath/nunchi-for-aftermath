# Market Making on Aftermath Perpetuals — Optimization Guide

> How to market make on Aftermath Perpetuals with maximum gas efficiency.

---

## The Optimal Pattern: Cancel-and-Place

The single most impactful optimization is using **atomic cancel-and-place** instead of separate transactions.

### Inefficient (separate cancel + place)

```
TX 1: interface::cancel_orders           → 0.0016 SUI
TX 2: interface::place_limit_order       → 0.0023 SUI
─────────────────────────────────────────────────────
Total: 0.004 SUI for 1 order update (2 transactions)
```

### Efficient (cancel-and-place combined + batched)

```
TX 1:
  [0] price_feed::update_price_feed       ← oracle
  [1] interface::cancel_orders             ← cancel old quotes
  [2] interface::start_session
  [3] interface::place_limit_order ×5      ← 5 new orders
  [4] interface::end_session
  [5] interface::deallocate_collateral
─────────────────────────────────────────────────────
Total: 0.002 SUI for cancel + 5 orders (1 transaction)
Gas per order: 0.0004 SUI
```

**Result: ~7x more gas efficient per order.**

---

## API Endpoint

```http
POST /api/perpetuals/account/transactions/cancel-and-place-orders
Content-Type: application/json
```

### Request body

```json
{
  "accountId": "123n",
  "accountCapId": "0x...",
  "walletAddress": "0x...",
  "marketId": "0x...",
  "orderIdsToCancel": ["456n", "789n"],
  "ordersToPlace": [
    { "side": 0, "price": "7250000000000n", "size": "100000000n" },
    { "side": 0, "price": "7249000000000n", "size": "100000000n" },
    { "side": 1, "price": "7260000000000n", "size": "100000000n" },
    { "side": 1, "price": "7261000000000n", "size": "100000000n" },
    { "side": 1, "price": "7262000000000n", "size": "100000000n" }
  ],
  "orderType": 2,
  "reduceOnly": false,
  "hasPosition": true,
  "sponsor": {
    "walletAddress": "0x..."
  }
}
```

### Key fields

| Field | Value | Notes |
|-------|-------|-------|
| `orderType` | `2` (Post-Only) | Guarantees maker execution. Use this for MM quotes |
| `ordersToPlace` | Array of orders | Batch 5+ for best gas efficiency |
| `side` | `0` = bid/long, `1` = ask/short | |
| `price` | Scaled integer string with `n` | Market-specific scaling |
| `size` | Base asset amount string with `n` | 9 decimal precision |
| `sponsor` | Optional gas pool | Pre-fund for predictable costs |
| `hasPosition` | boolean | Whether account has open position in this market |
| `reduceOnly` | boolean | Set true for reduce-only quotes |

### Response

```json
{
  "txKind": "base64-encoded TransactionKind",
  "sponsorSignature": "base64 or null"
}
```

Sign `txKind`, then submit via standard Sui transaction execution.

---

## Gas Optimization Checklist

1. **Always use cancel-and-place** — never separate cancel + place transactions
2. **Batch 5+ orders per transaction** — more orders per tx = lower gas per order
3. **Use Post-Only (orderType=2)** — guarantees maker rebate, avoids taker fees
4. **Use agent wallets** — grant an agent wallet via `grant-agent-wallet` to separate signing from the main account
5. **Use gas pool sponsorship** — pre-fund a gas pool and pass the `sponsor` field so agent wallets never need SUI
6. **Fund gas pool with USDC** — deposit USDC into gas pool, auto-swaps to SUI via Aftermath router
7. **Skip redundant oracle updates** — the API handles oracle freshness internally; don't add your own stork updates
8. **Quote both sides in one tx** — place bids AND asks in the same `ordersToPlace` array
9. **Leverage storage rebates** — cancelling orders returns a portion of the gas paid when placing them

---

## Quick Start Checklist

1. Create a perpetuals account via `POST /api/perpetuals/transactions/create-account`
2. Deposit USDC collateral via `POST /api/perpetuals/account/transactions/deposit-collateral`
3. Allocate collateral to your target market(s) via `POST /api/perpetuals/account/transactions/allocate-collateral`
4. (Optional) Grant an agent wallet for bot signing via `POST /api/perpetuals/account/transactions/grant-agent-wallet`
5. (Optional) Set up a gas pool for sponsorship via `POST /api/gas-pool/transactions/create`
6. (Optional) Fund gas pool with USDC via `POST /api/gas-pool/transactions/deposit` with `coinType` set to USDC
7. Start quoting with `POST /api/perpetuals/account/transactions/cancel-and-place-orders`

Full API docs: https://aftermath.finance/docs
