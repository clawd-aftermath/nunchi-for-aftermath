# TypeScript SDK Reference

> Complete reference for `@aftermath-finance/sdk`. Use this for frontend apps, complex integrations, and vault management.

---

## Initialization

```typescript
import { Aftermath } from "@aftermath-finance/sdk";

const afSdk = new Aftermath("MAINNET");  // or "TESTNET"
await afSdk.init();
const perps = afSdk.Perpetuals();
```

### SDK Class Hierarchy

- `Perpetuals` - Main entry point. Market/vault/account discovery, tx builders, WebSocket, pricing.
- `PerpetualsMarket` - Wrapper around a single market. Orderbook, stats, previews, max order size, rounding.
- `PerpetualsAccount` - Wrapper around a trading account. Collateral mgmt, order placement, previews, history.
- `PerpetualsVault` - Wrapper around an afLP vault. Deposits, withdrawals, admin ops, LP pricing.

---

## Account Management

```typescript
const { accounts: caps } = await perps.getOwnedAccountCaps({ walletAddress: "0x..." });
const { account } = await perps.getAccount({ accountCap: caps[0] });
account.collateral();          // Available collateral
account.accountId();           // Numeric account ID
```

## Order Placement

```typescript
// Market order
const { tx } = await account.getPlaceMarketOrderTx({
  marketId: "0x...",
  side: PerpetualsOrderSide.Bid,
  size: 1n * Casting.Fixed.fixedOneN9,
  collateralChange: 5000,
  reduceOnly: false,
  leverage: 5,
});

// Limit order
const { tx } = await account.getPlaceLimitOrderTx({
  marketId: "0x...",
  side: PerpetualsOrderSide.Bid,
  size: 1n * Casting.Fixed.fixedOneN9,
  price: 42000,
  collateralChange: 5000,
  reduceOnly: false,
  orderType: PerpetualsOrderType.PostOnly,
});

// Cancel orders
const { tx } = await account.getCancelOrdersTx({
  marketIdsToData: {
    "0xMARKET_ID": { orderIds: [12345n], collateralChange: -1000, leverage: 5 }
  }
});
```

## Collateral Management

```typescript
await account.getDepositCollateralTx({ depositAmount: 10n * 1_000_000n });
await account.getWithdrawCollateralTx({ withdrawAmount: 5n * 1_000_000n, recipientAddress: "0x..." });
await account.getAllocateCollateralTx({ marketId: "0x...", allocateAmount: 10n * 1_000_000n });
await account.getDeallocateCollateralTx({ marketId: "0x...", deallocateAmount: 5n * 1_000_000n });
await account.getTransferCollateralTx({ transferAmount: 10n * 1_000_000n, toAccountId: 456n });
```

---

## Fee Structure

See the official docs for current fee tiers: `https://aftermath.finance/docs`
