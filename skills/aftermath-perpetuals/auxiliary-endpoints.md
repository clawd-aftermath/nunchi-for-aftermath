# Auxiliary Endpoint Families

> Quick routing for public API families outside core CCXT/native perpetual trading docs.

---

## Gas Pool

```text
POST /api/gas-pool/pool
POST /api/gas-pool/transactions/create
POST /api/gas-pool/transactions/deposit
POST /api/gas-pool/transactions/withdraw
POST /api/gas-pool/transactions/grant
POST /api/gas-pool/transactions/revoke
POST /api/gas-pool/transactions/share
POST /api/gas-pool/transactions/sponsor
```

### 1CT (1-Click Trading) Agent Wallet Flow

The recommended setup for programmatic trading with sponsored gas. Agent wallets only need USDC — no SUI management required.

**Setup steps:**

1. **Grant agent wallet**: Create a second address, grant it via `POST /api/perpetuals/account/transactions/grant-agent-wallet`
2. **Create GasPool**: `POST /api/gas-pool/transactions/create` — deposit SUI via `/transactions/deposit`
3. **Authorize agent**: `POST /api/gas-pool/transactions/grant` with agent's `targetWalletAddress`
4. **Trade with sponsorship**: Include `sponsor.walletAddress` in any `/api/perpetuals/account/transactions/*` request

**How it works:**

- API returns `txKind` (base64) + `sponsorSignature` when sponsor is specified
- Agent wallet signs the transaction, then submit with both signatures (agent + sponsor)
- GasPool acts as a **prepaid SUI pool** — debited per sponsored tx, refill periodically from primary address

**USDC as gas:** Deposit USDC (or any token) into the gas pool via:

```json
POST /api/gas-pool/transactions/deposit
{
  "walletAddress": "0x...",
  "coinType": "0xdba34672e30cb065b1f93e3ab55318768fd6fef66c15942c9f7cb846e2f900e7::usdc::USDC",
  "amount": 5000000,
  "slippage": 0.01
}
```

The endpoint auto-swaps to SUI via the Aftermath router. Your agent wallet never needs to hold SUI.

---

## Perpetual Utility Transactions

```text
POST /api/perpetuals/transactions/create-account
POST /api/perpetuals/transactions/transfer-cap
POST /api/perpetuals/account/transactions/share
```

---

## Builder Codes (Integrator)

```text
POST /api/perpetuals/builder-codes/integrator-config
POST /api/perpetuals/builder-codes/integrator-vaults
POST /api/perpetuals/builder-codes/transactions/create-integrator-config
POST /api/perpetuals/builder-codes/transactions/remove-integrator-config
POST /api/perpetuals/builder-codes/transactions/create-integrator-vault
POST /api/perpetuals/builder-codes/transactions/claim-integrator-vault-fees
```

---

## Referrals

```text
POST /api/referrals/availability
POST /api/referrals/create
POST /api/referrals/link
POST /api/referrals/linked-ref-code
POST /api/referrals/query
POST /api/referrals/ref-code
```

---

## Rewards

```text
POST /api/rewards/claimable
POST /api/rewards/history
POST /api/rewards/points
POST /api/rewards/transactions/claim
```

---

## Source of Truth

- Swagger UI: `https://aftermath.finance/docs`
- OpenAPI JSON: `https://aftermath.finance/api/openapi/spec.json`
