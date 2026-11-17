# 共享状态

共享状态是宿主显式注入的一份对象，供同次执行的动作读取和修改。它不是步骤结果表，
也不是 Graph、子图各自拥有的隐式 store。Python、Rust、Node.js 使用各自语言的原生对象；
不将它序列化给调度 core，也不引入跨语言对象协议。

## Python

沿用 `Execution(..., shared=object)` 与 `ctx.shared(ExpectedType)`：

```python
from threading import Lock

from rivlet import Execution, Graph, NodeRunContext, Step, run


class Progress:
    def __init__(self) -> None:
        self.completed: set[str] = set()
        self._lock = Lock()

    def complete(self, name: str) -> None:
        with self._lock:
            self.completed.add(name)


def record(ctx: NodeRunContext) -> None:
    ctx.shared(Progress).complete(str(ctx.node_path))


workflow = Graph(nodes=(Step(record), Graph(nodes=(Step(record),))))
progress = Progress()
run(Execution(workflow, shared=progress), max_workers=2)
assert len(progress.completed) == 2
```

`shared()` 做 `isinstance` 检查，返回原对象；没有注入时抛出 RuntimeError，类型不符时抛出
TypeError。框架不创建默认对象，不复制对象，也不自动给它加锁。

## Rust

宿主通过 `ExecutionOptions::shared(Arc<T>)` 注入，动作通过
`ActionContext::shared::<T>()` 得到同一个 `Arc<T>`：

```rust
use std::sync::{Arc, atomic::{AtomicUsize, Ordering}};

use action::{Action, ActionContext, ActionError, ActionHandle};
use execution::{Execution, ExecutionOptions, ExecutionStatus};
use graph::{Graph, Step};

#[derive(Default)]
struct Progress(AtomicUsize);

struct Record;
impl Action for Record {
    type Inputs = ();
    type Output = ();

    fn execute(&self, ctx: &ActionContext, (): ()) -> Result<(), ActionError> {
        ctx.shared::<Progress>()?.0.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let first = Step::new("first", ActionHandle::new(Record))?;
    let second = Step::new("second", ActionHandle::new(Record))?;
    let workflow = Graph::new("workflow", [first.into(), second.into()], [])?;
    let progress = Arc::new(Progress::default());
    let options = ExecutionOptions::new(2)?.shared(Arc::clone(&progress));
    let outcome = Execution::new(workflow.compile()?, options).run()?;
    assert_eq!(outcome.status(), ExecutionStatus::Succeeded);
    assert_eq!(progress.0.load(Ordering::SeqCst), 2);
    Ok(())
}
```

类型要求 `T: Send + Sync + 'static`，复杂可变对象由宿主选择 Mutex、RwLock 或其它同步机制。
`shared()` 不复制 T；未注入和类型不符分别返回 `SharedStateError::NotProvided`、
`SharedStateError::TypeMismatch`，可以用 `?` 转为普通 ActionError。

低阶执行器也可以显式提供 `ActionContext::default().with_shared(Arc::clone(&progress))`。
`SharedState` 是保存原 Arc 的类型擦除载体，不是按类型自动创建对象的容器。

完整可运行文件见 [`shared_state.rs`](../../crates/execution/examples/shared_state.rs)：

```sh
cargo run --locked -p execution --example shared_state
```

## Node.js

通过 `run(node, { shared })` 或 `execution.run({ shared })` 注入，动作使用
`ctx.shared(Progress)`，类型由构造器推导，运行时用 `instanceof` 检查：

```js
import { Graph, Step, run } from "rivlet";

class Progress {
  completed = new Set();
}

const record = ctx => {
  ctx.shared(Progress).completed.add(ctx.nodePath.join("."));
};
const workflow = new Graph({
  nodes: [new Step(record), new Graph({ nodes: [new Step(record)] })],
});
const progress = new Progress();
await run(workflow, { shared: progress });
console.log(progress.completed.size); // 2
```

传给 `shared()` 的构造器只是类型检查依据，框架不会调用它创建对象。它返回原引用；
未注入时抛出 Error，类型不符时抛出 TypeError。该错误属于请求读取它的动作，
按普通失败规则传播，不使独立分支自动失败。

JS 回调在所属事件循环执行，但跨 `await` 的“读、等待、再写”仍可能产生逻辑竞争；
框架不承诺这种操作是事务，也不把任意 Worker 的内存自动变成共享内存。

## 作用域与生命周期

- 同次执行的并行动作、声明子图、递归生成的动态子图继承同一对象。
- 复用 Graph 或 Compilation 不会携带上一次的共享对象。各次执行显式注入什么，就使用什么。
- 显式在多次执行中复用同一个对象，会共享状态。Rust 克隆 ExecutionOptions 会克隆 Arc，
  而不是创建独立对象；需要隔离时由宿主提供新对象。
- 不写 `shared` 就没有共享状态。读取时明确报错，不按请求的类型创建默认实例。
- 执行结束后，框架释放自己的运行期引用；宿主或动作主动保留的引用仍由其自己管理。
- 失败和取消不回滚已经发生的修改，没有自动重置、持久化或恢复语义。

## 与结果、依赖和事件的边界

共享本身不产生边，不提供执行顺序或完成屏障。A 必须等 B 更新后再读取时，仍需显式声明
`Edge(B, A)` 或结果边。结果传递仍走 `Edge.results(...)`，不能把轮询共享对象当成结果依赖。

运行时条件没有新增 ctx 参数；它们仍使用自己声明的结果输入。需要读取宿主对象的谓词可以
显式捕获该对象，但捕获也不会让框架推断依赖。

共享对象不自动进入步骤结果、JSON 图文档、Tree/Mermaid 或执行事件，不调用它的 repr、
Debug、toJSON 来扫描内容。动作主动将共享对象作为返回值发布，属于动作自己的显式行为。
Rivlet 不内置共享对象变更事件、消息队列、事务或 agent 自动唤醒；这些是外层协作系统的职责。

此功能不扩展实验性混合执行 SDK：Python 对象、Rust Arc 和 JavaScript 对象分别由所属宿主
管理，不保证 Rust 扩展动作能读取 Python 的 `shared`，也不改变现有跨库 ABI。
