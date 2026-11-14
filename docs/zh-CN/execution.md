# 执行语义

[English](../execution.md) | 简体中文

## 展示模式

`Execution` 保留两个公开模式，但二者不再对应两套执行器：

- dynamic
  - `run(execution, mode="dynamic")`
  - 使用 Rust 声明编译器和 Rust execution session
  - 为兼容结构视图，composite 同时记录到 `executed_steps/completed_steps`
  - 端口连接 与 compiled 模式使用同一 lowering
- compiled
  - `Execution.compile()`
  - `run(execution, mode="compiled")`
  - 使用同一个 Rust 声明编译器和 Rust execution session
  - composite 只记录专门的进入与完成状态
  - 支持 端口连接 lowering

正式 runtime 只接收 `GraphCompilation`。action 在运行期返回的 `Graph` 会携带父
step id、label path 与父结果作用域，再进入同一个编译执行流程。

## 编译准备

Execution 的声明及宿主引用在构造后不可替换。首次成功的 `compile()`、`run()` 或
`arun()` 固定同一份根编译结果，后续复用；只读 `execution.compilation` 在准备成功前为
None，之后返回该对象。编译异常不提交半成品。重新求静态条件需要新建 Execution；
独立工具 compile_graph 仍独立编译。准备结果复用不重置执行状态，也不是业务结果缓存。
冻结当次真实图的用法见[结构导出](./graph_export.md)。

## Python action 返回值

阻塞和可等待入口共用动作调用、结果处理与 session 推进流程：

- 普通值直接进入结果记录；不为同步 action 创建 asyncio 事件循环。
- 协程动作使用同一个事件循环。阻塞入口实际遇到 awaitable 后才创建循环，之后的
  协程和动态子图复用它；可等待入口直接使用调用方当前循环。同样支持同步函数返回
  coroutine 或自定义 awaitable，保留动作自己的 ContextVar 上下文。
- 返回 `Graph` 时仍执行动态子图，完成后再结束当前 Node。

同步 Python 动作提交到调用方提供的同解释器线程池。已知协程动作不会排在阻塞线程
任务后面；普通函数的返回值也会检查是否需要等待，不能只检查 `async def`。
纯同步执行及其失败收尾均不创建事件循环。日志、异常、结果及 Node 生命周期规则不变。

各项开销的分离测量见 [Node 开销基准](./step_overhead_benchmark.md)。

## 条件执行

条件模型现在分成两类：

- [conditional_edges.md](./conditional_edges.md)
  - 解决依赖关系是否成立
- [conditional_steps.md](./conditional_steps.md)
  - 解决 step / 子图本身是否启用

这里只保留执行层面的统一结论：

- 两种模式共同：
  - Rust 编译器裁剪 `StaticCondition`
  - 运行时条件边编译为 `CompiledConditionalDependency`
  - step 级 runtime `condition` 编译为 `CompiledRuntimeCondition`
  - 条件位于子图内部时，叶子仍然扁平化
  - 条件为假时节点进入 skipped 终态；条件依赖为假时只移除该依赖
  - 每个编译条件实例在当前 session 中最多求值一次；多个实例可以复用同一个 predicate

## 图渲染

位置：

- [src/rivlet/render/](../../src/rivlet/render/)

当前渲染接口分两类：

- `render_graph(...)`
  - 接受 Step / Graph、GraphStructure 或 GraphCompilation
  - 纯结构展示显式接收检查标题 `root_label`；不覆盖 Graph 自身的 label
  - `mode="dynamic"` 时返回终端友好的组合结构树
  - `mode="compiled"` 时返回 compilation 视图文本
  - 若传入 `GraphCompilation` 且 `mode="dynamic"`，按 `source_graph` 的组合结构渲染
- `render_graph_mermaid(...)`
  - 接受 Step / Graph、GraphStructure 或 GraphCompilation
  - 纯结构展示显式接收检查标题 `root_label`；不覆盖 Graph 自身的 label
  - `mode="dynamic"` 时返回原始 `Graph` 的结构 mermaid
  - `mode="compiled"` 时返回 compilation mermaid
  - 若传入 `Graph` 且 `mode="compiled"`，内部会先 `compile_graph(...)`

