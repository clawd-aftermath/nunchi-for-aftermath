---
name: sui-move-gas-optimization
description: How Sui charges gas for Move execution, the patterns that lead to wasted gas, and the tools for measuring and verifying optimizations.
version: 0.1.0
capabilities:
  - gas-analysis
  - bytecode-inspection
  - move-optimization
---

# Sui Move Gas Optimization

Sui's Move compiler does almost no optimization and lots of deoptimization; understanding what it *does* do lets you write source that costs much less without changing behavior.

## Files

| File | What's in it |
| --- | --- |
| [gas-model.md](./gas-model.md) | How sui charges gas. The three cost axes, tier multipliers, native call threshold, buckets. |
| [patterns.md](./patterns.md) | Source-level optimization patterns. Macro inlining and its caveats, branch convergence and condition expansion, working with tuples, returning efficiently. |
| [workflow.md](./workflow.md) | Tools for measuring optimization impact. `sui move test -s csv`, `--disassemble`, reading `.mvb` files, inspecting per-table `.mv` sizes. |
| [gotchas.md](./gotchas.md) | Non-obvious peculiarities. Miscellaneous. |

## When to use this skill

Full optimization usually trades readability for gas. Apply it deliberately on paths that measurably matter, not while writing fresh code. Keep the unoptimized version around as a (test-only) `_reference` companion — it serves as readable documentation, a behavioral oracle for tests, and a baseline for ongoing gas comparisons.

Always measure before and after. Optimization attempts often make things worse — the Move compiler's behavior is non-obvious.
