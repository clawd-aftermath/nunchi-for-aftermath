# Delta: what these vendored skills get wrong

The skills in this directory are the authoritative reference for **patterns** —
safety behaviours, gotchas, error handling, monitoring. Their **URLs are dead**.

Take their patterns. Never their hostnames.

## The trap

`aftermath-api` v3.0.0 documents V2-only features while still naming the
**retired v1 host** throughout. As vendored at `5b614db` there are:

- **22** references to the bare `aftermath.finance` host
- **0** references to `v2-preview.aftermath.finance`

Verified as of 2026-07-28: `https://aftermath.finance` no longer serves the API
at all. Anything pointed there is broken — and broken quietly, which is worse.

### Where they are

| File | What it says |
|---|---|
| `skills/api/SKILL.md` | "Production OpenAPI" pointing at the dead host |
| `skills/api/ccxt.md` | REST base URL, plus `wss://aftermath.finance/...` stream URLs |
| `skills/api/auxiliary-endpoints.md` | dead host in the endpoint examples |
| `skills/api/gotchas.md` | dead host in an example |
| `skills/api/monitoring-patterns.md` | `const BASE_URL = "https://aftermath.finance"`, plus `wss://` URLs |
| `skills/api/safety-and-risk.md` | the `max-order-size` fetch example |
| `skills/api/.api-spec-state.json` | the spec URL it tracks |

Reproduce the count:

```sh
grep -rIo 'aftermath\.finance' AFTERMATH_SKILLS_REF | grep -v 'v2-preview' | wc -l   # 22
```

### The OpenAPI spec carries the same trap

This bites **code generation** specifically. The live V2 spec's own `servers`
block reads:

```json
"servers": [
  { "url": "https://aftermath.finance",         "description": "Production server" },
  { "url": "https://testnet.aftermath.finance", "description": "Testnet server" },
  { "url": "http://localhost:8080",             "description": "Local development server" }
]
```

That "Production server" entry is the **dead v1 host**. Any standard generator
(`openapi-typescript`, `openapi-generator`, `swagger-codegen`) bakes it in as
the default base URL, and the resulting client silently talks to a dead API.

If you ever generate a client from the spec: strip or override `servers` first,
make the generated client read its base URL from the one config constant, then
grep the output for the bare host and prove it clean.

## What this repository does instead

| Concern | This repository |
|---|---|
| Host | One constant: `AF_API_BASE_URL_DEFAULT` in `cli/af/config.py`, default `https://v2-preview.aftermath.finance` |
| Override | `AF_API_BASE_URL` env var — no source edit needed |
| Call sites | All go through `cli/af/api.py`, which reads that one constant |
| Enforcement | `tests/test_af_v2_hosts.py` fails on any bare-host reference outside `AFTERMATH_SKILLS_REF/` |

`https://v2-preview.aftermath.finance` **is production mainnet**, despite the
hostname. It is not a preview or a testbed.

## Patterns adopted from these skills

| Skill file | Implemented in |
|---|---|
| `safety-and-risk.md` — margin zones, 2% rule, two-tier breakers, kill switch, serialized deposits, refresh-after-mutation | `cli/af/safety.py`, `cli/af/proxy.py` |
| `gotchas.md` §1 — ID discipline | `cli/af/ids.py` (distinct classes per identity) |
| `gotchas.md` §6 — previews return 200 with an error body | `cli/af/api.py::preview` (tagged union, fails closed) |
| `gotchas.md` §11 — native BigInt `"123n"` wire format | `cli/af/ids.py::to_native_bigint` |
| `gotchas.md` §12 — `create-account` `deferShare` response shape | `cli/af/tx.py` (never assumes bare `{txKind}`) |
| `gotchas.md` §13 — no server-side dead-man switch | `cli/af/safety.py::KillSwitch` |
| `gotchas.md` §14 — `signatures[]` is plural | `cli/af/tx.py::sign_inspected` |
| `error-handling.md` — retry with backoff | `cli/af/api.py::request` |
| `gas/*` — sponsored / dynamic gas | `cli/af/gas.py` |
