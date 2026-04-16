# Agent Wallets

Agent Wallets let you delegate trading permissions on a Perpetuals account to another wallet without giving up ownership of the account itself. This is useful for bots, automation, or managing permissions across an organization, while keeping a primary wallet as the account's admin.

### How it works

Each Perpetuals account is controlled by an admin capability. With Agent Wallets, the admin wallet can grant assistant-level permissions to a second wallet (the agent). The agent wallet can then execute supported trading actions on behalf of that account. **The agent can perform all actions except withdrawing collateral** and granting or revoking other agent wallets.

### Why use Agent Wallets

* Run automated strategies from a dedicated wallet
* Separate signing keys for risk management
* Keep long-term treasury/admin wallet isolated from execution
* Revoke permissions instantly if needed

> **Important:** Always use an agent wallet for any type of automation. Do not reuse your treasury/admin wallet for bot execution.

### Grant an Agent Wallet

Submit a transaction from the account admin wallet specifying:

* The `accountId`
* The `recipientAddress` — the wallet that should receive agent permissions

After on-chain confirmation, the recipient can trade on behalf of the account with assistant-level permissions.

**Endpoint:** `POST /api/perpetuals/account/transactions/grant-agent-wallet`

### Revoke an Agent Wallet

Submit a transaction from the account admin wallet specifying:

* The `accountId`
* The `accountCapId` of the assistant capability to revoke

After confirmation, the revoked wallet immediately loses its delegated permissions.

**Endpoint:** `POST /api/perpetuals/account/transactions/revoke-agent-wallet`

> Running bots? You don't need SUI in your agent wallet. See [Gasless Trading (GasPool)](gasless-trading.md) to sponsor gas from a central pool.
