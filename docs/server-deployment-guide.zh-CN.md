# 服务器部署指南（零基础版）

本文带你把本项目部署到一台全新的云服务器，并通过 `https://你的域名` 访问管理后台。全文默认使用项目当前支持的生产方案：

- Ubuntu 24.04 LTS；
- Docker Engine + Docker Compose；
- 单台服务器；
- Caddy 自动申请和续期 HTTPS 证书；
- PostgreSQL、Qdrant、Redis 只在 Docker 内网运行；
- OpenAI 或兼容 OpenAI API 的真实大模型服务。

不需要在服务器上单独安装 Python、Node.js、PostgreSQL、Redis 或 Nginx。Docker 会负责这些运行环境。

> 本文所有命令都在服务器的 SSH 终端中执行，除非章节明确写着“在本地电脑操作”。命令中的 `support.example.com`、邮箱、仓库地址等示例必须替换成你自己的值。

## 1. 先理解部署后的结构

```mermaid
flowchart LR
    User["浏览器 / 网站访客"] -->|"HTTPS 443"| Caddy["Caddy：证书与反向代理"]
    Caddy --> Dashboard["Dashboard：管理后台"]
    Caddy --> API["FastAPI：业务接口"]
    API --> PostgreSQL["PostgreSQL：业务数据"]
    API --> Qdrant["Qdrant：知识向量"]
    API --> Redis["Redis：共享状态"]
    Worker["后台工作进程"] --> PostgreSQL
    Worker --> Qdrant
    Worker --> Redis
```

只有以下端口会对公网开放：

| 端口 | 用途 | 是否开放 |
| --- | --- | --- |
| `22/TCP` | SSH 登录服务器 | 是，最好只允许你的固定 IP |
| `80/TCP` | HTTP 和 HTTPS 证书签发 | 是 |
| `443/TCP` | HTTPS | 是 |
| `443/UDP` | HTTP/3，可选但 Compose 已启用 | 是 |
| `5432` | PostgreSQL | **不要开放** |
| `6333` | Qdrant | **不要开放** |
| `6379` | Redis | **不要开放** |
| `8000` | FastAPI | **不要开放** |

## 2. 上线前要准备什么

### 2.1 云服务器

初次部署建议配置：

- 系统：Ubuntu 24.04 LTS 64 位；
- CPU：至少 4 核；
- 内存：至少 8 GB，正式业务建议 16 GB；
- 系统盘：至少 50 GB SSD，并开启云盘快照；
- 公网：有固定公网 IPv4；
- 出站网络：能访问 Docker Hub、Python/npm 软件源、模型下载站和你的大模型 API。

本项目会在本机加载 embedding 和 reranker 模型。第一次启动下载模型时会比较慢，内存或磁盘太小也可能导致容器被系统杀死。

如果服务器位于中国大陆，还需要自行确认域名备案、云厂商接入和内容合规要求。未完成备案时，部分云厂商可能阻止域名提供公网 Web 服务。

### 2.2 域名

准备一个子域名，例如：

```text
support.your-company.com
```

在域名服务商控制台添加 DNS 记录：

| 类型 | 主机记录 | 值 |
| --- | --- | --- |
| `A` | `support` | 服务器公网 IPv4 |

如果服务器没有正确配置 IPv6，不要添加 `AAAA` 记录。错误的 `AAAA` 记录会导致部分用户无法访问，也可能影响证书签发。

DNS 生效需要几分钟到数小时。在本地电脑检查：

```powershell
nslookup support.your-company.com
```

返回的 IP 必须是你的服务器公网 IP。

### 2.3 账号和密钥

提前准备：

- 一个可用的大模型 API Key；
- 一个用于 HTTPS 证书通知的真实邮箱；
- 一个管理员登录邮箱；
- 项目代码仓库的读取权限（私有仓库需要部署密钥或访问令牌）。

不要把 API Key、数据库密码或 `.env.production` 发到聊天群、提交到 Git，或放进公开截图。

