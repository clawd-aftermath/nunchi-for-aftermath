# Sub-Accounts

A single wallet can own multiple Perpetuals accounts. Each additional account acts as a sub-account, letting you isolate strategies, manage risk independently, and keep collateral separated, while all volume rolls up to a single fee tier.

## Why use Sub-Accounts

* **Strategy isolation**: Run a delta-neutral strategy on one account and a directional strategy on another, each with their own collateral and margin.
* **Risk separation**: A liquidation on one account doesn't affect the others.
* **Shared fee tier**: All accounts owned by the same wallet (or linked via the same master) share the same 14-day rolling volume for fee tier calculation.
* **Team operations**: Combine with [Agent Wallets](agent-wallets.md) to let different team members or bots trade on different sub-accounts while the master wallet retains admin control.

## Creating a Sub-Account

Creating a sub-account is the same as creating a regular account. Call the create-account endpoint from the same wallet that owns your primary account:

**Endpoint:** `POST /api/perpetuals/transactions/create-account`

```json
{
  "walletAddress": "<your-wallet>",
  "collateralCoinType": "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC"
}
```

Each call creates a new account with its own `accountId`. You can create as many as you need.

## Transferring Collateral Between Accounts

Move collateral between your accounts without withdrawing and re-depositing:

**Endpoint:** `POST /api/perpetuals/account/transactions/transfer-collateral`

```json
{
  "walletAddress": "<your-wallet>",
  "fromAccountId": 100,
  "toAccountId": 101,
  "amount": "50000000"
}
```

## Listing Your Accounts

Fetch all account capabilities owned by your wallet:

**Endpoint:** `POST /api/perpetuals/accounts/owned`

```json
{
  "walletAddress": "<your-wallet>",
  "collateralCoinType": "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC"
}
```

This returns every account your wallet controls, including both admin and agent capabilities.

## Fee Tier Rollup

Volume from all sub-accounts counts toward the master wallet's 14-day rolling volume. All accounts under the same wallet share the same fee tier. You don't need to concentrate volume on a single account to hit higher tiers.
