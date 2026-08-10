# 页面在线访客容量验证与灰度手册

本文档是页面级 Presence 发布门禁。所有 capacity、peak、soak 和故障演练必须在隔离
Staging 执行，禁止把 10,000 访客流量发送到生产环境。

## 1. 交付内容

- `tests/load/presence.js`：页面 Presence 分片负载，处理首次登记、心跳和 Token 续期。
- `tests/load/admin-presence.js`：管理员列表负载，验证 10,000 条返回和序列化延迟。
- `compose.load-test.yaml`：固定版本 k6 容器入口。
- `scripts/check_presence_load_results.py`：聚合四个 shard 并执行发布门禁。
- `scripts/check_admin_presence_result.py`：执行管理员列表单场景门禁。
- `scripts/audit_presence_staging.py`：只读核对 PostgreSQL Session 与 Redis Presence。
- `scripts/run_presence_fault_drill.sh`：带自动恢复的 Redis 暂停和 API 重启演练。
- `deploy/monitoring/presence-alerts.yml`：Prometheus Presence 告警规则。

## 2. Staging 安全边界

Staging 必须使用独立域名、PostgreSQL、Redis、Qdrant、对象存储和备份目录。测试数据库
不得包含真实客户 PII。容量目标应使用与生产相同的 API 镜像、Redis 版本、网络路径和
负载均衡策略。

~~~bash
cp .env.production.example .env.production
# 填写 Staging 专用配置
sh ./scripts/deploy.sh

docker compose --env-file .env.production -f compose.production.yaml ps
curl --fail https://staging.example.com/health/live
curl --fail https://staging.example.com/health/ready
~~~

单机 Compose 只用于功能和恢复验证。10,000 在线容量结论要求至少三个单 worker API
副本，由外部负载均衡按健康状态摘除实例。不要把单机测试结果当成高可用证明。

## 3. 真实依赖门禁

Windows 开发机可以执行现有隔离测试脚本。脚本只允许 `_test` 或 `_integration` 数据库，
并使用 Redis DB 15。

~~~powershell
pwsh -File .\scripts\run_integration_tests.ps1
~~~

Staging 部署完成后执行非破坏性验收：

~~~bash
export LAUNCH_ACCEPTANCE_PASSWORD='从安全存储读取'
python scripts/launch_acceptance.py \
  --base-url https://staging.example.com \
  --tenant-id tenant-staging \
  --username owner \
  --password-env LAUNCH_ACCEPTANCE_PASSWORD \
  --site-key 'Staging 站点密钥' \
  --require-production
~~~

## 4. 限流测试与容量测试分离

先使用生产默认值验证 429 行为：

~~~dotenv
WIDGET_PRESENCE_RATE_LIMIT_PER_MINUTE=6
WIDGET_PRESENCE_SOURCE_RATE_LIMIT_PER_MINUTE=120
WIDGET_PRESENCE_SITE_RATE_LIMIT_PER_MINUTE=30000
~~~

在这些默认值下执行单访客限流回归：

~~~bash
K6_SCRIPT=presence-rate-limit.js \
CONFIRM_STAGING=1 \
docker compose --env-file .env.load-test \
  -f compose.load-test.yaml run --rm presence-load
~~~

该场景发送 1 次 enter 和 7 次 heartbeat，要求最多 6 次成功且至少 1 次返回 429。

容量测试的四台压测机共享少量出口 IP，来源限流会掩盖 API/Redis 吞吐。仅在 Staging
容量测试期间使用：

~~~dotenv
WIDGET_PRESENCE_RATE_LIMIT_PER_MINUTE=6
WIDGET_PRESENCE_SOURCE_RATE_LIMIT_PER_MINUTE=10000
WIDGET_PRESENCE_SITE_RATE_LIMIT_PER_MINUTE=45000
~~~

容量测试后必须恢复生产默认值，再执行一次 smoke 和 NAT 聚合流量测试。预计同一出口
IP 有超过约 40 个持续在线访客时，默认 120/分钟可能触发硬限流；应依据真实站点 NAT
分布决定是否调整，不能用压测临时值直接替代生产策略。