另外保留一个更具体的 compilation mermaid 入口：

- `render_graph_compilation_mermaid(compilation, root_label=...)`

## 托管与借用执行资源

不再区分线程模式和异步模式，也不隐式设置全图动作额度。`run` 阻塞调用方；`arun`
允许调用方异步等待同一类混合工作流。`dynamic` / `compiled` 只控制展示记录。

```python
from rivlet import arun, run

state = run(workflow, max_workers=4)
# 在 async 函数中：
# state = await arun(workflow, max_workers=4)
```

高阶函数创建并关闭自己的 Python 线程池。`max_workers` 默认 1，只限制同步 Python
工作线程，不限制在途协程；线程按需启动。传入 Node 时创建新 Execution，也可以
直接传入已准备的 Execution，保留同一次编译、结果、事件及宿主绑定。
两个入口都可显式传 `observer=...`；不传时不发出 graph observation。

已有线程池时使用低阶方法：

```python
from concurrent.futures import ThreadPoolExecutor

from rivlet import Execution

with ThreadPoolExecutor(max_workers=4) as pool:
    execution = Execution(workflow)
    compilation = execution.compile()
    state = execution.run(pool=pool)
    assert execution.compilation is compilation
    assert pool.submit(lambda: 42).result() == 42
```

在 async 函数中使用 `await execution.arun(pool=pool)`，不用再传 loop。已经运行
事件循环的线程不能调用 `execution.run`，应使用 arun。低阶方法从不关闭借入的池，
失败和取消时也一样；返回前只等待本次提交的工作退出，不等待池中无关任务。

这里只支持同一解释器中的 ThreadPoolExecutor。ProcessPoolExecutor 和
InterpreterPoolExecutor 不可直接替换：闭包、上下文、共享状态和扩展对象没有建立
序列化或隔离契约。Rust 动作继续在 Rust worker 中执行，不经过 Python action 回调；
其 session 内部资源与借入的 Python 池分开。
每个 Rust session 默认取逻辑 CPU 数（不可获得时为 1），最多 32 个 worker，且不超过
当前 Rust 动作数量；不再从 Python `max_workers` 推导其容量。
Rust 批次完成通知不占用 Python 动作线程，池已满也不会阻止协程消费已经完成的
Rust 结果。

运行时返回的子图复用父执行的 Python 池和事件循环。父动作返回子图后释放线程，
不在线程里递归等待子图；独立分支不保证先后顺序。不传 `max_concurrency` 时，不自动限制
在途协程数或等待线程池的提交数量。下载、API、内存等预算由业务显式限制，例如
在网络请求周围组合 asyncio semaphore，不能把 `max_workers` 当作这些资源的预算。

## 可选的子图并发上限

限额属于执行策略，不写进 Graph 声明。四个执行入口都接受同一种平铺
`max_concurrency` 字典，key 是已声明的 Step 或 Graph，value 是正整数：

```python
downloads = (download_a | download_b | download_c).graph()
processing = (process_a | process_b).graph()
workflow = (downloads | processing).graph()

run(
    workflow,
    max_workers=8,
    max_concurrency={workflow: 4, downloads: 2, processing: 3},
)
```

借用池时，将同一字典传给 `Execution(workflow).run(pool=pool, max_concurrency=limits)`；
两个 `arun` 入口的参数一致。图与已经缓存的编译实例不变，不为子图额外创建 Python
线程池，已有的 Rust worker 所有权也不变。包含关系完全来自真实图，不重复声明配置树。

并发上限指“已经获准派发、尚未完成或失败”的 action 数量，包含排在线程池队列中的
任务，以及等待 I/O 的协程。同步 Python、异步和 Rust action 共用所在范围的额度。
每个 action 必须同时满足所有受限祖先和自身的上限。上例中，下载最多 2 个、处理最多
3 个，合计最多 4 个。它只是上限，不预留名额，不保证启动数量，也不保证独立分支公平。

