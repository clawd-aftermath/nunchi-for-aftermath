# Gotchas & Edge Cases

> Common pitfalls when integrating with the public API at `https://aftermath.finance/docs`.

---

## 1) Account ID vs Account Object ID

- CCXT write endpoints use `accountId` as an account capability object ID (`0x...`).
- CCXT read/stream endpoints use `accountNumber` (number).
- Native Perpetuals account endpoints use numeric `accountId`.

Mixing these is one of the most common integration failures.

## 2) Build -> Sign -> Submit Is Mandatory

For CCXT writes: `POST /api/ccxt/build/*` -> sign `signingDigest` -> `POST /api/ccxt/submit/*`

Sign the `signingDigest`, not `transactionBytes`.

## 3) CCXT `OrderRequest` Is Minimal

Current schema supports: `type`, `side`, optional `amount`, `price`, `reduceOnly`, `expirationTimestampMs`. Do not assume `clientOrderId`, `timeInForce`, or `postOnly` are accepted.

## 4) Native History Endpoints Are Cursor-Based

`/api/perpetuals/account/order-history` paginates with `beforeTimestampCursor`. Keep the cursor from each response for full history backfill.

## 5) Stop-Order Data Requires Signed Auth

`/api/perpetuals/account/stop-order-datas` requires auth payload fields (`walletAddress`, `bytes`, `signature`).

## 6) Preview Endpoints Can Return 200 With Error Payload

Some preview routes return HTTP `200` with `{ error: string }`. Treat preview responses as tagged unions.

## 7) Coin and Gas Object Concurrency Is Real

Concurrent signed transactions can race on shared Sui objects (USDC coin objects, gas coins) and fail with version/equivocation-style errors. Serialize critical funding operations.

## 8) Account State Is Quickly Stale

After any fill/cancel/withdraw/deposit/leverage update, refresh account and position state before computing new risk or order decisions.

## 9) Native Integer Fields May Arrive as Strings

Native `accountId`, timestamps, order IDs, and amount-like fields are sometimes documented as decimal strings, optionally with trailing `n`.

## 10) Deferred Create Flows Change Response Shape

`/api/perpetuals/transactions/create-account` can return deferred PTB argument references when `deferShare = true`.

## 11) No Built-In Dead Man's Switch

There is no protocol-level scheduled cancel safety net. Implement a heartbeat-driven kill switch.

## 12) CCXT Submit May Require Multiple Signatures

`/api/ccxt/submit/*` accepts `signatures[]`. When sender and gas owner are different, collect both signatures.

## 13) Sender and Sponsor CAN Be the Same Address

Sui sponsored transactions do NOT require different sender and sponsor addresses. The sponsor is usually a separate address, but it is not a protocol-level constraint.
