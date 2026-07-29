# Pinned upstream reference

This directory is a **verbatim, unedited** vendored copy of the official
Aftermath skills repository. It is reference material, not executable code —
nothing in this repository imports from it.

| | |
|---|---|
| Upstream | `AftermathFinance/skills` |
| Branch | `feat/v2-skills` |
| Commit | `5b614db62dcd2e58f442e93661f608fe7b073c32` |
| Commit date | 2026-07-14 |
| Commit subject | `feat: `aftermath-api` v3.0.0` |
| Skill version | `aftermath-api` **v3.0.0** |
| Vendored on | 2026-07-28 |
| Excluded | `.git/` only |

## Why it is vendored unedited

Recording the exact upstream SHA makes the next sync a **diff** rather than an
archaeology exercise: fetch the new upstream, diff against `5b614db`, and read
only what changed.

That value disappears the moment the copy is edited, so the known-wrong URLs
inside these files are **deliberately left in place**. See `README-DELTA.md`
for the full list of what is wrong and what this repository does instead.

## Re-syncing

```sh
git clone --branch feat/v2-skills https://github.com/AftermathFinance/skills /tmp/af-skills
git -C /tmp/af-skills diff 5b614db62dcd2e58f442e93661f608fe7b073c32 HEAD
rsync -a --exclude='.git' /tmp/af-skills/ AFTERMATH_SKILLS_REF/
```

Then update the commit row above, refresh `README-DELTA.md` if the URL count
changed, and re-run `python -m pytest tests/test_af_v2_hosts.py`.