Rust 在派发前一次性检查并占用所有相关额度。没有名额的动作留在调度器，不占线程，
也不先占一部分额度再等待。Graph 生命周期和运行时 predicate 不算 action，不占名额。
action 返回运行时子图后，归还自己的额度，子图 action 跨 session 继续受原有祖先约束。
普通失败归还名额，只阻断相关下游；取消停止新派发，等待已经提交的工作收尾，不关闭
借入的池，也不强制中断已经开始的动作。

配置中的 Node 必须在根声明内且只有一个挂载位置。复用结构时可以分别配置
`Graph(shared_structure)` 实例，不能用两处挂载共享的内部 Step 作为歧义 key。
未知引用、歧义引用及非正整数在 action 开始前报错。已声明但被静态裁剪的节点允许配置，
只是没有工作可执行。运行时才创建、未在根声明中出现的节点不能提前配置，继承生产它的
action 所在范围。不传或传空字典时不限流；派发前会对配置取快照。

只统计图内 action，不统计 action 内自行创建的线程、请求或子进程。若一个 action
阻塞等待另一个被相同额度挡住的 action，使用者仍可能制造死锁；依赖应通过图上的边
表达，不要在 action 内等待同组的其它 action。这不是请求速率、优先级或特定资源预算。

## 执行控制

`ExecutionControl` 保存停止状态和停止处理器，不保存 step 结果。普通 action 或
运行时条件的异常不会请求全局停止；宿主主动取消、`ExecutionStopped` 或
`KeyboardInterrupt` 等执行中断仍终止整图。宿主可以注册资源停止处理器，回收仍在运行的子进程。

control 是一次性的执行作用域：一旦收到停止请求，就不会恢复，也不提供 `reset()`。
运行前已经停止的 Execution 会直接抛出 `ExecutionStopped`；运行过程中收到停止请求
后，runtime 不再派发新的 ready step，但会等待已经启动的 step 结束，再报告停止。

`Execution` 默认创建独享 control：

```python
execution = Execution(root=Graph(structure=(graph).structure))
```

独立使用 Graph 时，不会触碰宿主的进程级停止状态。若宿主把多个顺序 Execution 视为同一
次请求，可以显式共享一份 control：

```python
control = ExecutionControl()
first_execution = Execution(
    root=Graph(structure=(first_graph).structure), execution_control=control
)
second_execution = Execution(
    root=Graph(structure=(second_graph).structure),
    execution_control=control,
)
```

这仍然是两次顺序执行，而不是在一个 Execution 的 step 内嵌套启动另一个 Execution。两个
Execution 各自持有独立的 `ExecutionState`；共享的只有请求停止与宿主资源回收信号。
显式停止后，后一个 Execution 会在开始执行前看到停止状态，因此不会继续运行。
普通失败仍会从 `run()` 抛出异常；调用方若捕获异常后主动运行第二个 Execution，control 不会阻止它。
两个 Execution 间需要成功依赖时，应组合成一张图或由调用方明确处理，不能用共享 control 代替依赖边。
信号处理、子进程终止和多个 Execution 的请求级协调由宿主负责，不属于 Graph core。

action 可以调用 `ctx.control.stop_requested()` 或 `ctx.control.require_running()` 协作
响应停止。取消等待 `arun()` 的 task 会请求停止，并等待已启动动作清理退出。停止
处理器在锁外执行，停止后才注册的处理器立即执行；所有处理器执行完后聚合其异常。

## 步骤执行事件

通过 `Execution(events=...)` 显式传入 `NodeEventSink`，接收包含声明 node_id、
挂载 node_path（标签由 .labels 读取）、
结构化 `state_detail` 及派生 `state: NodeExecutionState` 的 `NodeExecutionEvent`：
`STARTED`、`COMPLETED`、`FAILED`、
`BLOCKED`、`SKIPPED`。静态子图、动态子图和原生动作使用同一接口。通知发生在状态
提交之后，组合边界的事件不依赖展示模式。回调同步、串行执行；回调失败以
`EventDeliveryError` 停止执行，不撤销已提交状态。未订阅时不创建事件对象或事件
标签索引。原有 `GraphObserver` 继续独立负责图级展示消息。示例见[核心模型](./core_model.md)。

