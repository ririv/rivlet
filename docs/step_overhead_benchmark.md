# Node overhead benchmark

English | [简体中文](./zh-CN/step_overhead_benchmark.md)

This records the 2026-10-10 A/B measurement of the synchronous-action fast path.
It predates later changes such as failure isolation and is not a claim about
current performance on arbitrary machines. Original measurements are preserved.

## Scope and conclusion

Declaration, compilation, and execution were measured separately to locate fixed
costs for short Python actions. Empty actions expose scheduling overhead, not
compiler, network, or disk throughput.

The A/B comparison isolates return-value handling: the baseline creates an event
loop for every result; the fast path returns ordinary values directly and creates
an event loop only for awaitables. Other topology, scheduling, logging, and worker
settings remain unchanged.

## Environment and method

- Apple M1 Pro, arm64, macOS 26.4 (25E246).
- Python 3.13.5.
- 2026-10-10; the installed Rust extension was not rebuilt between groups.
- Five samples per case, reported as medians; before/after groups ran sequentially.
- One warm-up per microbenchmark case. Execution reused a compilation, with fresh
  execution state for each run.
- No observer, log files, or final execution output.

## 1,000 independent empty Steps

| Phase | Before | After |
| --- | ---: | ---: |
| Graph and Node declaration | 5.65 ms | 5.82 ms |
| Compilation | 8.69 ms | 9.04 ms |
| Serial reference driver | 227.21 ms | 16.99 ms |
| Thread driver, 1 worker | 272.10 ms | 51.93 ms |
| Thread driver, 8 workers | 282.28 ms | 39.60 ms |

Declaration and compilation were unchanged; their small differences do not
establish a gain or regression.

The current `run(workflow, max_workers=1)` still uses a thread pool, not
the serial reference driver. Task submission, synchronization, and batch
Python/Rust exchange still cost more than an ordinary function call.

## 2,000 subgraphs

The `benchmarks/recipe_scale.py` workload uses 6 sequential leaves per subgraph:
12,000 Python actions, 2,000 composites, 20 subgraph chains, and 8 workers.

| Phase | Before median | After median |
| --- | ---: | ---: |
| Compilation | 0.490070 s | 0.500653 s |
| Execution | 3.365259 s | 0.520759 s |

Execution improved by about 6.46×. This does not imply a 6.46× speedup for real
application workflows.

Raw execution samples, in seconds:

```text
before = [3.369867, 3.348461, 3.354630, 3.374089, 3.365259]
after  = [0.546870, 0.525995, 0.520759, 0.520433, 0.515768]
```

## Reproduction

From the Graph repository root, with the current extension installed:

```bash
.venv/bin/python benchmarks/step_overhead.py --nodes 1000 --repeats 5
.venv/bin/python benchmarks/recipe_scale.py \
  --recipes 2000 --steps 6 --lanes 20 --workers 8 --repeats 5
```

The first script separates declaration, compilation, serial execution, and threaded
execution. The second retains nesting and dependencies between subgraphs.

These commands measure the current checkout and do not automatically restore the
before version. Application logging and file I/O are not measured by these commands.
