# Gasless Trading (GasPool)

If you are running bots or programmatic strategies, you don't need to fund every agent wallet with SUI for gas. Instead, you can use a **GasPool** to sponsor your agent wallet's transactions from a single prepaid balance.

Think of the GasPool like a prepaid gas card. You load it with SUI (or USDC) from your primary wallet, and whenever your agent wallet executes a trade, the protocol automatically pulls the gas cost from the pool. Your agent wallet only ever needs to hold USDC collateral.

## How it works

Your primary wallet creates a GasPool, deposits funds into it, and whitelists your agent wallet. From that point on, when the agent wallet calls any trading endpoint it includes a `sponsor` field pointing to the primary wallet. The API builds the transaction, draws gas from the pool, and returns both a `txKind` and a `sponsorSignature`. The agent wallet signs the transaction and submits it with both signatures.

The agent wallet never touches SUI. You just top up the GasPool from your primary wallet when the balance gets low.

## Setup

### Step 1: Create a GasPool

From your primary wallet, create and fund a GasPool in a single transaction:

**Endpoint:** `POST /api/gas-pool/transactions/create`

```json
{
  "walletAddress": "<your-primary-wallet>",
  "deferShare": false,
  "initialDepositAmount": 1000000000
}
```

`initialDepositAmount` is in MIST (1 SUI = 1,000,000,000 MIST). You can also deposit later.

### Step 2: Deposit (optional top-up)

**Endpoint:** `POST /api/gas-pool/transactions/deposit`

Deposit SUI directly:

```json
{
  "walletAddress": "<your-primary-wallet>",
  "amount": 500000000
}
```

Or deposit USDC (or any other coin). The endpoint automatically swaps it to SUI via the Aftermath router before depositing:

```json
{
  "walletAddress": "<your-primary-wallet>",
  "coinType": "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC",
  "amount": 5000000,
  "slippage": 0.01
}
```

This means you can fund your GasPool without needing to hold SUI at all. Just deposit from whatever token you already have.

### Step 3: Grant access to your agent wallet

**Endpoint:** `POST /api/gas-pool/transactions/grant`

```json
{
  "walletAddress": "<your-primary-wallet>",
  "targetWalletAddress": "<your-agent-wallet>"
}
```

### Step 4: Execute sponsored trades

When calling any `/api/perpetuals/account/transactions/...` endpoint from your agent wallet, include the `sponsor` field:

```json
{
  "walletAddress": "<your-agent-wallet>",
  "accountId": 424,
  "sponsor": {
    "walletAddress": "<your-primary-wallet>"
  }
}
```

The API returns:

* `txKind` — base64-encoded transaction for the agent wallet to sign
* `sponsorSignature` — the gas sponsor's signature

Your agent wallet signs the `txKind`, then submits the transaction with both signatures.

## Managing your GasPool

| Action | Endpoint |
| --- | --- |
| Check balance and whitelist | `POST /api/gas-pool/pool` |
| Deposit more funds | `POST /api/gas-pool/transactions/deposit` |
| Withdraw SUI | `POST /api/gas-pool/transactions/withdraw` |
| Grant access | `POST /api/gas-pool/transactions/grant` |
| Revoke access | `POST /api/gas-pool/transactions/revoke` |

## Alternative: local sponsorship

If you prefer not to use the GasPool, you can sponsor transactions yourself by having your primary wallet co-sign the transaction payload as the gas sponsor before submission. This gives you full control over the sponsorship flow without going through the GasPool infrastructure.
