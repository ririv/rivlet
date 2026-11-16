# Rivlet for Node.js

Experimental Node-API binding to Rivlet's existing Rust compiler and scheduler.
JavaScript actions and their results stay in JavaScript; Python is not involved.
This private package is not published to npm and requires a source build.

From this directory, with Node.js 22.13+ and Rust 1.95 installed:

```sh
npm ci
npm test
npm run check
node examples/ports.mjs
```

See the [Node.js API guide](../../docs/node-api.md) for declarations, typed results,
subgraph ports, concurrency, failure behavior, architecture and current limits.

[简体中文](../../docs/zh-CN/node-api.md)