## 3. 连接服务器

在本地 Windows PowerShell 中执行：

```powershell
ssh ubuntu@服务器公网IP
```

不同云厂商的默认用户名可能是 `ubuntu`、`root` 或你创建实例时指定的用户名。首次连接会询问是否信任服务器指纹，核对云控制台中的 IP 后输入 `yes`。

登录后先确认系统：

```bash
cat /etc/os-release
uname -m
```

本文假设输出是 Ubuntu 24.04 和 `x86_64`/`aarch64`。

## 4. 配置云防火墙

先在云厂商控制台的“安全组”或“防火墙”中添加入站规则：

1. `22/TCP`：来源先填你当前电脑的公网 IP；
2. `80/TCP`：来源 `0.0.0.0/0`；
3. `443/TCP`：来源 `0.0.0.0/0`；
4. `443/UDP`：来源 `0.0.0.0/0`。

不要添加数据库端口。也不要在确认新 SSH 规则可用前删除原有 SSH 规则，否则可能把自己锁在服务器外。

服务器内再配置 UFW：

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw enable
sudo ufw status verbose
```

看到询问时输入 `y`。云安全组仍然是主要边界；Docker 发布端口与 UFW 的交互会因系统规则而异。

## 5. 安装 Docker

先安装基础工具：

```bash
sudo apt update
sudo apt install -y ca-certificates curl git openssl nano
```

添加 Docker 官方软件源：

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${UBUNTU_CODENAME:-$VERSION_CODENAME} stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
```

安装 Docker Engine 和 Compose 插件：

