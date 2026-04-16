# Safety & Risk Management

> Read this when building anything that involves real funds, margin, or automated trading on Aftermath Perpetuals.

---

## Isolated Margin Model

Aftermath uses **isolated margin** — each position has its own margin allocation. A liquidation in one market does not directly drain collateral from other positions.

### Collateral Flow

```
Wallet USDC
  -> Deposit -> Account (unallocated collateral)
    -> Allocate -> Position A (isolated margin)
    -> Allocate -> Position B (isolated margin)
```

Unallocated collateral sits in the account but is NOT protecting any position. You must explicitly allocate margin to each position.

---

## Circuit Breakers

Automated safety limits for trading bots. Implement these before going live.

### Tier 1: Soft Limits (Log Warnings)

```typescript
interface SoftLimits {
  maxDrawdownPct: number;      // e.g., 0.05 = 5%
  maxPositionNotional: number; // e.g., 50000 USDC
  maxLeverage: number;         // e.g., 5
  minMarginBuffer: number;     // e.g., 2.0 = 2x maintenance margin
}
```

### Tier 2: Hard Limits (Stop Trading)

```typescript
interface HardLimits {
  maxDrawdownPct: number;      // e.g., 0.15 = 15%
  maxDailyLoss: number;        // e.g., 5000 USDC
  maxDailyTrades: number;      // e.g., 200
}
```

---

## Kill Switch Pattern

Aftermath has **no built-in dead man's switch**. Bots must implement their own heartbeat-based kill switch that cancels all open orders when the strategy loop stalls.

---

## Pre-Launch Checklist

Before deploying any bot to mainnet:

- [ ] Tested all logic on testnet (`https://testnet.aftermath.finance`)
- [ ] Circuit breakers implemented and tested (both soft and hard limits)
- [ ] Kill switch implemented with heartbeat timeout
- [ ] Position sizing enforced (never exceeds risk limits)
- [ ] Error handling covers all failure modes (see error-handling.md)
- [ ] Logging captures all order submissions, fills, and errors
- [ ] SIGINT/SIGTERM handlers cancel all orders on shutdown
- [ ] Deposit operations are serialized (no parallel deposits)
- [ ] Account state is refreshed after every order/fill/cancel
