# Optimization Patterns

Source-level patterns that reduce gas without changing semantics. Each describes the problem the compiler creates and the source change that eliminates or reduces it.

## 1. Replace function calls with macros — when the body is small enough

A `Call` costs 1 instruction plus argument push-pop. A `macro fun` produces no `Call` and no new stack — the body is inlined at the typing phase.

```move
// Before
public fun is_neg(x: u256): bool { x >= 0x8000_..._0000 }
let n = is_neg(rate);

// After
public macro fun is_neg($x: u256): bool { $x >= 0x8000_..._0000 }
let n = is_neg!(rate);
```

### Caveat: value-returning macros incur a binder shuffle

The macro expander wraps every macro body in a `NamedBlock` (to support `return` from inside a macro). When that named block is in **value position inside a surrounding binop**, HLIR allocates a fresh local, stores the macro's result into it, and reads it back to consume — a `StLoc` + `MoveLoc` pair per call. Visible in disassembly as `%#N` locals.

### What we know about when macroizing helps vs hurts

| Situation | Observed behavior |
| --- | --- |
| Macro consumed in `if`-condition position (`if (m!(...)) { ... }`) | No binder; saves the `Call` overhead cleanly. Win. |
| Macro is the entire RHS of an `let x = m!(...);` assignment | No binder; saves the `Call`. Win. |
| Macro is the tail expression of a function body | No binder; saves the `Call`. Win. |
| Macro returns `()` (statement-only macro) | No binder; saves the `Call`. Win. |
| Macro on the LHS of a binop, with a plain local on the RHS | No binder. Win. |
| Macro on the RHS of a binop with another macro or computed expression on the LHS | Binder shuffle on both sides. The flip (Pattern 2) can recover the LHS case for commutative ops. |
| Macro nested inside another macro's arguments, both value-returning | Each inner macro's NamedBlock triggers a binder for its sibling in the outer binop. Costs compound. |
| Trivial accessor macro (body is 1 op) in value position | Binder shuffle costs as much as the saved `Call`. Often neutral; can be worse than the function. |

### Package-size cost of inlining

A macro body is copy-pasted into every callsite during typing. The bytecode of the body appears once per call in the compiled `.mv` — there is no shared implementation. This bloats the `function_defs` table and contributes to the on-chain package size limit.

**Practical implication**: macroize **small** bodies. A 3-op macro called from 50 sites adds ~150 ops of bytecode. The same call as a function would be 1 `Call` instruction (and stack setup/teardown) per site + the function body once.

## 2. The commutative flip (macro on LHS, not RHS)

The HLIR pass `process_binops` enforces left-to-right evaluation order. When building `lhs OP rhs`, if lowering `rhs` produced any statements, the compiler hoists `lhs` into a fresh local — to keep `lhs` from being computed after the RHS's statements run. A value-returning macro always lowers to a NamedBlock (a statement-producing expression), so it triggers the hoist whenever it sits on the RHS. Macros on the LHS, including macros wrapping other macros, do not.

```move
//   neg_a == is_neg!(b)
// triggers binder hoist for `neg_a`. Becomes:
//   tmp1 = neg_a
//   tmp2 = is_neg!(b) evaluated
//   tmp1 == tmp2
```

Swap the operands when the operator is commutative:

```move
// Before — 10 ops, 2 binders
neg_a == is_neg!(b)
acc + macros::negate!(b)

// After — 6 ops, 0 binders
is_neg!(b) == neg_a
macros::negate!(b) + acc
```

## 3. Eliminate branch-convergence locals

Bad:
```move
let result = if (cond_a) {
    // ... work ...
    X
} else if (cond_b) {
    // ... work ...
    Y
} else {
    0
};
// later: use result
```

Good:
```move
let result;
if (cond_a) {
    // ... work ...
    result = X;
} else if (cond_b) {
    // ... work ...
    result = Y;
} else {
    result = 0;
}
// later: use result
```

The compiler allocates a local for `result` and emits `StLoc result; Branch end` at every branch's tail, then `MoveLoc result` after the join. That's `2 × branches + 1` ops of pure shuffle.

If the branches sit at the function's tail and nothing meaningful happens after the join, return from each branch directly:

```move
fun foo(...): (u256, bool) {
    if (cond_a) {
        // ... work ...
        return (X, ...)
    } else if (cond_b) {
        // ... work ...
        return (Y, ...)
    }
    return (0, ...)
}
```

## 4. Restructure branches to thread sign decisions naturally

Often a function's logic is "if positive, do A; if negative, do B." A `let neg = is_neg!(x);` followed by an `if (neg) { ... } else { ... }` is fine. But if A and B *each* branch on the same sign, the outer branch already knows the sign — propagate it through structure rather than re-checking:

```move
// Worse: each branch re-tests
if (small) { ... if (is_neg!(x)) { ... } else { ... } ... }
else      { ... if (is_neg!(x)) { ... } else { ... } ... }

// Better: split on sign first
if (is_neg!(x)) {
    if (small) { ... } else { ... }
} else {
    if (small) { ... } else { ... }
}
```

## 5. Use macros for constants instead of `const`

For constants, `public macro fun foo(): u256 { 0x... }` is much cheaper than `const FOO: u256 = 0x...;`. The macro lowers to `LdU256(literal)` (1 byte stack-size charge); the const lowers to `LdConst` (BCS-serialized length, 32 bytes for u256). See `gas-model.md` for the mechanism.

Applies to `bool` too. Does not apply to vector constants.
