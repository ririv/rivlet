# 协作取消

取消属于一次 Execution，不属于 Graph 声明。复用一张图不会共享取消状态；只有显式
传入同一来源的信号，多个执行才会一起收到取消。首版只支持整次执行取消，不支持
指定子图取消，也不修改依赖边或切换端口 provider。

## 公共语义

Python、Rust 和 Node.js 使用相同的 Rust core 状态转换：

- 取消来源发出请求，只读信号提供查询、检查和等待能力。
- 请求幂等，取消不可恢复；不提供 reset。
- 停止派发未开始的动作和谓词；它们进入 Cancelled，而非 Skipped。
- 在途动作必须结束或配合退出，不能用取消请求伪造它们已经结束。
- 保留在途工作实际产生的成功和失败，不撤回既有结果，不回滚副作用。
- 有真实失败时，最终报告失败；否则有效取消报告取消。执行已结束后的取消无效。
- 取消结果不能当作 Nothing 传给下游。取消会停止后续派发，而不是让消费者改用缺省值。

运行中的动作最终可能全部成功，但执行仍可能已经被有效取消。因此执行级取消状态
独立保存，不从“是否存在取消节点”猜测。

## Python

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor
from rivlet import CancellationSource, Execution, ExecutionCancelled, Step

async def work(ctx):
    await ctx.control.wait_cancelled()
    ctx.control.require_running()

async def main():
    source = CancellationSource()
    execution = Execution(Step(work), cancellation=source.token)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = asyncio.create_task(execution.arun(pool=pool))
        await asyncio.sleep(0.01)
        source.cancel()
        try:
            await running
        except ExecutionCancelled as error:
            assert error.state.cancelled

asyncio.run(main())
```

同步 `Execution.run(pool=...)` 接收相同构造参数，外部线程可以调用 source.cancel()。
同步动作可以在处理每个分块前调用 `ctx.control.require_running()`；也可查询
`ctx.control.stop_requested()`。异步动作等待 `ctx.control.wait_cancelled()` 不占线程。
Token 自身提供 `is_cancelled()`、`require_running()` 和 `wait_cancelled()`。

`ExecutionCancelled.state` 保留部分执行状态：`cancelled` 表示执行确实接受过取消，
`cancelled_steps` 保存已取消路径；成功结果、完成路径和真实失败仍保留。步骤事件
使用独立的 `NodeExecutionState.CANCELLED`，渲染也显示 cancelled，不显示为 running。

已有 `execution_control=ExecutionControl(...)` 仍用于宿主停止处理器。它与新的
`cancellation=` 构造参数互斥；需要同时使用时，先构造
`ExecutionControl(cancellation=source.token)`，再显式传给 Execution。
`ctx.control.wait_cancelled()` 同时响应这份 control 的宿主停止；它不会反向取消调用方
可能供其它执行共享的 Source。直接等待 source.token 则只响应该来源的显式取消。
资源停止、KeyboardInterrupt 与外层 asyncio Task.cancel() 仍是宿主中断路径，
不冒充一次已正常收尾并带有取消终态的显式取消请求。

## Rust

```rust,ignore
use execution::{CancellationSource, Execution, ExecutionOptions, ExecutionStatus};

let source = CancellationSource::new();
let options = ExecutionOptions::new(4)?.cancellation(source.token());
let execution = Execution::new(graph.compile()?, options);

let outcome = std::thread::scope(|scope| {
    let running = scope.spawn(move || execution.run());
    source.cancel();
    running.join().expect("execution thread panicked")
})?;
println!("{:?}", outcome.status());
```

动作调用 `ctx.control().require_running()?` 配合退出；该错误是
`ActionError::Cancelled`，不是自由字符串。未发生对应执行取消时，动作自行返回
这个错误仍视为动作失败。只读 Token 可克隆，`token.cancelled().await` 不依赖 Tokio。
示例中取消与执行并发发生：若执行已全部结束，迟到的取消不改写成功状态。

`run_async()` 使用相同选项。取消时丢弃待定的 Rust 谓词 Future，并确认其取消；
已提交的同步动作仍收尾。`AsyncAction` 的在途 Future 则继续被 poll，直到动作配合取消
或返回真实成功/失败，之后才返回。直接丢弃整个执行 Future 才会丢弃待定异步动作，
这不等于显式协作取消。详见[异步动作](./node-rust-advanced.md#rust-异步动作)。
NodeOutcome::Cancelled、ConditionOutcome::Cancelled 与失败、跳过各自独立。

## Node.js

```javascript
import { setTimeout as delay } from 'node:timers/promises';
import { Execution, ExecutionCancelled, Step } from './lib/index.js';

const controller = new AbortController();
const work = new Step(async ctx => {
  await delay(60_000, undefined, { signal: ctx.signal });
});
const running = new Execution(work).run({ signal: controller.signal });
controller.abort();
try {
  await running;
} catch (error) {
  if (!(error instanceof ExecutionCancelled)) throw error;
  console.log(error.outcome.status); // cancelled
}
```

直接使用 AbortController / AbortSignal，不新增同义包装。动作接收只读 `ctx.signal`，
可传给 fetch、定时器等原生 API，或调用 throwIfAborted()。
只有当前信号的 reason，或 Node 原生 AbortError 携带相同 reason 的 cause，才是取消
确认；仅凭错误名 AbortError 不会吞掉业务错误。谓词仍无 ctx，需要响应取消时显式
捕获调用方的 signal，不增加一组隐式结果输入。

ExecutionCancelled.outcome 保留全部终态与成功结果；真实失败仍抛 ExecutionError。
Promise 不配合取消时继续等待，不遗弃任务、不伪造 Promise 已结束。
JS 阻塞事件循环的同步函数无法被同一循环中的取消回调打断。

## 分层与边界

core 只保存取消状态、发出事件并阻止新派发；它不调用 Python、JS 或异步运行时。
驱动负责通知动作、唤醒等待和收尾；动作或进程适配器负责停止自己的外部资源。
取消不会自动终止任意子进程，也不会强杀线程。不配合的工作可能使收尾持续等待。

取消订阅在执行结束或等待结束时注销，不依赖定时轮询，不修改图或重新编译。
这是一套三端共用的调度语义，不代表 Python、Rust 与 JS 已支持混合语言执行。
实验性 SDK ABI 当前不传递取消信号，跨库 Rust 动作仍只能停止新派发并等待已提交工作。