## 失败传播

普通 Node 失败只阻断真正依赖它的后续节点，无关分支继续执行，最终整图仍报告失败：

```text
A（失败）→ B（blocked，不执行）
C（成功）→ D（继续执行）
```

- Rust session 区分 `Finished`、`Skipped`、`Failed`、`Blocked`。失败不能伪装为成功或主动跳过。
- 激活的条件依赖和结果边传播失败；未激活的条件边不传播失败。
  条件函数自身抛错时，相关节点被阻断，不会读取缺失的 action 结果。
- 已进入的子图让内部无关分支执行完，再汇总为失败；不会触发 `AFTER_SUCCESS`。
  依赖整个子图的节点被阻断。尚未进入就被阻断的子图，其全部后代也标记为 `blocked`。
- 端口端口遵循实际接线：出口步骤已成功，则该端口仍就绪，不因其子图内其他步骤失败而撤销。
  如果出口失败，消费它的入口被阻断；消费者子图内不依赖该入口的准备工作仍可执行。
- 动态 action 返回的子图使用相同规则；其失败最终使外层 action 失败。
- Python action 与 Rust action 共享以上语义。Rust 批量回传失败状态和错误，不增加逐节点异常查询。

包含多个出入口、出口提前就绪和整个子图依赖的对比，见
[端口依赖：出入口遇到失败时](./interfaces.md#出入口遇到失败时)。

调度结束后，单个错误沿现有异常链抛出并保留节点路径，多个错误用 Python `ExceptionGroup`
一起报告；被阻断的节点不制造重复异常。执行快照的 `node_failures` 保存
`GraphNodeFailure.FAILED | BLOCKED`，渲染区分这两个终态，不再显示为运行中。
编译错误、调度协议错误、日志/观察设施错误不是可恢复的业务 action 失败，不承诺继续执行。

失败不会回滚已成功工作，也不会自动重试。下游是否可以继续由图上的依赖关系决定，而不是异常名称。
新增阻断传播沿现有邻接索引进行，不为每个错误重扫整图；阻断子树访问为
`O(V_blocked + E_out)`。子图终态使用直接子节点计数，全部子图的汇总工作为 `O(V)`。

## 宿主运行设施边界

Graph runtime 只负责图本身的调度、状态和结果，不隐式读取宿主的进程级设施。
`Execution` 通过两个独立端口接入宿主：

- `ActionLog`
  - 为 action 建立宿主日志作用域
  - 在并行 action 抛出 Python 异常时追加异常文本
- `ExecutionOutput`
  - 在一次执行结束或失败后消费最终图状态
  - Graph 提供 `TreeFileExecutionOutput(output_dir)`，把执行视图写入显式目录

独立使用 `Execution` 时，这两个端口采用明确的 standalone 行为：既不接入宿主日志，
也不产生外部执行视图文件。停止控制只由独立的 `ExecutionControl` 负责，不属于
action 日志端口。需要日志或执行视图时，宿主必须显式传入自己的 `ActionLog`
实现和 `ExecutionOutput`；核心不会探测全局目录或静默切换行为。

## 显式跳过

显式 skip 状态可以指定 step path。compiled 模式下跳过 composite 时，其后代一并跳过。
它不同于 failed / blocked，不会执行 action 或生成 action 结果。

声明、编译和结果 API 统一见[核心模型](./core_model.md)；条件与失败传播按上文规则处理。

## 为什么当前不提供 runtime hooks

当前 Graph runtime 仍然不提供 side-effectful hooks，例如：

- `before_step`
- `after_step`
- cache probe / cache writeback hook
- skip 决策 hook

原因不是 hooks 做不到，而是它们会把核心执行语义从图上挪走。

当前模型刻意要求这些行为都留在声明层显式表达：

- cache：显式 probe step / writeback step
- log：显式接入 `ActionLog`，或在声明层组合 action wrapper
- skip：由宿主显式写入 `ExecutionState`

这样图本身就能直接表达：

- 哪些步骤会做 cache probe
- 哪些步骤可能写回外部状态
- 哪些步骤会被跳过

如果这些行为进入 runtime hooks，图上就看不出真正流程，执行语义也会变隐式，不符合当前“显式、可追踪、组合优先”的约束。

当前已经提供只读 observer/event 接口，用于 trace、timing、可视化与图级摘要输出；但它不应修改执行结果，也不替代显式 graph step。

## DAG 约束

Step/Graph 校验自身元信息；GraphStructure 在构造阶段校验结构：

- 显式提供的 graph label 非空；省略 label 合法
- GraphStructure.nodes 只包含 Step 或 Graph
- 同一个 Node 对象没有重复挂载
- edge 的 source / target 都属于当前图
- 无环

Graph 将本层显式边和运行时条件读取投影为整数节点与阻塞边，批量交给 Rust
`dag_cycle` 检查；不是调用 Python 的 `DirectedTopology` / `DAGTopology`。
跨子图端口绑定在编译时 lower 后再做全局 DAG 校验，详见
[拓扑复杂度](./topology_complexity.md)。

## 子图语义

Graph 自身是声明态复合节点，内部可复用结构保存在 graph.structure；不再用 Step 包装。

Rust compiler 会转换所有声明期已知子图：

- 叶子 action 进入全局扁平执行集合
- 原子图边界变成非执行的 `CompiledComposite`
- composite 激活后才允许其直接子节点进入 ready
- 全部直接子节点终态后 composite 才完成
- 空 composite 激活后立即完成
- `GraphObservationMessages.before/after_success` 挂在 composite 生命周期上

因此增加 runtime condition、条件边或 observation 不会改变执行模型。action 在
运行期返回的 `Graph` 无法提前编译；返回后会以当前叶子路径为显式输入，建立一个
嵌套 compilation 与 execution session。

结果边在当前挂载内解析生产者身份。action 按参数顺序接收 Option，
不再获取图作用域索引或任意结果查询上下文。返回动态子图的 action 可以将
已经收到的值捕获到子图动作中，不隐式访问祖先图的结果存储。

## 多入口子图

子图允许多个入口节点。

例如：

- `prepare_a`
- `prepare_b`
- `build.wire(after=(prepare_a, prepare_b))`

这在当前模型下合法。多个入口只是“同时 ready”，不是“同时并行执行”。

## 当前并发语义

同步与协程动作可以混用，不再按运行选项选择整图后端。资源所有权和调用方式见
[托管与借用执行资源](#托管与借用执行资源)。日志作用域通过 ContextVar 保持动作隔离。

并发语义：

- 普通失败阻断相关下游，无关 ready step 继续派发
- 已经在跑的 step 等自然结束
- 异步后端不会取消已经创建并开始运行的 task
- 运行期返回图通过嵌套 compilation/session 执行

不要依赖两个互不相干 Node 的隐式顺序；如果顺序有业务意义，必须通过
`Node.wire(after=...)`、Graph edge 或 `>>` 显式声明。

## 并发边界

统一调度保留以下边界：

- 同步 Python 工作线程数由池决定，业务限流由使用者组合
- 只调度拓扑上已经 ready 的节点
- 显式取消后停止派发新节点；普通失败按依赖关系传播
- 已经开始执行的同步 worker 或异步 task 不会被 Graph 强制取消
- 运行期返回图使用独立 Rust session，由同一驱动推进
- 等待子图的父步骤不占线程；Python 子图共用线程池和事件循环
- 不隐式设置 Python / Rust / 协程合计额度；显式 `max_concurrency` 跨 session 生效
- 节点 ready 判定仍在 Rust，串行嵌套 session 使用显式栈推进
- action 对宿主共享状态的并发写入安全由宿主负责

Graph 不统计 action 内部自行创建的线程、子进程或模型请求；它提供 DAG 调度，
不是宿主资源、进程或事务的全局并发管理器。