## 5. 压测前基线

审计脚本只接受专用 Staging 连接变量，不读取生产 `DATABASE_URL` 或 `REDIS_URL`。
数据库账号只需要读取 `widget_visitor_sessions`。

~~~bash
export STAGING_AUDIT_DATABASE_URL='postgresql+asyncpg://readonly:...@postgres/agent_staging'
export STAGING_REDIS_URL='redis://:...@redis:6379/0'

python scripts/audit_presence_staging.py \
  --phase baseline \
  --tenant-id tenant-staging \
  --public-widget-id site_pub_xxx \
  --state-file load-test-results/presence-baseline.json \
  --confirm-staging
~~~

## 6. 单机 Smoke

复制示例配置，填写 Staging 域名、精确 Origin 和 public Widget ID。Origin 必须已经登记
在站点白名单。

~~~bash
cp .env.load-test.example .env.load-test
docker compose --env-file .env.load-test \
  -f compose.load-test.yaml run --rm presence-load
~~~

默认 `PROFILE=smoke`、`VUS=10`，不会误发大规模流量。Smoke 必须先通过且返回
`presence_token`，再允许进入容量阶段。所有 k6 场景都要求显式设置 `CONFIRM_STAGING=1`。

## 7. 四分片 10,000 在线

四台压测机使用相同脚本，每台配置 2,500 VU，并分别设置 shard 0、1、2、3：

~~~dotenv
PROFILE=capacity
CONFIRM_STAGING=1
VUS=2500
SHARD_ID=0
SUMMARY_PATH=/results/presence-summary-shard-0.json
~~~

每台机器执行：

~~~bash
docker compose --env-file .env.load-test \
  -f compose.load-test.yaml run --rm presence-load
~~~

分片 ID 会进入 visitor ID，四台机器不会产生重复访客。capacity 场景在 10 分钟内升到
目标，保持 30 分钟，再用 2 分钟降到零。20–25 秒随机心跳的理论平均值约为 444 RPS。

稳定阶段执行：

~~~bash
python scripts/audit_presence_staging.py \
  --phase steady \
  --tenant-id tenant-staging \
  --public-widget-id site_pub_xxx \
  --state-file load-test-results/presence-baseline.json \
  --expected-active 10000 \
  --active-tolerance 200 \
  --confirm-staging
~~~

该命令同时保证页面 Presence 没有增加 PostgreSQL `widget_visitor_sessions`。

## 8. 管理员列表测试

从专用 Staging 管理员会话取得短期 Cookie，写入未跟踪的 `.env.load-test`。测试完成后
立即撤销该会话。

~~~dotenv
K6_SCRIPT=admin-presence.js
CONFIRM_STAGING=1
ADMIN_SESSION_TOKEN=replace-with-staging-session
ADMIN_EXPECTED_MIN_ITEMS=9800
ADMIN_TEST_DURATION=30m
SUMMARY_PATH=/results/admin-presence-summary.json
~~~

在 Presence 四分片稳定运行时执行：

~~~bash
docker compose --env-file .env.load-test \
  -f compose.load-test.yaml run --rm presence-load
~~~

管理员接口 P95 必须低于 250 ms、P99 低于 500 ms，且每次返回不少于 9,800 条。前端
每组只先挂载 200 行，避免一次创建 10,000 个表格行；API 仍返回完整集合，是否增加
服务端分页由本测试的响应大小和延迟决定。

~~~bash
python scripts/check_admin_presence_result.py \
  load-test-results/admin-presence-summary.json
~~~

## 9. 汇总四分片结果

把四台机器生成的 JSON 收集到同一目录：

~~~bash
python scripts/check_presence_load_results.py \
  'load-test-results/presence-summary-shard-*.json' \
  --expected-shards 4 \
  --expected-vus 10000 \
  --minimum-requests 100000
~~~

门禁要求：

