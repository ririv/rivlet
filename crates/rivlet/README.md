# Rivlet Rust

Rust 调用方只依赖 `rivlet`，用同一套类型声明任务图并执行；无需 Python、
Node.js 或 Tokio。公开入口包含 `Graph`、`Step`、`Edge`、`Action` 和
`Execution`。需要底层编译与批量调度时，显式使用 `rivlet::core`。

```rust
use rivlet::{Execution, ExecutionOptions, ExecutionStatus, Graph};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let graph = Graph::new("workflow", [], [])?;
    let outcome = Execution::new(graph.compile()?, ExecutionOptions::new(2)?).run()?;
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    Ok(())
}
```

完整动作、结果与端口示例见 [Rust 图声明](https://github.com/ririv/rivlet/blob/main/docs/zh-CN/rust-graphs.md)。
