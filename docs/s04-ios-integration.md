# S04 联调说明

当前全量入口：[`ios-integration.md`](ios-integration.md)。

孵化（S02–S04、五句、complete）细节：

[`p01-p07-integration.md`](p01-p07-integration.md)

- 环境：API `http://192.168.100.212:8000`，iOS 设备 `192.168.100.205`
- 服务端已按 JWT `sub` 补本机 `auth.users`，iOS 不用再发 sub
- 当前 OpenAPI SHA 以总说明为准，不要用 P01–P07 表头里的历史值做全量解码锁