- checks rate 大于 99.5%；
- failure rate 小于 0.5%；
- 最差 shard P95 小于 250 ms，P99 小于 500 ms；
- 429、401、5xx 和其他异常状态均为零；
- 四个 shard 唯一且 VU 总数正好为 10,000。

## 10. 离线与 TTL

所有 k6 分片停止 90 秒后执行：

~~~bash
python scripts/audit_presence_staging.py \
  --phase stopped \
  --tenant-id tenant-staging \
  --public-widget-id site_pub_xxx \
  --state-file load-test-results/presence-baseline.json \
  --confirm-staging
~~~

active Presence 必须为零，Session 数必须与基线相同。约 360 秒后 Redis item 和 index
应自然过期，不执行人工删除。

## 11. Peak、Soak 与恢复峰值

同一脚本支持：

| Profile | 升载 | 保持 | 降载 |
|---|---:|---:|---:|
| capacity | 10 分钟 | 30 分钟 | 2 分钟 |
| peak | 10 分钟 | 2 小时 | 2 分钟 |
| soak | 10 分钟 | 24 小时 | 2 分钟 |

设置 `PROFILE=peak` 或 `PROFILE=soak` 即可。恢复峰值通过把心跳间隔临时设置为固定值：

- 600 RPS：约 16.7 秒；
- 800 RPS：12.5 秒。

~~~dotenv
HEARTBEAT_MIN_SECONDS=12.5
HEARTBEAT_MAX_SECONDS=12.5
~~~

该配置仅用于 30–60 秒恢复验证，随后恢复 20–25 秒。

## 12. 监控

将 `deploy/monitoring/presence-alerts.yml` 加入现有 Prometheus rule_files。应用 `/metrics`
需要 Bearer Token，并应从私网抓取，不通过公网 Caddy 暴露。

同时接入 Redis exporter、PostgreSQL exporter 和容器指标，关注：

- API CPU、RSS、重启次数和每个副本的请求分布；
- Redis CPU、used_memory、evicted_keys、rejected_connections 和命令延迟；
- PostgreSQL连接占用、连接池等待、锁和 `public_widget_registry` 查询；
- Presence接口与管理员列表的 RPS、P95、P99、429 和 5xx。

## 13. 故障演练

仅对隔离 Staging 执行：

~~~bash
CONFIRM_STAGING=1 FAULT_DURATION_SECONDS=30 \
  sh scripts/run_presence_fault_drill.sh redis-pause

CONFIRM_STAGING=1 \
  sh scripts/run_presence_fault_drill.sh api-restart
~~~

Redis 脚本使用 trap 保证中断时仍尝试 unpause。Redis 暂停期间 Dashboard 应进入 stale，
恢复后 25 秒左右重新填充。单机 API restart 只验证恢复，不证明零中断。

HA Staging 还必须由基础设施负责人执行：

1. 逐个摘除并重启三个 API 副本；
2. 执行 PostgreSQL主从切换；
3. 阻断压测机网络后集中恢复；
4. 确认故障窗口没有超过 60 秒，且没有持续 401、429 或 5xx。

## 14. 灰度与回滚

WordPress 在 Settings > Product Support Agent > Online visitor tracking 中选择：

- Only while the support panel is open：`widget_only`；
- While a visitor is viewing the site：`page_view`；
- Disabled：`disabled`。

WordPress 默认保持 `widget_only`。公共脚本使用 `data-presence-mode`，静态连接器使用
`window.CPSAWidgetConfig.presenceMode`。

~~~html
<script
  src="https://support.example.com/widget.js"
  data-site-id="site_pub_xxx"
  data-presence-mode="page_view"
></script>
~~~

灰度顺序为测试站点 24 小时、3 站点 48 小时、10 站点 3–7 天、最后 30 站点。任一阶段
出现持续 429、P95 超标、Redis内存持续增长、数据库连接等待或 5xx 超过 0.5%，立即把
相关站点切回 `widget_only`。该回滚不需要数据库降级。
