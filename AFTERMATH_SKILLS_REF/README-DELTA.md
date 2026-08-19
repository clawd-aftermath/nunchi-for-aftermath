# Delta: local integration notes for the vendored skills

The skills in this directory are the authoritative reference for API patterns,
safety behaviours, gotchas, error handling, and monitoring. They are pinned at
`5b614db` and kept byte-identical so an upstream refresh remains reviewable.

## Production host

The launched V2 production API is `https://aftermath.finance`. The URLs already
present in the pinned skills now agree with production and should be used as
written. The former preview deployment still answers but exposes a stale
two-market universe; it is not a fallback.

As verified on 2026-08-19, the production `/api/perpetuals/all-markets`
endpoint returns 15 markets for native Sui USDC. Market object IDs are
deployment-specific and must always be discovered rather than copied from an
older environment.

### OpenAPI server configuration

The V2 spec's `servers` block identifies the production and testnet hosts:

```json
"servers": [
  { "url": "https://aftermath.finance",         "description": "Production server" },
  { "url": "https://testnet.aftermath.finance", "description": "Testnet server" },
  { "url": "http://localhost:8080",             "description": "Local development server" }
]
```

Generated clients should still read their base URL from the repository's one
config constant so future host changes do not require generated-code edits.

## What this repository does instead

| Concern | This repository |
|---|---|
| Host | One constant: `AF_API_BASE_URL_DEFAULT` in `cli/af/config.py`, default `https://aftermath.finance` |
| Override | `AF_API_BASE_URL` env var — no source edit needed |
| Call sites | All go through `cli/af/api.py`, which reads that one constant |
| Enforcement | `tests/test_af_v2_hosts.py` pins production and rejects the retired preview hostname |

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
