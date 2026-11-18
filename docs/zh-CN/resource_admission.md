# 动作派发前的资源准入

依赖满足只表示动作**可以执行**，不表示它已拿到执行所需的资源。Rivlet 的 Python 执行入口可以在派发前向宿主提供的资源来源申请一个名额。暂时拿不到名额的动作保持就绪，不占线程，也不占子图并发额度。

## 为什么要在派发前申请

假设一次构建请求包含三个互不依赖的库。图执行器有两个 worker，构建宿主使用 GNU jobserver 管理共享并发预算，此时只有一个构建名额可用：

- A：构建 Expat，启动 `make`，已经拿到唯一的名额。
- B：构建 libfyaml，准备启动 `cmake --build`（使用 Ninja 后端），也需要名额。
- C：检查 libiconv 的已有构建结果能否复用，只读缓存记录，不需要构建名额。

| 时机 | 在动作里面等待名额 | 在派发前等待名额 |
| --- | --- | --- |
| A 使用唯一的构建名额 | A 占一个 worker | A 占一个 worker |
| B 就绪，但没有名额 | B 已开始，占第二个 worker 等待 | B 仍就绪，没有提交到线程池 |
| C 就绪 | 两个 worker 都被占着，C 也得等 | C 能用空闲的 worker 检查缓存 |
| A 退出并归还名额 | B 原地继续 | 资源来源通知执行器，执行器重试后派发 B |

这个机制协调的是**图何时把动作交给 worker**，不会改变图里的依赖，也不会接管 Make 或 Ninja 内部的编译任务。如果 libiconv 的缓存命中，它甚至可以在 Expat 还在编译时完成。若提前完成的工作不在整批构建的关键路径上，总耗时也可能不变；这种情况下，改善的是 worker 不再被等待者占住，不是编译更快了。

## 在执行入口绑定

`a`、`b`、`c` 是普通的 Step，`slots` 是宿主提供的同一个资源来源。Graph 声明不变：

```python
from rivlet import Graph, Limit, run

workflow = Graph(nodes=(a, b, c))
run(
    workflow,
    max_workers=2,
    max_concurrency={
        workflow: 2,
        a: Limit(maximum=1, source=slots),
        b: Limit(maximum=1, source=slots),
    },
)
```

整数配置仍然只限制图内在途动作；`Limit` 在相同位置额外绑定外部来源。各层本地上限同时生效，重复绑定同一个来源（按对象身份）只申请一次。同一个动作不能同时绑定两个不同来源；若需要多个资源，必须由宿主自行定义一个能原子获取它们的来源。条件求值不是动作，不申请名额。

[可运行示例](../../examples/python/resource_admission.py) 用一个内存名额复现上述场景。示例中的事件只用于固定演示顺序：

```text
A: acquired build slot
C: cache check ran before B could acquire a slot
A: finished
B: acquired build slot
```

## 宿主如何接入

- `Source.try_acquire()` 必须立即返回：得到 `Lease(value, release)`、暂不可用时返回 `None`，或在资源故障时抛错。获取失败使当前动作失败，而不是启动一个可能失败的动作。
- `Source.watch(wake)` 注册一次性通知，并返回注销回调。注册前后资源已可用或已故障也必须通知，避免丢失唤醒；通知可以来自其它线程。不保证通知者拿到名额，执行器会重试。订阅和注销都不应阻塞执行器。
- 动作通过 `ctx.permit(slots)` 拿到已借用的值，不能向执行器归还名额。执行器在动作成功、失败或取消收尾后归还。动作返回运行时子图时，先归还父动作的名额，子图再继承范围并各自申请。

`max_workers` 控制托管池的线程数，也限制已提交的同步调用存量；不会约束协程数量。借用线程池时，可显式传 `max_submitted` 限制本次执行的同步提交数；它不会为执行预留线程，也不限制池中其它调用方的工作。外部名额和本地上限都不等同于进程内 CPU 数，也不会统计动作自行开启的子进程。

Rivlet 只定义派发与归还边界。GNU jobserver 的 FIFO、文件描述符、隐含名额和如何传给构建工具，都由宿主适配器处理。这个接口目前接在 Python 执行入口，Rust 与 Node.js 高阶入口没有新增外部来源配置。
