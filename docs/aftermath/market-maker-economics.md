# Market Maker Economics

Cost breakdown, fee structure, and operational considerations for market makers on Aftermath Perpetuals.

## Total Cost Per Order

The total cost of placing or refreshing an order has two components:

1. **Trading fee** (charged on fill, not on placement)
2. **Gas cost** (charged on every transaction, regardless of fill)

### Trading Fees

Fees are tiered based on rolling 14-day volume. Makers pay significantly less than takers, and at higher tiers receive **negative fees (rebates)** — meaning you get paid when your resting orders are filled.

**Growth Mode is live** — all maker fees are **-0.5 bps (-0.005%)** across every tier. You get paid when your resting orders are filled.

| Tier | 14-Day Volume     | Maker Fee | Taker Fee |
| ---- | ----------------- | --------- | --------- |
| 0    | $0+               | -0.005%   | 0.045%    |
| 1    | $5M+              | -0.005%   | 0.040%    |
| 2    | $10M+             | -0.005%   | 0.038%    |
| 3    | $25M+             | -0.005%   | 0.035%    |
| 4    | $50M+             | -0.005%   | 0.033%    |
| 5    | $100M+            | -0.005%   | 0.030%    |
| 6    | $250M+            | -0.005%   | 0.029%    |
| 7    | $500M+            | -0.005%   | 0.028%    |
| 8    | $1B+              | -0.005%   | 0.027%    |
| 10   | $2B+              | -0.005%   | 0.026%    |

**Key point:** Trading fees are only charged when an order is **filled**, not when it is placed or cancelled. Quoting (placing/cancelling resting orders) costs only gas.

### Direct Maker Rebates

In addition to the fee tiers above, makers who maintain consistent market share receive direct rebates paid out biweekly:

| Tier | Market Share | Direct Rebate         |
| ---- | ------------ | --------------------- |
| T1   | ≥ 0.5%      | 0.001% (0.1 bps)     |
| T2   | ≥ 1.5%      | 0.002% (0.2 bps)     |
| T3   | ≥ 3%        | 0.004% (0.4 bps)     |
| T4   | ≥ 5%        | 0.008% (0.8 bps)     |

### Gas Costs

Aftermath Perpetuals runs on Sui, where gas costs are extremely low:

| Operation          | Avg Cost (SUI) | Avg Cost (USD) | Notes                                    |
| ------------------ | -------------- | -------------- | ---------------------------------------- |
| Place order        | ~0.002 SUI     | ~$0.002        | Includes computation + storage deposit   |
| Cancel order       | ~0.001 SUI     | ~$0.001        | Storage rebate offsets most of the cost   |

Sui's storage rebate model means that **cancelling an order returns a portion of the gas paid when placing it**. For market makers who continuously refresh quotes, this rebate significantly reduces the effective cost of quoting.

Gas is the only cost incurred when placing, cancelling, or refreshing orders that do not fill.

## Programmable Transaction Blocks (PTBs)

PTBs are a core feature of the Sui blockchain that allow multiple operations to be composed into a **single atomic transaction**. This is critical for market makers because it means you can:

- **Cancel old orders and place new orders in one transaction** — no gap where you have no orders on the book
- **Update quotes across multiple price levels atomically** — your entire grid refreshes at once
- **Pay gas only once** for what would be multiple transactions on other chains

### Cancel-and-Place Endpoint

Aftermath provides a dedicated endpoint that builds a PTB to atomically cancel existing orders and place new ones in a single transaction:

`POST /api/perpetuals/account/transactions/cancel-and-place-orders`

This eliminates the "naked" period between cancelling stale quotes and posting fresh ones, which on other platforms creates adverse selection risk.

### What This Means in Practice

| Operation                     | Other Chains             | Aftermath (via PTB)      |
| ----------------------------- | ------------------------ | ------------------------ |
| Cancel 5 orders + place 5 new | 10 transactions, 10× gas | 1 transaction, 1× gas    |
| Time exposed with no quotes   | Seconds to minutes       | Zero (atomic swap)       |
| Failure mode                  | Partial execution risk   | All-or-nothing           |

## Adverse Selection Protection (RGP)

Reference Gas Price (RGP) is Aftermath's mechanism to protect makers from toxic flow. On most exchanges, fast/informed takers exploit stale maker quotes by paying higher gas for sequencing priority.

RGP detects this by tracking a gas price baseline and flagging taker transactions that aggressively overpay. Flagged transactions pay a higher execution fee, which is **redistributed as rebates to market makers**.

This means:
- The more toxic flow targets your quotes, the more you earn in RGP rebates
- Retail takers trading at normal gas prices are unaffected
- No cancel prioritization needed — works within Sui's existing execution model

## Operational Setup

### Agent Wallets

Market makers should use **Agent Wallets** to separate signing keys from the account admin wallet. The agent wallet can execute all trading actions (place, cancel, modify orders) but **cannot withdraw collateral**. This limits blast radius if a bot key is compromised.

- Grant via: `POST /api/perpetuals/account/transactions/grant-agent-wallet`
- Revoke via: `POST /api/perpetuals/account/transactions/revoke-agent-wallet`

### Sub-Accounts

Volume from sub-accounts rolls up to the master account for fee tier calculation. Run multiple strategies across sub-accounts while benefiting from a single fee tier.

### Integration Options

| Method         | Best For                        |
| -------------- | ------------------------------- |
| REST API       | Full control, custom strategies |
| CCXT           | Firms with existing CCXT infra  |
| TypeScript SDK | Fastest integration path        |

## Summary

| Cost Component  | When Charged       | Typical Cost                              |
| --------------- | ------------------ | ----------------------------------------- |
| Gas (place)     | Every transaction  | ~0.002 SUI (~$0.002)                      |
| Gas (cancel)    | Every transaction  | ~0.001 SUI (~$0.001), offset by rebate    |
| Maker fee       | On fill only       | -0.005% (Growth Mode — all tiers)         |
| Maker rebate    | Biweekly payout    | Up to 0.008% back (by market share)       |
| RGP rebate      | On toxic flow      | Variable, redistributed from toxic takers  |

**Bottom line:** With Growth Mode active, makers earn -0.5 bps on every fill — the only recurring cost for quoting is gas (~$0.002 per order, with cancels partially refunded via Sui storage rebates). PTBs eliminate the multi-transaction overhead and execution risk that make market making expensive on other chains.
