# fswatch

用法：

1. （可选）在 `src/settings.py` 中修改运行选项
2. `python3 src/monitor.py path [path ...] [--dispatcher_type DISPATCHER_TYPE] [--route_events [ROUTE_EVENTS ...]] [--route_patterns [ROUTE_PATTERNS ...]] [--route_tags [ROUTE_TAGS ...]]`
3. 对于 `--dispatcher_type local`，使用 `tail -f .fswatch.{route_tag}.buf` 调试
4. 在 monitor.py 的运行中输入 list_tracked, checkout 指令查看追踪的 INI 文件，输入 exit 退出

默认用例：

- 选项：
  ```sh
  python3 src/monitor.py ~/test/watched --route_tags logs warnings \
    --route_patterns "\.*" "\.*" --route_events "IN_ALL_EVENTS|EX_RENAME" "EX_META" \
    --dispatcher_type local
  ```
- 调试：
  ```sh
  tail -f .fswatch.logs.buf
  tail -f .fswatch.warnings.buf
  ```
- 解释：
  - 监控单个目录 `~/test/watched`
  - 2条路径，第一条用正则 `\.*` 监控所有文件，用 `IN_ALL_EVENTS|EX_RENAME` 监控原生 Inotify 的所有事件 `IN_ALL_EVENTS` 以及扩展事件 `EX_RENAME`，发送到 logs；第二条将监控器自身运行情况 `EX_META` 发送到 warnings

用例1：

- 选项
  ```sh
  python src/monitor.py ~/test/watched1 ~/test/watched2 --route_tags py cc \
    --route_patterns ".*\.py" ".*\.cc" --route_events "IN_ALL_EVENTS|EX_RENAME" IN_DELETE
  ```
- 调试：
  ```sh
  tail -f .fswatch.py.buf
  tail -f .fswatch.cc.buf
  ```
- 解释：
  - 监控 `~/test/watched1` 和 `~/test/watched2`
  - 2条路径，第一条监控 *.py 文件的所有事件（包括 rename），第二条监控 *.cc 文件的 delete 事件