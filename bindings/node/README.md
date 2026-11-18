# Rivlet for Node.js

Experimental Node-API binding to Rivlet's existing Rust compiler and scheduler.
JavaScript actions and their results stay in JavaScript; Python is not involved.
The workspace package is private. The npm distribution is staged separately
as a JavaScript root package and platform-specific native packages.

From this directory, with Node.js 22.13+ and Rust 1.95 installed:

```sh
npm ci
npm test
npm run check
node examples/ports.mjs
```

## 组装 npm 发行候选

分别在 macOS arm64、Linux x64（glibc）和 Windows x64（MSVC）的构建宿主上运行
`npm run build:artifact -- <Rust 目标三元组> <新目录>`。把三个带平台后缀的
`.node` 文件收集到同一目录，再运行：

```sh
npm run stage:release -- /path/to/artifacts /path/to/new-staging-dir
```

组装会检查配置中的目标是否齐全。根目录包含 JS 和类型声明；
`platforms/` 中是对应的原生子包。两条命令都不上传；发布前仍需在目标平台
使用实际将上传的 tarball 安装并运行测试。

See the [Node.js API guide](../../docs/node-api.md) for declarations, typed results,
subgraph ports, concurrency, failure behavior, architecture and current limits.

[简体中文](../../docs/zh-CN/node-api.md)
