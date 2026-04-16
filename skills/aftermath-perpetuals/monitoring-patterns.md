# Monitoring Patterns

> Practical monitoring patterns using CCXT and native Perpetuals endpoints.

---

## 1) Fast Market Scanner (Native Bulk Endpoints)

Use native bulk endpoints to reduce request fanout:

```typescript
const BASE_URL = "https://aftermath.finance";

async function scanMarkets() {
  const markets = await fetch(`${BASE_URL}/api/perpetuals/all-markets`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      collateralCoinType: "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC",
    }),
  }).then(r => r.json());

  const prices = await fetch(`${BASE_URL}/api/perpetuals/markets/prices`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ marketIds: markets.markets.map((m: any) => m.marketId) }),
  }).then(r => r.json());

  return { markets, prices };
}
```

---

## 2) Stream Updates

### CCXT SSE stream

```typescript
const es = new EventSource(`${BASE_URL}/api/ccxt/stream/orderbook?chId=0x...`);
es.onmessage = (event) => {
  const delta = JSON.parse(event.data);
  // apply orderbook deltas
};
```

### Native WebSocket proxy

```typescript
const ws = new WebSocket("wss://aftermath.finance/api/perpetuals/ws/updates");
ws.onmessage = (event) => {
  const update = JSON.parse(event.data);
  // handle multi-type perpetuals updates
};
```

---

## 3) Reconnect and Resync Rule

On stream reconnect:

1. Re-fetch snapshots (`/api/ccxt/orderbook`, positions, or native markets/orderbooks).
2. Replace local state atomically.
3. Resume delta processing.

Do not continue from stale in-memory state after disconnects.
