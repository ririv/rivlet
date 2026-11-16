# 混合执行的驱动边界讨论

[English](../../design/mixed-driver-boundary.md) | 简体中文

本文只记录实验分支的设计讨论，不是已实现的驱动契约。混合执行、扩展 SDK、桥接和
协调层整体保留在实验分支；主线不提供 Python/Rust 混合执行。纯 Rust 执行和独立的
Node.js 前端是另外两项能力，不随混合执行移走。

## 当前实现

SDK 只通过 `export_node` 导出具体 Step 或 Graph。当前 Rust SDK 导出的 Graph 内部
动作和条件全部属于 Rust，没有往内部注入 Python action 的入口；Python 在外层组合
这个节点。复用 `.structure` 会创建新 Graph 声明，不自动复制根身份和条件。
扩展可以直接注册节点为模块属性，工厂函数只用于参数化或创建新声明，不是 SDK 要求。

动作由 Rust 执行，Rust 到 Rust 的中间值保持原生；但 Python 宿主驱动仍负责调用
`start/advance`、等待原生完成，再调用下一轮 `advance`。导入完整 Graph 不等于内部
已经能够自主推进。声明只导入一次，执行却仍可能多次经过宿主。

```text
Python start → Rust 读取 → 通知 Python
Python advance → Rust 解码 → 通知 Python
Python advance → Rust 校验 → 通知 Python
```

独立 Rust `Execution::run` 已有自己的同步 Rust 驱动，不执行 Python 动作，也不依赖
Python 宿主；它与上述混合执行路径尚未统一。

## 为什么原先保留宿主驱动

原边界让 core 负责拓扑、就绪判断与调度状态机，Python 管理任意 callable、借用线程池、
asyncio、ContextVar、异常、结果和运行时返回的图。宿主批量交换工作，不让 Rust worker
为每个动作反向调用 Python。私有 Tokio 回调实验只针对这种逐动作回调方案，不能据此
推导所有 Rust driver 都更慢。

加入原生动作后出现另一种成本：全部由 Rust 组成的依赖链，也要等待 Python 接力推进。

## 候选方向

在 Python driver 下增加自主原生推进器，可以去掉原生链的逐轮往返，但仍是两层协作
驱动。共用一个 core 调度器，并不等于只有一个 driver，不能混称。

真正统一的 Rust driver 应负责 session 推进、完成处理、准入和动态子 session 关系；
各语言适配器执行自己的 callable，通过批量请求和响应交回结果。Python 保留事件循环
和宿主对象，但不再决定图如何推进。一个 driver 不等于只有一个线程或没有宿主循环。

两个候选都没有实现。以后若继续，应收敛现有纯 Rust driver 和协调层，不再增加第三份
长期独立的驱动实现。

## 要保留的语义与风险

- core 图模型和调度算法保持独立，driver 属于 execution，语言对象和转换留在适配层。
- 不能把原生 Graph 变成一个原子黑盒动作；保留节点、限额、失败和端口提前就绪。
  解码输出可交给 Python 消费，同时 Rust 继续后续校验，不等待整个子图完成。
- 不能在 asyncio 事件循环线程等待原生工作；释放 GIL 不等于释放该线程。
  线程归属是独立的运行策略，不因为 driver 用 Rust 写就必须新建线程。
- 保留 Python ContextVar、协程、异常和对象所有权；转换仍按实际消费惰性发生，
  转换错误归属于对应消费者。
- 保留共享准入、取消、skip 和动态子图继承；父动作等待返回子图时不占动作名额。
- 由完成和控制消息唤醒，不增加定时轮询；事件批量交付但不丢弃或乱序，不为派发下一个
  原生节点而强制经过 Python。
- 不预设性能收益。分别测原生链、纯 Python 小图、真实混合图与异步负载，记录往返次数、
  取消和观察延迟；纯 Python 不应无条件承担额外原生池或后台线程。

## 与运行时嵌入的区别

SDK 导出的是 Rivlet 原生节点，不负责嵌入 CPython 或 Node.js。同一进程中同时调用
Python 专属库和 Node.js 专属库，需要独立的运行时桥接。普通跨语言库调用不需要图 SDK；
需要把库内的依赖结构和进展接入 Rivlet 时，节点导出才提供额外价值。
