# Sui Gas Model

How sui charges gas for Move execution. Sourced from `sui-types/src/gas_model/` and `move-vm-runtime/src/interpreter.rs` in the sui repo.

Do not attempt optimization design without a reference clone of the sui repo. Always compare decisions against what the compiler literally does.

## Computation gas: three axes

Every bytecode instruction is charged along three independent axes, each with tiered pricing (aka cost buckets). Per instruction, the charge is:

```
internal_gas = (instruction_tier_mult × num_instructions)
             + (stack_size_tier_mult × abstract_size_delta)
             + (stack_height_tier_mult × num_pushes)
```

The final reported gas is `internal_gas / 1000` (with KeepHalfDigits rounding, minimum 1).

### Instructions

| Threshold | Multiplier |
| --- | --- |
| 0 | 1 |
| 20,000 | 2 |
| 50,000 | 10 |
| 100,000 | 50 |
| 200,000 | 100 |
| 10,000,000 | 1000 |

**Staying under 20,000 instructions is the cheapest regime.** Past that, costs scale superlinearly. A function that crosses 50,000 instructions in one call charges 10× per additional instruction. Keep this in mind when comparing gas measurements.

### Stack height

Every push (`Ld*`, `CopyLoc`, `MoveLoc`, each element of a function's return tuple, struct/enum unpack) increments height. Every pop (`StLoc`, arithmetic operand consumption, function call args, struct/enum pack) decrements height.

The stack height is high-water tracked. If the stack ever gets high enough, the fee kicks in. This is uncommon in practice but can be observed with a thousand-long line of `x + x + x + ... + x` for example.

| Threshold | Multiplier |
| --- | --- |
| 0 | 1 |
| 1,000 | 2 |
| 10,000 | 10 |

### Stack size (AbstractMemory)

Stack size is the running total of **abstract memory** of values on the operand stack — a synthetic memory-footprint metric. High-water tracked, sticky tier multipliers, same as stack height.

| Threshold | Multiplier |
| --- | --- |
| 0 | 1 |
| 100,000 | 2 |
| 500,000 | 5 |
| 1,000,000 | 100 |
| 100,000,000 | 1000 |

Per-type abstract-memory charges (from `move-vm-types/src/loaded_data/runtime_types.rs::Type::size`):

| Value type | Abstract size |
| --- | --- |
| Any primitive: `bool`, `u8`..`u256`, `address`, `signer` | 1 |
| Generic type parameter `T` | 1 |
| Reference: `&T`, `&mut T` | 8 |
| Struct value | 2 (envelope) + sum of field abstract sizes |
| Vector value | 8 (envelope) + sum of element abstract sizes |
| Constant loaded via `LdConst` | **BCS-serialized byte length** of the constant (e.g. 32 for a `u256`, potentially more for a `vector<u8>` literal) |

The primitives-all-cost-1 row is load-bearing: a `u256` arithmetic op charges 1, not 32. "Use smaller types to save gas" advice from other languages does not apply.

`LdConst` is the sole primitives operation that uses real serialized bytes. This makes constants more expensive to load than their `LdU<N>(literal)` equivalents. Use the macro-constant pattern.

## Native function gas

Native functions (Rust-implemented Move callables) return an `InternalGas` amount representing their work. Each native's cost params are declared in `sui-execution/latest/sui-move-natives/src/`. The struct `NativesCostTable` in `lib.rs` is the catalog; each of its fields is a per-native cost-params struct.

### The 700-call threshold

In `gas_predicates.rs`:

```
V2_NATIVE_FUNCTION_CALL_THRESHOLD = 700
```

- **< 700 native calls per transaction**: each native's returned gas is deducted directly from the budget. No interaction with instruction tiers.
- **≥ 700 native calls per transaction**: the returned gas is added as virtual instructions to the instruction count. From there it compounds with the instruction-tier multiplier — a transaction already in the 50,000-instruction tier pays 10× for every native-implied instruction added past the threshold.

### Accumulating native cost

Costs fall into one of three shapes:

| Shape | Form | Example natives |
| --- | --- | --- |
| Flat | `base` | `address::from_bytes`, `transfer::transfer_impl`, `tx_context::sender`, `object::borrow_uid` |
| Per-byte | `base + per_byte × size` | `bcs::to_bytes`, `string::*`, `type_name::get`, `address::to_string` |
| Per-byte + per-block | `base + per_byte × size + per_block × blocks` | `hash::blake2b256`, `hash::keccak256`, `hmac::hmac_sha3_256`, all `ecdsa_*::verify`, `ed25519::verify`, `bls12381::*`, `groth16::*` |

## Storage gas

Separate from computation. The unit of measurement is **BCS-serialized bytes** of the object (`Object::object_size_for_gas_metering` = `contents.len() + serialized_type_tag_size + 9` where `contents` is `bcs::to_bytes(move_struct_value)`). This is unlike the *abstract memory* metric used for computation — for storage, primitives cost their real BCS width (u256 = 32 bytes, u64 = 8, etc.) and structs/vectors cost their full serialized footprint.

Charged per object after execution:

- `storage_cost = object_size × obj_data_cost_refundable × storage_gas_price`
- `storage_rebate = previous_storage_cost × storage_rebate_rate` (for mutated/deleted objects)
- `non_refundable_fee = storage_rebate - sender_rebate` (kept by the system)

Storage cost is paid up-front when an object is created or its size grows; the rebate is paid back when it shrinks or is deleted.

I/O costs not in computation:
- **Object read**: `object_size × obj_access_cost_read_per_byte` (charged before execution, per loaded object).
- **Package publish**: `package_bytes × package_publish_cost_per_byte`.

## Post-execution rounding

After execution, raw computation gas is rounded before being multiplied by gas price. KeepHalfDigits (current): rounds up preserving ~half the significant digits, minimum 1000. E.g., 1001 → 1010, 20001 → 20100.

For test-stat scaling: a 1-instruction difference in a function called once contributes well below the rounding floor. To resolve small per-call differences, loop the call 1000× in a `#[test]` and divide.

## Final formula

```
computation_cost = rounded_gas_used × gas_price
storage_cost     = Σ(object_bytes × per_byte_cost × storage_gas_price)
storage_rebate   = Σ(old_storage_cost) × rebate_rate
user_pays        = computation_cost + storage_cost - storage_rebate
```

## Dynamic fields

Dynamic fields (DFs) and dynamic object fields (DOFs) are how objects hold variable or split state. Their cost profile is unusual and worth a dedicated mental model.

**They are expensive per operation.** Each move-level DF call (`df::add`, `df::borrow`, `df::remove`, `df::exists_`) lowers to **two natives**: `hash_type_and_key` first, then the matching `*_child_object`. Both charge per byte of key type, per byte of value, and per byte of struct tag; both count toward the 700-call threshold. DOFs wrap DFs, adding a second child-object native on the wrapped value — roughly 1.5–2.5× the natives of a plain DF per op. Loops over many fields are a realistic way to cross 700 native calls.

**But they suppress size costs.** Each DF is its own object on chain. The parent's stored bytes do not include any child's contents (`object_size_for_gas_metering` is just `contents.len() + type_tag + 9`). A transaction touching the parent pays read cost for the parent only; untouched children incur zero read and zero rebate change. Mutations charge per touched object's new size, independently.

**Use case**: splitting a large struct across DFs lets transactions pay storage I/O only for the slices they actually need. The tradeoff is the per-access native cost.

## Move macros are (almost) pure inlining

Move 2024 `macro fun` is compile-time inlining. The compiler expands macros during the typing phase. It inserts a named block for the macro scope and injects the macro body verbatim.

By HLIR, macro functions are gone — the body is injected into the caller's bytecode. There is **no `Call` bytecode** for a macro invocation.

This is the foundation of the macro-inlining optimization in `patterns.md`. It's also the source of the subtle binder-shuffle cost discussed there.

`return` inside a `macro fun` becomes a break to a labeled block, not a return from the enclosing function. Macro-author beware.
