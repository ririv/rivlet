# Experimental mixed execution and extension SDK

English | [简体中文](./zh-CN/mixed-execution.md)

**Experimental as one feature family:** Python workflows containing Rust-native
actions or predicates, independent extension exports, the binding SDK, the C ABI
bridge, native value conversions, and the shared native coordinator.
These APIs, protocol details and the feature itself may change or be removed.
Entry points stay in their existing modules; there is no experimental namespace
migration or implicit runtime warning. The SDK exports only concrete nodes through
`export_node`; Graph body reuse uses the imported Graph's `.structure`.

This status does **not** apply to ordinary Python actions/conditions using the Rust
core compiler, Python sync/coroutine mixing, or the pure Rust declaration and
Execution::run APIs. Using a Rust scheduler is not cross-language action execution.

## Boundaries that allow removal

- Core/topology know no language objects, bridge callbacks or foreign value schemas.
- Ordinary Python sessions use `host_execution.rs` and the existing core session
  directly. Their actions, predicates, dynamic subgraphs and concurrency do not
  enter the native coordinator.
- Only actual native action/predicate declarations select `mixed_execution.rs`.
  This is explicit dispatch, not retry/fallback after an execution error.
- The coordinator is under `crates/execution/src/coordinator`, behind the
  non-default `experimental-mixed-execution` Cargo feature. The Python adapter
  explicitly enables it; the pure Rust driver works without it.
- SDK and ABI ownership stay in `crates/python-sdk` and `crates/bridge`, not core.
  General Graph/Step/GraphStructure/ports/result APIs do not depend on SDK types.

The Python wheel currently includes this experiment. The Cargo feature above is a
Rust module boundary, **not** a switch that already produces a Python-only wheel.

## Removal procedure

Removal is bounded cleanup, not a promise that deleting one directory is enough:

- Retain the ordinary Python host-session branch; remove the native branch from the
  private session factory. The private `mixed_session` method name is retained today
  for the existing driver protocol; it is not evidence of native work in a host session.
- Remove native collection/import hooks and native-only inspection branches at the
  declaration adapter boundary. Keep ordinary GraphStructure snapshots and APIs.
- Remove the private native Step binding, imported root-predicate branch and
  `_from_native` constructors from the shared declaration types. Keep ordinary
  Python callable and condition construction unchanged.
- Remove native result codecs/wrappers, Rust-specific compiled records and their
  exports. These experimental records are not ordinary Python result storage.
- Remove SDK capsule registration, the SDK/bridge dependencies, coordinator feature
  activation and the isolated experimental modules/examples/tests.
- Leave core scheduling and the pure Rust high-level driver unchanged. Generic core
  accessors/completion composition need not be reverted.

No ordinary recipe or application should need a replacement Graph API. Consumers
that explicitly use native subgraphs must migrate; experimental status does not
mean their behavior can be silently replaced with Python or subprocess fallbacks.

## Verification

```sh
cargo test --locked -p execution --no-default-features
cargo test --locked -p execution --features experimental-mixed-execution
python -m pytest tests/test_experimental_isolation.py
just test-native-bridge
```

The first command checks the pure Rust path without the coordinator. Isolation tests
check that ordinary and runtime-returned Python graphs select HostExecutionSession,
while native declarations select MixedExecutionSession. They do not claim that the
entire Python build can already omit all experimental source files.

See [native graph composition](./rust-graphs.md), [independent extensions](./python-native.md)
and [shared coordination](./rust-architecture.md#shared-native-execution-coordination).

The unimplemented autonomous/unified driver candidates are recorded separately in
[the driver-boundary discussion](./design/mixed-driver-boundary.md).
