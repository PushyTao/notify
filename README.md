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

镜像：`pushytao/notify:0.1.0`，Linux amd64。无需下载源码或在服务器构建。

下载 [完整部署 YAML](deploy/compose.full.yaml) 为 `compose.yaml`，填写顶部五项密钥/密码，即可启动长虹和自部署采集服务；如果已有采集服务，使用 [简版 YAML](deploy/compose.image.yaml)，只需填写两项。

```bash
docker compose pull
docker compose up -d --wait
```

前台 `http://服务器IP:8080`，后台 `/admin`。抖音 Cookie、模型、SMTP 和订阅者在后台配置。完整步骤、采集初始化、升级与备份见 [镜像部署说明](docs/docker-image.md)。

## 从源码构建 Docker 镜像

需要 Docker Engine 和 Docker Compose。首次配置：

```bash
python scripts/setup_env.py
```

这会生成随机密钥和初始管理密码到 `.env`，已有文件不会覆盖。也可复制 `.env.example`，自行填入 **至少 32 字符随机 `APP_SECRET_KEY`** 和 **至少 12 字符 `ADMIN_PASSWORD`**。

```bash
docker compose up -d --build --wait
docker compose ps
```

- 前台：`http://服务器IP:8080/`
- 后台：`http://服务器IP:8080/admin`
- 初始密码：查看自己生成的 `.env` 中的 `ADMIN_PASSWORD`。
- `APP_PORT` 控制宿主端口；`BIND_HOST=127.0.0.1` 可将应用仅绑定本机。
- 默认仅启动应用 Web/Worker，尚未配置采集、模型、SMTP 时不会产生真实记录或通知。

Web 和 Worker 共用持久化卷 `douyin-notify_notify-data`。容器使用非 root 用户，转写模型缓存也保存在数据卷中。首次下载转写模型需要可访问 Hugging Face；下载耗时和识别速度取决于网络及 CPU。建议先预留数 GB 内存，处理较多视频时再按监测结果调整。

## 自部署采集和首次配置

**自部署采集服务有独立容器和初始化步骤，不包含在默认的两个应用容器里。** 请按 [服务接入文档](docs/providers.md) 部署固定版本 DTK，并使用配套的 `deploy/notify.collector.yaml` 将两个项目连接到同一内部网络。该文档也定义第三方接口的完整请求与响应格式。

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

## 运维、备份和升级

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