```bash
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

让当前用户可以执行 Docker：

```bash
sudo usermod -aG docker "$USER"
exit
```

重新 SSH 登录，再验证：

```bash
docker version
docker compose version
docker run --rm hello-world
```

`docker` 用户组拥有接近 root 的权限，只给可信运维人员使用。

## 6. 获取项目代码

推荐用 Git，这样以后更新最简单：

```bash
sudo mkdir -p /opt/obsidian-rag-agent
sudo chown "$USER":"$USER" /opt/obsidian-rag-agent
git clone 你的Git仓库地址 /opt/obsidian-rag-agent
cd /opt/obsidian-rag-agent
```

确认关键文件存在：

```bash
ls compose.production.yaml .env.production.example scripts/deploy.sh
git status
```

如果仓库是私有的，优先为服务器配置只读 SSH Deploy Key。不要把个人账号密码写进仓库地址或脚本。

如果只能上传压缩包，请把代码解压到 `/opt/obsidian-rag-agent`，然后执行：

```bash
cd /opt/obsidian-rag-agent
chmod +x scripts/*.sh scripts/postgres/*.sh
```

## 7. 创建生产配置

### 7.1 复制模板并限制权限

```bash
cd /opt/obsidian-rag-agent
cp .env.production.example .env.production
chmod 600 .env.production
```

`.env.production` 已被仓库的 `.gitignore` 忽略。仍应在每次提交前检查：

```bash
git status --short
```

输出中不应出现 `.env.production`。

### 7.2 生成随机值

下面每执行一次都会生成一个不同的 48 位十六进制随机串：

```bash
openssl rand -hex 24
```

至少执行 10 次，并临时保存在密码管理器中，分别用于：

| 配置名 | 要求 |
| --- | --- |
| `ENROLLMENT_TOKEN_SECRET` | 独立随机值 |
| `BOOTSTRAP_ADMIN_PASSWORD` | 独立随机值，管理员首次登录密码 |
| `POSTGRES_PASSWORD` | 独立随机值，数据库初始化账号 |
| `POSTGRES_MIGRATOR_PASSWORD` | 独立随机值，数据库迁移账号 |
| `POSTGRES_APP_PASSWORD` | 独立随机值，应用数据库账号 |
| `POSTGRES_BACKUP_PASSWORD` | 独立随机值，只读备份账号 |
| `REDIS_PASSWORD` | 独立随机值 |
| `PROMETHEUS_METRICS_TOKEN` | 独立随机值 |
| `WIDGET_TOKEN_SECRET` | 独立随机值 |

这些随机串只包含 `0-9a-f`，放进数据库和 Redis URL 时不需要 URL 编码。不要为了省事让多个配置共用同一个密码。

生成租户标识。它不是密码，但必须稳定且不要包含空格，例如：

```text
your-company-prod
```

### 7.3 编辑配置

打开文件：

```bash
nano .env.production
```

Nano 中按 `Ctrl+O`、回车保存，按 `Ctrl+X` 退出。至少修改以下内容：

```dotenv
APP_DOMAIN=support.your-company.com
ACME_EMAIL=ops@your-company.com
BUILD_SHA=这里填下面git命令输出的提交编号

ADMIN_PUBLIC_BASE_URL=https://support.your-company.com
ALLOWED_ORIGINS=["https://support.your-company.com"]
DEFAULT_TENANT_ID=your-company-prod
TENANT_EXPERIENCE_WORKER_TENANT_IDS=["your-company-prod"]

ENROLLMENT_TOKEN_SECRET=第1个随机值

BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_EMAIL=owner@your-company.com
BOOTSTRAP_ADMIN_PASSWORD=第2个随机值
BOOTSTRAP_ADMIN_DISPLAY_NAME=Tenant Owner
BOOTSTRAP_PLATFORM_OWNER=true

POSTGRES_PASSWORD=第3个随机值
POSTGRES_MIGRATOR_PASSWORD=第4个随机值
POSTGRES_APP_PASSWORD=第5个随机值
POSTGRES_BACKUP_PASSWORD=第6个随机值
DATABASE_URL=postgresql+asyncpg://app_tenant:第5个随机值@postgres:5432/customer_agent
MIGRATION_DATABASE_URL=postgresql+asyncpg://migrator:第4个随机值@postgres:5432/customer_agent

REDIS_PASSWORD=第7个随机值
REDIS_URL=redis://:第7个随机值@redis:6379/0
PROMETHEUS_METRICS_TOKEN=第8个随机值

OPENAI_API_KEY=你的真实API密钥
OPENAI_BASE_URL=
OPENAI_CHAT_MODEL=你的账号实际可用的模型名

PUBLIC_WIDGET_BASE_URL=https://support.your-company.com
WIDGET_TOKEN_SECRET=第9个随机值
```

获取 `BUILD_SHA` 要填写的值：

```bash
git rev-parse HEAD
```

配置注意事项：

- `APP_DOMAIN` 只写域名，不要写 `https://`，末尾也不要加 `/`；
- `ADMIN_PUBLIC_BASE_URL`、`ALLOWED_ORIGINS`、`PUBLIC_WIDGET_BASE_URL` 都必须是同一个 HTTPS 域名；
- `DEFAULT_TENANT_ID` 与 `TENANT_EXPERIENCE_WORKER_TENANT_IDS` 中的租户值保持一致；
- `DATABASE_URL` 使用 `POSTGRES_APP_PASSWORD`，用户名固定为 `app_tenant`；
- `MIGRATION_DATABASE_URL` 使用 `POSTGRES_MIGRATOR_PASSWORD`，用户名固定为 `migrator`；
- `REDIS_URL` 中的密码必须与 `REDIS_PASSWORD` 完全一致；
- 使用官方 OpenAI API 时 `OPENAI_BASE_URL` 保持空白；使用兼容服务时填写服务商给出的完整 HTTPS Base URL；
- `EMBEDDING_PROVIDER=fastembed` 时，`OPENAI_EMBEDDING_MODEL` 当前不参与 embedding，可保留模板值；
- 暂时不用钉钉时保持 `DINGTALK_LOGIN_ENABLED=false`，钉钉的示例字段不会生效；
- 暂时不用邮件通知时保持 `TRANSACTIONAL_EMAIL_ENABLED=false` 和 `HANDOFF_EMAIL_ENABLED=false`；
- 暂时不要开启网页爬虫，保持 `WEB_CRAWLER_ENABLED=false`；
- 保持 `RETENTION_EXECUTION_ENABLED=false`，直到隐私和数据保留策略经过审批。

检查是否还遗留了关键示例值：

```bash
grep -nE 'support\.example\.com|ops@example\.com|owner@example\.com|replace-with-(tenant|provider-key|release)' .env.production
```

理想情况是没有输出。钉钉等已禁用功能的示例值可以保留，但上述核心项目不能保留。

## 8. 第一次部署

### 8.1 部署前检查

确认 DNS：

```bash
getent ahostsv4 "$(sed -n 's/^APP_DOMAIN=//p' .env.production)"
```

确认 80 和 443 端口没有被其他程序占用：

```bash
sudo ss -lntup | grep -E ':(80|443)\b' || true
```

如果看到 Apache、Nginx 或其他 Caddy 已占用端口，先查清它是否承载别的业务，不要直接删除或停止。

### 8.2 执行部署脚本

```bash
cd /opt/obsidian-rag-agent
sh ./scripts/deploy.sh
```

脚本会依次：

1. 检查 Docker Compose 配置；
2. 拉取基础镜像并构建 API、Dashboard 镜像；
3. 在构建后的容器中校验生产环境变量；
4. 首次创建 PostgreSQL 数据库角色；
5. 自动执行 Alembic 数据库迁移；
6. 启动 API、Dashboard、后台 worker、PostgreSQL、Qdrant、Redis 和 Caddy。

第一次构建和模型下载可能需要 5 至 30 分钟，取决于服务器和网络。终端暂时没有新输出不一定是故障，不要反复按 `Ctrl+C` 或重复运行脚本。

部署完成后查看状态：

```bash
docker compose --env-file .env.production -f compose.production.yaml ps -a
```

正常状态应满足：

- `caddy`、`dashboard`、`api`、`postgres`、`qdrant`、`redis` 和常驻 worker 为 `Up`；
- 带健康检查的核心服务最终显示 `healthy`；
- `migrate` 显示 `Exited (0)` 是正常的，它只在部署时执行一次；
- `web-sync-worker` 默认未启动是正常的，因为网页抓取功能默认关闭。

## 9. 验证是否部署成功

### 9.1 从服务器检查

```bash
curl --fail --show-error https://你的域名/health/live
curl --fail --show-error https://你的域名/health/ready
```

两条命令都不应报错。然后查看近 100 行日志：

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  logs --tail=100 caddy api migrate postgres redis qdrant
```

重点查找 `error`、`failed`、`traceback`。模型首次下载期间的等待日志不一定代表失败。

### 9.2 从本地电脑检查

在浏览器打开：

```text
https://support.your-company.com
```

浏览器地址栏应显示有效 HTTPS 证书，不能有证书警告。再在本地 PowerShell 执行：

```powershell
curl.exe --fail https://support.your-company.com/health/live
curl.exe --fail https://support.your-company.com/health/ready
```

### 9.3 首次管理员登录

使用 `.env.production` 中的：

- 邮箱：`BOOTSTRAP_ADMIN_EMAIL`；
- 密码：`BOOTSTRAP_ADMIN_PASSWORD`。

首次登录后检查管理后台能正常打开，并在 Settings 中检查系统状态。管理员引导是幂等的，重启不会覆盖已有管理员密码。

### 9.4 关闭首次引导密码

确认邮箱登录成功后，在服务器执行：

```bash
cd /opt/obsidian-rag-agent
nano .env.production
```

**整行删除** `BOOTSTRAP_ADMIN_PASSWORD=...`，并确认：

```dotenv
LEGACY_LOGIN_ENABLED=false
BOOTSTRAP_PLATFORM_OWNER=false
```

不要把 `BOOTSTRAP_ADMIN_PASSWORD` 留成空值，应该删除整行。保存后重新部署：

```bash
sh ./scripts/deploy.sh
```

再次登录确认无误。把首次密码保存在离线密码管理器中作为受控的应急记录，不要留在服务器配置里。

## 10. 上线后立即做的事

### 10.1 创建第一份备份

```bash
cd /opt/obsidian-rag-agent
sh ./scripts/backup_postgres.sh
sh ./scripts/backup_qdrant.sh
```

PostgreSQL 备份应每天执行；Qdrant 冷备份会短暂停止向量服务，建议在低峰期执行。检查文件：

```bash
find backups -maxdepth 2 -type f -printf '%TY-%Tm-%Td %TH:%TM %10s %p\n'
```

备份只放在同一台服务器上不能应对整机或云盘损坏。还需要把备份加密复制到对象存储或另一台受控服务器。完整恢复说明见 [backup-and-restore.md](./backup-and-restore.md)。

### 10.2 设置磁盘和服务监控

至少监控：

- 服务器是否存活；
- HTTPS `/health/ready` 是否成功；
- CPU、内存、磁盘使用率；
- Docker 容器是否反复重启；
- PostgreSQL 和 Qdrant 备份是否过期；
- API 5xx 错误率和响应时间。

手工检查：

```bash
df -h
free -h
docker stats --no-stream
docker system df
```

不要看到磁盘紧张就直接执行带 `--volumes` 的清理命令。Docker volume 中保存着数据库和向量数据。

### 10.3 配置自动安全更新

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

系统内核更新后应安排维护窗口重启，并在重启后重新检查容器和 `/health/ready`。

## 11. 日常运维命令

每次先进入项目目录：

```bash
cd /opt/obsidian-rag-agent
```

查看状态：

```bash
docker compose --env-file .env.production -f compose.production.yaml ps -a
```

持续查看所有日志，按 `Ctrl+C` 退出日志查看，不会停止服务：

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  logs -f --tail=200
```

只看某个服务：

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  logs -f --tail=200 api
```

重启 API：

```bash
docker compose --env-file .env.production -f compose.production.yaml restart api
```

停止整套服务但保留数据：

```bash
docker compose --env-file .env.production -f compose.production.yaml down
```

重新启动：

```bash
docker compose --env-file .env.production -f compose.production.yaml up -d
```

> **绝对不要在生产环境随意执行** `docker compose down --volumes`、`docker volume prune`、`docker system prune --volumes`。这些命令可能删除数据库、Redis 和 Qdrant 数据。

## 12. 更新项目版本

更新前先在测试环境验证，并安排维护窗口。生产服务器执行：

```bash
cd /opt/obsidian-rag-agent
sh ./scripts/backup_postgres.sh
sh ./scripts/backup_qdrant.sh
git status --short
git rev-parse HEAD
```

`git status` 应无代码修改。然后更新：

```bash
git pull --ff-only
git rev-parse HEAD
nano .env.production
```

把新的提交编号写入 `BUILD_SHA`，保存后部署：

```bash
sh ./scripts/deploy.sh
curl --fail --show-error https://你的域名/health/ready
```

最后完成浏览器登录和关键业务检查。数据库迁移不会自动降级；更新失败时不要盲目恢复旧数据库，应先保留日志，再根据迁移兼容性选择修复、回退代码或从已验证备份恢复。

## 13. 常见故障排查

### 13.1 域名打不开或 HTTPS 证书失败

检查：

```bash
getent ahosts 你的域名
curl -I http://你的域名
docker compose --env-file .env.production -f compose.production.yaml \
  logs --tail=200 caddy
sudo ss -lntup | grep -E ':(80|443)\b'
```

常见原因：DNS 仍指向旧 IP、存在错误 `AAAA` 记录、云安全组没开 80/443、端口被其他 Web 服务占用、域名未备案被云厂商阻断、服务器时间错误。

### 13.2 `api` 一直 unhealthy 或反复重启

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  ps -a api postgres qdrant redis migrate
docker compose --env-file .env.production -f compose.production.yaml \
  logs --tail=300 api migrate
```

常见原因：环境变量仍有示例值、数据库 URL 密码不一致、大模型 API Key 无效、服务器无法下载本地模型、内存不足。检查是否发生 OOM：

```bash
sudo journalctl -k --since "1 hour ago" | grep -iE 'out of memory|killed process|oom' || true
```

### 13.3 数据库迁移失败

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  logs --tail=300 migrate postgres
```

全新部署最常见的是四个 PostgreSQL 密码或两个数据库 URL 填写不一致。如果这个 Docker volume 曾被其他配置初始化过，初始化脚本不会再次自动运行，按 [database-roles-and-rls.md](./database-roles-and-rls.md) 的“已有 volume”流程处理，不要删除 volume 试运气。

### 13.4 构建时下载失败

先确认磁盘和网络：

```bash
df -h
curl -I https://registry-1.docker.io/v2/
docker pull python:3.12-slim
```

Docker Hub 返回 `401 Unauthorized` 可表示网络已经到达仓库，并不等于拉取失败。若服务器所在网络无法访问镜像仓库、npm/Python 源、模型站或大模型 API，需要按云厂商和服务商文档配置合规的镜像源或出站代理。

### 13.5 修改 `.env.production` 后没有生效

`restart` 不会重新创建容器，也不会重新读取全部环境变量。执行：

```bash
sh ./scripts/deploy.sh
```

### 13.6 忘记管理员密码

不要直接修改数据库。先确认是否有其他平台管理员可以发起密码重置或邀请流程。需要启用一次性应急账号时，应走受控变更：备份、设置新的 `BOOTSTRAP_ADMIN_PASSWORD`、部署、登录修复账号、再次删除该配置并部署，同时保留审计记录。

## 14. 可选功能不要一次全开

基础登录、健康检查和备份稳定后，再逐项启用邮件、WordPress 站点、网站爬取或钉钉。每次只改一类配置，部署后立即验收，这样出现问题时容易定位。

- 邮件登录和邀请说明：[email-invitation-auth.md](./email-invitation-auth.md)
- WordPress 插件说明：[wordpress-plugin.md](./wordpress-plugin.md)
- 网站知识同步：[web-knowledge-operations.md](./web-knowledge-operations.md)
- 钉钉登录：[dingtalk-sso.md](./dingtalk-sso.md)
- 安全基线：[security.md](./security.md)
- 完整上线验收：[operations-readiness.md](./operations-readiness.md)

启用网页抓取后，还要启动带 `web-sync` profile 的 worker：

```bash
docker compose --env-file .env.production -f compose.production.yaml \
  --profile web-sync up -d web-sync-worker
```

## 15. 最终上线检查清单

以下项目全部打勾后再让真实用户使用：

- [ ] 域名 A 记录只指向当前服务器，错误 AAAA 已删除；
- [ ] 云安全组只开放必要的 22、80、443 端口；
- [ ] PostgreSQL、Qdrant、Redis、API 端口没有对公网开放；
- [ ] `.env.production` 权限为 `600`，且没有提交到 Git；
- [ ] 所有生产密码彼此不同，并已进入密码管理器；
- [ ] 大模型 API Key 有合理的额度和费用告警；
- [ ] `/health/live` 和 `/health/ready` 都成功；
- [ ] 浏览器 HTTPS 证书有效，没有安全警告；
- [ ] 管理员邮箱登录成功；
- [ ] `BOOTSTRAP_ADMIN_PASSWORD` 已从服务器配置中删除并重新部署；
- [ ] PostgreSQL 和 Qdrant 首次备份成功；
- [ ] 备份已复制到服务器之外，并安排了恢复演练；
- [ ] 已配置服务、磁盘、错误率和备份过期告警；
- [ ] 已记录本次上线的 Git 提交编号、执行人、时间和验证结果。

项目现有的英文生产运维摘要见 [deployment.md](./deployment.md)。它适合熟悉部署之后快速查命令；第一次部署请以本文的顺序为准。
