# fswatch

### 说明

Inotify事件，主要为
- IN_OPEN, IN_CREATE, IN_ACCESS: 打开，创建，读取
- IN_MODIFY, IN_CLOSE_WRITE, IN_CLOSE_NOWRITE: 修改，有写入后关闭，无写入后关闭
- IN_MOVED_FROM, IN_MOVED_TO: 移出，移入
- IN_OVERFLOW: inotify事件队列溢出

扩展事件
- EX_RENAME: 重命名事件，由一对移出移入构成
- EX_BEGIN_MODIFY, EX_END_MODIFY: 连续修改事件的开始和结束，用于 copy 和 vim 等修改事件
- EX_META: 监控程序自身运行告警
- EX_MODIFY_CONFIG: 记录了新的文件版本

### 用法

1. （可选）在 `src/settings.py` 中修改运行选项，或写json格式配置文件
2. `python3 src/monitor.py path [path ...] --config_files [CONFIG_FILES ...] [--dispatcher_type DISPATCHER_TYPE] [--route_events [ROUTE_EVENTS ...]] [--route_patterns [ROUTE_PATTERNS ...]] [--route_tags [ROUTE_TAGS ...]] --route_formats [ROUTE_FORMATS ...]`，settings 中选项先依次被 `config_files` 覆盖，后被运行参数覆盖
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


### 查询日志

```python
>>> from database.conn import SQLEventLogger
>>> from datetime import datetime
>>> from utils import overwrite_settings
>>> overwrite_settings(argv=['--config_files', 'configs/db_config.json'])
>>> dbconn = SQLEventLogger()
>>> dbconn.query_event(from_time=datetime(2024, 5, 13, 12), to_time=datetime(2024, 5, 13, 15))  # 查询 2024/5/13 12点到15点的日志
```

结果：
```
[ExtendedEvent(IN_ISDIR|IN_OPEN, /home/user/test/watched, None, 2024-05-13 14:57:31.806956),
 ExtendedEvent(IN_ACCESS|IN_ISDIR, /home/user/test/watched, None, 2024-05-13 14:57:31.806969),
 ExtendedEvent(IN_CLOSE_NOWRITE|IN_ISDIR, /home/user/test/watched, None, 2024-05-13 14:57:31.806973),
 ExtendedEvent(IN_ISDIR|IN_OPEN, /home/user/test/watched/configs, None, 2024-05-13 14:57:31.806978),
 ...
```