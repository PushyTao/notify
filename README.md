# 长虹 · 财经博主关注

自部署的抖音视频更新订阅应用：定时检测多个博主，获取视频语音全文，使用可配置 AI 模型总结，保存阅读记录并逐人发送邮件。

前台和邮件固定展示：**更新时间 → 博主名称 → 总结内容 → 内容全文 → 原视频标题 → 原视频链接**。更新时间为视频原始发布时间，统一显示北京时间，记录按新到旧排序。

## 功能

- 多博主监测、单独启停、检查间隔、可选首次补抓最近 N 条。
- 自部署 DTK 采集和第三方通用 JSON API；各博主独立选择，不自动切换付费服务。
- 本地 CPU faster-whisper 转写，或兼容语音转写 API。
- 多个 Chat Completions 兼容总结模型，包括 DeepSeek；配置 URL、模型 ID、API key、超时，选择一个启用模型，实际请求测试连通性。
- 默认中文提示词、自定义覆盖、恢复默认；长文本分段总结后合并。
- 多收件人、全部/指定博主订阅、SMTP/TLS 配置和测试邮件；HTML/纯文本双格式。
- 简洁响应式前台、明暗主题记忆、筛选、分页、全文展开。
- 顶部吸顶行情栏：沪深 A 股累计成交额、七个指数和证券/银行/半导体板块，与上一交易日同期比较；支持卡片拖动排序和横向滚动，详见 [行情数据与口径](docs/market.md)。
- 密码后台 `/admin`，前台没有登录按钮；会话鉴权、CSRF 校验、登录限速、加密保存密钥。
- SQLite 持久化、去重、分阶段重试、租约与心跳、后台任务状态和手动重试。

## 拉取镜像部署（推荐）

