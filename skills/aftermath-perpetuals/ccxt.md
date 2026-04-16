# CCXT Endpoint Reference

> CCXT-compatible endpoints under `/api/ccxt/*`.

Use CCXT when you need exchange-style request/response compatibility. For full Aftermath feature coverage, prefer native perpetuals endpoints in `native.md`.

Verified against OpenAPI: `https://aftermath.finance/api/openapi/spec.json`

---

## Endpoint Groups

### Public market data

```text
GET  /api/ccxt/markets
GET  /api/ccxt/currencies
POST /api/ccxt/orderbook
POST /api/ccxt/ticker
POST /api/ccxt/OHLCV
POST /api/ccxt/trades
```

### Account reads

```text
POST /api/ccxt/accounts
POST /api/ccxt/balance
POST /api/ccxt/positions
POST /api/ccxt/myPendingOrders
```

### Signed writes (build -> sign -> submit)

```text
POST /api/ccxt/build/createOrders   -> POST /api/ccxt/submit/createOrders
POST /api/ccxt/build/cancelOrders   -> POST /api/ccxt/submit/cancelOrders
POST /api/ccxt/build/createAccount  -> POST /api/ccxt/submit/createAccount
POST /api/ccxt/build/deposit        -> POST /api/ccxt/submit/deposit
POST /api/ccxt/build/withdraw       -> POST /api/ccxt/submit/withdraw
POST /api/ccxt/build/allocate       -> POST /api/ccxt/submit/allocate
POST /api/ccxt/build/deallocate     -> POST /api/ccxt/submit/deallocate
POST /api/ccxt/build/setLeverage    -> POST /api/ccxt/submit/setLeverage
```

### Streams

```text
GET /api/ccxt/stream/orderbook?chId={marketId}
GET /api/ccxt/stream/orders?chId={marketId}
GET /api/ccxt/stream/positions?accountNumber={number}
GET /api/ccxt/stream/trades?chId={marketId}
```

---

## CCXT IDs

| Field | Meaning |
|---|---|
| `chId` | Market object ID |
| `accountId` | Account capability object ID (for writes) |
| `accountNumber` | Numeric account identifier (for reads/streams) |
| `account` | Balance lookup identifier accepted by `/api/ccxt/balance` |

---

## Request Types (Current Schema)

```typescript
interface OrderRequest {
  chId: string;
  type: "market" | "limit";
  side: "buy" | "sell";
  amount?: number;
  price?: number;
  reduceOnly?: boolean;
  expirationTimestampMs?: number;
}

interface TransactionMetadata {
  sender: string;
  gasBudget?: number;
  gasPrice?: number;
  sponsor?: string;
  gasCoins?: Array<{ objectId: string; version: string | number; digest: string }>;
}

interface TransactionBuildResponse {
  transactionBytes: string;
  signingDigest: string;
}

interface SubmitTransactionRequest {
  transactionBytes: string;
  signatures: string[];
}
```

Notes:
- Sign `signingDigest`, not `transactionBytes`.
- `signatures` can contain multiple signatures (for example sender + separate gas owner/sponsor signer).
- `TransactionMetadata.sponsor` accepts a wallet address for gas pool sponsorship.
- `TransactionMetadata.gasCoins` allows specifying explicit gas coin objects.

---

## Source of Truth

- Swagger UI: `https://aftermath.finance/docs`
- OpenAPI JSON: `https://aftermath.finance/api/openapi/spec.json`
