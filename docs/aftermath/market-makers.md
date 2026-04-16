# Market Makers

There is no designated market maker program, special rebates, or latency advantages. Anyone is welcome to market make on Aftermath Perpetuals.

For technical integration questions, join the [Discord](https://discord.gg/VFqMUqKHF3) or message on [X](https://x.com/AftermathFi).

## Recommended Integration

We recommend using the native Perpetuals REST endpoints (`/api/perpetuals/account/transactions/*`) rather than the CCXT layer. The native endpoints give you full control over gas management and access to Sui-native features like Programmable Transaction Blocks (PTBs).

All native endpoints return a `TxKindResponse` containing a base64-encoded `TransactionKind`. You decode it, wrap it in a full `Transaction`, sign with your wallet, and submit via your Sui client.

### Key Endpoints

**Cancel and Place Orders** (`POST /api/perpetuals/account/transactions/cancel-and-place-orders`)

This is the primary endpoint for market makers. It atomically cancels existing orders and places new ones in a single PTB, which is significantly cheaper on gas than separate cancel and place transactions. There is no intermediate state between the two operations, so your book depth never drops to zero mid-requote.

**Place Limit Order** (`POST /api/perpetuals/account/transactions/place-limit-order`)

For individual order placement. Supports inline SL/TP attachment in the same transaction.

**Place Market Order** (`POST /api/perpetuals/account/transactions/place-market-order`)

For immediate execution at market price. Also supports inline SL/TP.

**Place Scale Order** (`POST /api/perpetuals/account/transactions/place-scale-order`)

Distributes size across a price range in a single transaction. Useful for DCA-style entries, liquidity ladders, or spreading risk across multiple price levels.

### Real-Time Market Data

For live orderbook and trade updates, connect to the WebSocket endpoint:

`wss://aftermath.finance/api/perpetuals/ws/updates`

This supports subscriptions to orderbook updates, trade events, oracle prices, and account-level order and collateral changes. SSE streams are available under `/api/ccxt/stream/*`.

## Gas Optimization

The biggest gas savings come from how you structure your transactions. Using `cancel-and-place-orders` atomically instead of separate calls reduces gas by ~7x per order update.

**Inefficient: Separate Transactions**

```
TX 1: cancel_orders              → ~0.0016 SUI
TX 2: place_limit_order (×1)     → ~0.0023 SUI
────────────────────────────────────────────────
Total: ~0.004 SUI for 1 order update
```

**Efficient: Atomic Cancel-and-Place with Batching**

```
TX 1:
  cancel_orders                  ← cancel stale quotes
  place_limit_order (×5)         ← 5 new orders
────────────────────────────────────────────────
Total: ~0.002 SUI for cancel + 5 orders
Gas per order: ~0.0004 SUI
```

### Tips

* **Always use `cancel-and-place-orders`** — never send cancel and place as separate transactions.
* **Batch 5+ orders per call** — the `ordersToPlace` array accepts multiple orders. More orders per tx = lower gas per order.
* **Use Post-Only (`orderType: 2`)** — guarantees maker execution and avoids taker fees.
* **Quote both sides in one tx** — place bids and asks in the same `ordersToPlace` array.
* **Use agent wallets** — delegate signing to an agent wallet via `grant-agent-wallet` so your main key stays cold.
* **Use gas pool sponsorship** — pre-fund a gas pool and pass the `sponsor` field for predictable gas costs.
* **Don't add your own oracle updates** — the API handles oracle freshness internally.

### Example Request

```json
POST /api/perpetuals/account/transactions/cancel-and-place-orders

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
  "hasPosition": true
}
```

### CCXT

If your firm already runs on CCXT infrastructure, the CCXT standard interface is supported as well. Note that CCXT endpoints return `transactionBytes` and `signingDigest` rather than raw `TransactionKind`, so you have less control over gas and PTB composition.

Full API reference: https://aftermath.finance/docs