镜像：[pushytao/notify:latest](https://hub.docker.com/r/pushytao/notify/tags)，支持 **Linux amd64（x86_64）**。`main` 更新后由 GitHub Actions 测试并发布镜像；服务器无需下载源码、安装 Python 或现场构建，只需一份 YAML。以下步骤默认使用完整采集版。

### 1. 准备 Docker 和端口

服务器需安装 Docker Engine 和 Docker Compose **2.24 或更高版本**。尚未安装时，可按 [Docker 官方 Ubuntu 安装文档](https://docs.docker.com/engine/install/ubuntu/) 操作；安装后检查：

```bash
docker version
docker compose version
```

以下命令除 SSH 隧道外，均在服务器 Bash 终端执行，使用有 Docker 权限的用户。完整版本包含长虹 Web/Worker、DTK 采集 API/Worker、数据库迁移、PostgreSQL 和 Redis。首次拉取镜像需要磁盘空间，本地语音转写还需预留数 GB 可用内存。

| 端口 | 用途 | 是否开放公网 |
| --- | --- | --- |
| **8888** | 长虹前台与后台 `/admin` | 需要通过服务器 IP 访问时放行，调试时可限制为自己的 IP |
| 8000 | DTK 管理后台 | 不开放，默认仅监听服务器 `127.0.0.1` |
| 5432、6379 | PostgreSQL、Redis | 不开放，仅容器内部使用 |
| SSH 端口，通常为 22 | 服务器管理和 SSH 隧道 | 保留自己的管理访问权限 |

服务器需能访问 Docker Hub、抖音视频资源、所配置的 AI API 和 SMTP 服务。首次本地转写还需访问 Hugging Face 下载模型，行情更新需访问腾讯财经和同花顺。

### 2. 下载部署 YAML

在服务器上创建一个新的部署目录。以下使用 root 管理 `/data/changhong`；已有命名卷部署请先阅读 [迁移说明](docs/docker-storage.md)，不要直接替换旧 YAML 启动空数据库。

```bash
sudo -i
mkdir -p /data/changhong
cd /data/changhong
curl -fL https://raw.githubusercontent.com/PushyTao/notify/main/deploy/compose.full.yaml -o compose.yaml
chmod 600 compose.yaml
```

两种模板任选其一：

| 模板 | 适用情况 |
| --- | --- |
| [完整采集版](deploy/compose.full.yaml) | 推荐，包含长虹和 DTK，无需另外克隆采集服务仓库 |
| [简版](deploy/compose.image.yaml) | 已有采集服务或使用第三方 API，仅启动长虹 Web/Worker |

使用简版时，将下载地址中的 `compose.full.yaml` 换成 `compose.image.yaml`。两份模板不要在同一个 Compose 项目中同时启动。已有部署不要直接用空模板覆盖自己的配置；旧版独立 DTK 的数据卷不会自动迁入完整模板，已有 DTK 建议使用简版并配置可达的服务地址。

### 3. 填写密码、密钥和网站端口

用编辑器打开下载的文件，例如：

```bash
nano compose.yaml
```

填写文件顶部的空字符串：

| 配置项 | 要求 |
| --- | --- |
| `APP_SECRET_KEY` | 至少 32 字符的随机密钥，用于加密长虹的服务凭据 |
| `ADMIN_PASSWORD` | 至少 12 字符，首次初始化的长虹后台密码 |
| `DTK_SECRET_KEY` | 完整版必填，至少 32 字符的随机密钥 |
| `POSTGRES_PASSWORD` | 完整版必填，随机数据库密码，保留前面的 `&postgres-password` |
| `REDIS_PASSWORD` | 完整版必填，随机 Redis 密码，保留前面的 `&redis-password` |

每项使用不同的随机值，可分别运行 `openssl rand -hex 32` 生成。简版只需填写前两项。数据库连接地址会自动生成，不需要额外 `.env` 文件。手写密码若含 `$`，在 Compose YAML 中须写成 `$$`，避免被当作环境变量展开。

默认的网站端口配置是：

```yaml
ports: ["0.0.0.0:8888:8080"]
```

其中 **8888 是服务器访问端口，8080 是容器内部端口**。需要更换网站端口时，只改中间的 `8888`。仅通过本机代理或 SSH 隧道访问时，可改成 `"127.0.0.1:8888:8080"`。

保存好填完的 YAML。升级时保留 `APP_SECRET_KEY` 和 `DTK_SECRET_KEY`，更换密钥会影响已有凭据解密。数据库初始化后，不要仅修改 YAML 中的数据库密码；旧数据库不会因此自动改密。管理员初始化后通过后台修改密码。

### 4. 拉取镜像并启动

在保存了 `compose.yaml` 的目录执行：

```bash
docker compose config --quiet
docker compose pull
docker compose up -d --wait
docker compose ps -a
```

首次启动由 `init-data` 自动创建主机目录、设置权限，再初始化数据库。`init-data` 以及完整版的 `migrate` 容器退出码为 **0** 是正常现象；其他长期服务应为 running/healthy。

数据采用主机目录映射，不再存放在 Docker 自动管理的命名卷中：

| 主机目录 | 内容 |
| --- | --- |
| `/data/changhong/compose.yaml` | 你保存的部署配置及密钥 |
| `/data/changhong/app` | 长虹 SQLite 数据库、转写模型缓存 |
| `/data/changhong/dtk/postgres` | DTK PostgreSQL 数据 |
| `/data/changhong/dtk/redis` | Redis 持久化数据 |
| `/data/changhong/dtk/backups` | DTK 备份 |
| `/data/changhong/dtk/media` | DTK 媒体目录 |

简版只创建应用目录。容器内仍使用原来的路径，业务进程以非 root 用户运行，只有一次性的目录初始化容器使用 root。不要映射或覆盖镜像中的 `/app` 程序目录，更新代码通过拉取镜像完成。如需自定义根目录，在同一部署目录的 `.env` 设置 `CHANGHONG_DATA_DIR=/你的目录`，之后所有 Compose 命令都使用这份配置。

- 前台：`http://服务器IP:8888/`
- 后台：`http://服务器IP:8888/admin`
- 后台密码：刚填写的 `ADMIN_PASSWORD`

服务器本机可检查：

```bash
curl -fsS http://127.0.0.1:8888/healthz
```

如果本机能访问、外部打不开，检查云安全组是否放行 `8888`。HTTP 下保持 `COOKIE_SECURE: "false"`；配置 HTTPS 时需同步设置可信反向代理和协议转发，再开启 Secure Cookie。

### 5. 初始化 DTK 采集服务

完整采集版在服务器执行：

```bash
docker compose logs --tail=100 collector
```

从日志中找到 `/setup?token=……` 初始化链接。然后在**自己的电脑**另开一个终端，替换 SSH 用户名和服务器地址：

```bash
ssh -N -L 18000:127.0.0.1:8000 -L 18080:127.0.0.1:8888 user@server
```

保持该终端打开，在本机浏览器访问 `http://127.0.0.1:18000/setup?token=日志中的token`，创建 DTK 管理员。DTK 与长虹的管理员是两套账户。`18000`、`18080` 是本机隧道端口，无需在服务器安全组放行。

1. 在 DTK 的访问管理中创建 API Key，角色选 Operator 或更高，权限包含 `douyin:read`、`identity:manage`。
2. 打开长虹后台，也可经隧道访问 `http://127.0.0.1:18080/admin`。
3. 在采集设置中填写服务地址 **`http://collector:8000`** 和刚创建的 API Key，先保存。
4. 从同一已登录的抖音浏览器会话中复制 Cookie、User-Agent，在长虹后台导入，确认返回身份 UUID。

Cookie 失效后需重新导入。不要把 Cookie、API Key 或已填写密码的 YAML 提交到公开仓库。

简版不包含 DTK，跳过上述初始化，填写 Web/Worker 容器能够访问到的现有采集地址即可。容器里的 `localhost` 不是服务器宿主机。第三方 API 须符合 [服务接入文档](docs/providers.md) 中的接口契约。

### 6. 配置后台并验证一次完整通知

按以下顺序配置：

1. **语音转写**：默认本地 `small` 中文模型，首次运行会下载模型并持久化缓存；也可配置兼容的转写 API。
2. **模型与提示词**：填写模型的 API 基础 URL、模型 ID、API Key，测试连接并启用一个模型；保留默认提示词或自行覆盖。
3. **邮件**：配置 SMTP 主机、端口、TLS、发件地址和授权密码，先发送测试邮件。常见搭配为 `587 + starttls` 或 `465 + ssl`，以邮箱服务商要求为准。
4. **订阅者**：先添加自己的邮箱并启用；支持添加多个收件人。
5. **博主**：填写抖音完整主页地址或 `sec_user_id`，选择自部署采集，检查间隔可先用 600 秒，**首次补抓设为 1**，启用后点击“立即检查”。

首次补抓为 0 时只建立历史基线，等待后续新视频；如果要立即验证，请在首次添加时设为 1。指定博主订阅可先添加并停用博主，配置订阅后再启用。

查看处理日志：

```bash
docker compose logs -f --tail=100 worker
```

确认视频完成采集、转写和总结，前台出现记录，邮箱收到通知后，再增加博主和订阅者。按 `Ctrl+C` 只退出日志查看，不会停止服务。容器 healthy 仅表示服务可运行，不代表 Cookie、AI 或 SMTP 凭据有效。

### 7. 停止、恢复和更新

以下命令均在当前部署目录执行：

```bash
# 查看状态和日志
docker compose ps -a
docker compose logs --tail=100 web worker

# 停止服务，保留数据
docker compose stop

# 恢复服务
docker compose up -d --wait
```

默认 YAML 使用 `pushytao/notify:latest`。代码推送 `main` 且 [自动发布流程](https://github.com/PushyTao/notify/actions/workflows/docker-publish.yml) 成功后，服务器先按下一步备份，再执行以下命令更新长虹；不顺带升级 DTK、数据库或 Redis：

```bash
cd /data/changhong
docker compose pull init-data web worker
docker compose up -d --no-deps --wait web worker
docker compose ps -a
```

`pull` 只下载镜像，必须执行 `up` 才会替换运行中的容器。这里的更新命令用于已经正常运行的部署；首次启动或整套服务停止后，使用不带 `--no-deps` 的 `docker compose up -d --wait`。

需要固定版本或回退时，在 `.env` 配置 `NOTIFY_IMAGE=pushytao/notify:sha-完整提交SHA`（也可使用发布的版本号），再执行同样的拉取和重建命令。涉及数据库结构变化的版本不能仅靠旧镜像保证回退，应结合发布说明及升级前备份。

更新无需修改端口、密钥或数据目录。不要用下载的空 YAML 覆盖自己的配置，也不要删除 `/data/changhong`。旧命名卷尚未迁移时，更不能执行 `docker compose down -v`。

### 8. 备份

使用 SQLite 在线备份接口，不要直接复制正在运行的数据库主文件：

```bash
umask 077
BACKUP_DIR="backup/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"

docker compose exec -T web python -c "import sqlite3; a=sqlite3.connect('/data/notify.db'); b=sqlite3.connect('/data/backup.db'); a.backup(b); b.close(); a.close()"
docker compose cp web:/data/backup.db "$BACKUP_DIR/notify.db"
cp compose.yaml "$BACKUP_DIR/compose.yaml"
```

完整采集版继续备份 DTK 数据库：

```bash
docker compose exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U dtk -d dtk' > "$BACKUP_DIR/dtk.sql"
```

确认命令成功且备份非空，再复制到另一台设备。YAML 含加密密钥和密码，应与数据库一起安全保存。此处备份核心数据库和配置，转写模型缓存可重新下载。

独立版说明及维护者的镜像构建、发布命令见 [镜像部署说明](docs/docker-image.md)。

### 9. GitHub Actions 自动发布配置

工作流文件：[docker-publish.yml](.github/workflows/docker-publish.yml)。一次性在 [仓库 Actions Secrets](https://github.com/PushyTao/notify/settings/secrets/actions) 添加 `DOCKERHUB_TOKEN`，值为 Docker Hub `pushytao` 账号具有 Read & Write 权限的访问令牌；用户名已在工作流中设置，无需把令牌写入 YAML 或发送到聊天。

- 推送 `main`：运行 Python、前端测试，构建 Linux amd64 镜像，验证简版与完整采集版的启动、绑定目录和重建持久化，通过后发布 `latest`、`sha-完整提交SHA`。
- 推送新标签 `vX.Y.Z`：执行相同验证后发布 `X.Y.Z` 和 SHA 标签；不会用旧版本覆盖 `latest`，也不允许复用已发布的版本号。
- Pull Request：只测试和构建，不使用发布凭据、不推送镜像。
- 手动触发：Actions → 测试并发布 Docker 镜像 → Run workflow，选择 `main`；也可在配置好 Secret 后重新运行失败的发布任务。

同一分支串行构建；若验证结束时 main 已有更新提交，本次不覆盖 latest。发布的是实际通过启动验证的同一镜像。发布失败时旧镜像仍可使用；服务器不会自动重启，需主动执行前面的更新命令。

## 从源码构建 Docker 镜像

本节用于需要自行修改源码的部署；已按上面拉取镜像启动的用户无需执行。在克隆本仓库后的根目录首次配置：

```bash
python scripts/setup_env.py
```

这会生成随机密钥和初始管理密码到 `.env`，已有文件不会覆盖。也可复制 `.env.example`，自行填入 **至少 32 字符随机 `APP_SECRET_KEY`** 和 **至少 12 字符 `ADMIN_PASSWORD`**。

```bash
docker compose up -d --build --wait
docker compose ps
```

- 前台：`http://服务器IP:8888/`
- 后台：`http://服务器IP:8888/admin`
- 初始密码：查看自己生成的 `.env` 中的 `ADMIN_PASSWORD`。
- `APP_PORT` 控制宿主端口；`BIND_HOST=127.0.0.1` 可将应用仅绑定本机。
- 默认仅启动应用 Web/Worker，尚未配置采集、模型、SMTP 时不会产生真实记录或通知。

Web 和 Worker 共用主机目录 `/data/changhong/app`，可在 `.env` 中用 `CHANGHONG_DATA_DIR` 指定其他根目录。`init-data` 自动初始化目录权限，业务容器使用非 root 用户，转写模型缓存也保存在映射目录中。已有旧命名卷安装需先按 [迁移说明](docs/docker-storage.md) 迁移。首次下载模型需要访问 Hugging Face；下载耗时和识别速度取决于网络及 CPU。

## 源码部署的采集接入和首次配置

**仓库根目录的源码构建 Compose 默认只有两个应用容器，不包含 DTK。** 请按 [服务接入文档](docs/providers.md) 部署固定版本 DTK，并使用配套的 `deploy/notify.collector.yaml` 将两个项目连接到同一内部网络。上面的完整镜像部署 YAML 已包含这些服务，无需重复部署。该文档也定义第三方接口的完整请求与响应格式。

后台按以下顺序配置：

1. **设置 → 采集**：保存采集服务地址和 API Key；自部署共享网络地址为 `http://collector:8000`。需要时导入同一抖音登录浏览器的 Cookie 与 User-Agent，应用保存返回的身份 ID。
2. **设置 → 语音转写**：默认本地 `small` 模型、中文；也可选择 API 并配置地址、模型及 key。
3. **模型与提示词**：添加模型并设为当前模型，点击测试连接，按需要修改提示词。URL 填供应商 API 基础地址（例如 `https://api.deepseek.com`），模型 ID 以供应商当前实际提供的名称为准。
4. **设置 → 邮件**：设置 SMTP、TLS、发件地址和授权密码，发送测试邮件到自己的邮箱。
5. **订阅者**：添加邮箱并选择全部或指定博主。指定博主需要先添加；可以先添加博主并停用，完成订阅后再启用。
6. **博主**：填写完整主页链接或 `sec_user_id`，选择采集方式、检查间隔、首次补抓条数并启用。随后可点击“立即检查”。

密钥输入框留空表示保留已存值。Cookie 导入是独立操作，Cookie 存储由 DTK 管理；更换后可在 DTK 停用旧身份。修改管理员密码会使所有后台会话失效；初始化完成后修改 `.env` 的 `ADMIN_PASSWORD` 不会重置现有密码。

## 抓取和通知规则

- 默认每 600 秒检查，最短 60 秒。首次补抓为 0 时建立历史基线，仅处理首次成功检查后发布的新视频；补抓 N 条会处理最近 N 条并发送给当时匹配的订阅者。
- 首次检查失败不会完成初始化。采集接口需要可靠返回作品发布时间及分页游标；格式错误和分页未覆盖完整更新窗口会报错。
- “内容全文”是语音转写，不是视频简介；不包含画面 OCR、视觉描述或直播。无可识别语音会显示失败。
- 媒体默认限制 128 MiB、12 分钟；超限明确失败，不会将截断内容当全文发布。仅下载公共 HTTP(S) 地址，防止媒体 URL 访问内网。自部署/API 服务地址可以是管理员指定的内部地址。
- 完成转写和总结后才显示公开记录并创建邮件任务。修改提示词或切换模型影响后续生成，不会自动重写历史记录或重发邮件。
- 新增订阅者默认接收之后处理完成的记录，不自动补发历史邮件。每位订阅者单独投递，不向其他收件人暴露地址。
- 视频与邮件失败最多尝试 5 次，之后在“任务”中手动重试。采集失败持续退避重试，最长间隔 1 小时。成功转写会保存，AI 失败后无需重新识别。
- 重启后会恢复过期租约。正常情况不会重复抓取或整体重发；SMTP 接受邮件后、应用写入成功前发生崩溃，仍可能重复发送，不能保证严格投递一次。
- 已有视频记录的博主不能直接删除或更换账号；可停用以保留阅读历史。修改订阅关系后再删除未产生记录的博主。
- 管理后台显示最近 200 条视频任务和 200 条投递任务；公开记录通过分页查看全部完成记录。

## 源码部署的运维、备份和升级

本节命令针对从源码构建的安装；镜像部署请使用前文的更新和备份步骤。

```bash
docker compose logs --tail=100 web worker
docker compose exec worker python -m app.worker --healthcheck
```

在“概览”检查 Worker 心跳。健康检查表示进程及数据库可用，不表示抖音 Cookie、SMTP 或模型凭据有效；这些错误显示在对应任务中。

行情由 Worker 独立更新，经共享 SQLite 卷提供给 Web。无需新增环境变量，Worker 需要能访问腾讯财经和同花顺的公开 HTTPS 行情接口；行情失败不影响视频或邮件。仅运行 Web 时，前台展示已有缓存或等待更新。

备份数据库时使用 SQLite 在线备份接口，避免只复制正在使用 WAL 的主文件：

```bash
docker compose exec web python -c "import sqlite3; a=sqlite3.connect('/data/notify.db'); b=sqlite3.connect('/data/backup.db'); a.backup(b); b.close(); a.close()"
docker compose cp web:/data/backup.db ./notify-backup.db
```

另行安全备份 `.env`，尤其是 `APP_SECRET_KEY`；丢失或更换密钥后无法解密已保存的服务凭据。DTK 的数据库、密钥和 Cookie 需按上游文档独立备份。首次发布的 SQLite schema 会自动建立，后续升级涉及 schema 变化时需按对应版本迁移说明执行。

升级前备份，再运行 `docker compose up -d --build --wait`。使用 DTK 共享网络的安装必须保留覆盖文件，升级命令为：

```bash
docker compose -f compose.yaml -f deploy/notify.collector.yaml up -d --build --wait
```

此类安装执行其他 Compose 运维命令时也应使用相同的两组 `-f` 参数；省略覆盖文件后重建容器会丢失采集服务网络连接。不要使用 `docker compose down -v`，它会删除持久化数据卷。SQLite 适合单机部署，默认保持一个 Worker 实例。

外网使用建议通过 HTTPS 反向代理访问，并设 `COOKIE_SECURE=true`。代理须保留 Host 与协议信息；仅信任自己的代理来源。当前默认访问为 HTTP，因此默认 Cookie Secure 为 false。`/admin` 隐藏入口不是访问控制，所有管理 API 仍强制鉴权。

## 本地开发与测试

需要 Python 3.12+ 和 FFmpeg。使用 uv：

```bash
uv sync --extra dev --extra asr
uv run --env-file .env --extra asr uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8080
# 另一个终端，使用相同配置
uv run --env-file .env --extra asr python -m app.worker
uv run --extra dev pytest -q
```

`requirements.lock` 是 Docker 安装的带哈希生产依赖锁；`uv.lock` 用于开发解析。更新依赖后重新执行：

```bash
uv lock
uv export --locked --extra asr --no-dev --no-emit-project --format requirements-txt --output-file requirements.lock
```

测试覆盖真实 SQLite 流程、鉴权、字段顺序、模板转义、接口契约、媒体限制和失败恢复。外部接口使用可控响应验证；只有配置真实抖音身份、模型 key 和 SMTP 后，才能验证真实链路。测试数据与生产数据分离，不会自动加入演示记录。
