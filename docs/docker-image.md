# 使用现成 Docker 镜像部署长虹

镜像：`pushytao/notify:0.1.0`。当前发布平台为 **Linux amd64（x86_64）**，包含 FFmpeg 和本地 CPU 转写依赖。ARM 服务器不在本次验证范围。

只需 Docker Engine、Docker Compose 2.24+ 和一份 YAML；无需 Git、Python、源码、构建命令或额外 `.env`。转写模型会在首次使用时下载并缓存，AI、邮箱和抖音账号凭据仍由你在后台提供。

## 选择部署文件

| 文件 | 用途 |
| --- | --- |
| [compose.full.yaml](../deploy/compose.full.yaml) | 推荐：长虹 + DTK 采集 API/Worker + PostgreSQL + Redis，自动迁移数据库 |
| [compose.image.yaml](../deploy/compose.image.yaml) | 仅长虹 Web/Worker；用于已有采集服务或第三方 API |

两份文件任选其一，**不要同时在同一 Compose 项目中启动**。它们默认使用 `douyin-notify` 项目名，与旧版长虹的应用数据卷兼容；已有部署先备份，再切换。旧版分开部署的 DTK 数据卷不属于完整模板，不能直接当作自动迁移；已有 DTK 应优先使用简版并配置可达的采集地址。

## 1. 下载 YAML

在 Linux 服务器新建独立目录：

```bash
mkdir -p ~/changhong
cd ~/changhong
curl -fL https://raw.githubusercontent.com/PushyTao/notify/main/deploy/compose.full.yaml -o compose.yaml
chmod 600 compose.yaml
```

如果只要长虹，将下载地址中的 `compose.full.yaml` 换成 `compose.image.yaml`。不方便访问 GitHub 时，也可以从仓库下载文件后上传服务器。

## 2. 填写 YAML 顶部的配置

使用编辑器打开 `compose.yaml`，填写空字符串：

| 配置项 | 要求 |
| --- | --- |
| `APP_SECRET_KEY` | 随机密钥，至少 32 字符；加密长虹保存的凭据 |
| `ADMIN_PASSWORD` | 初始后台密码，至少 12 字符 |
| `DTK_SECRET_KEY` | 完整版必填，随机密钥至少 32 字符；加密采集身份 |
| `POSTGRES_PASSWORD` | 完整版必填，随机数据库密码；只填一次，保留前面的 `&postgres-password` |
| `REDIS_PASSWORD` | 完整版必填，随机 Redis 密码；只填一次，保留前面的 `&redis-password` |

每项使用不同的随机值，例如分别运行 `openssl rand -hex 32`。这里的 YAML 锚点会自动让相关容器使用同一份配置，数据库连接地址也会自动生成。空配置无法完成正常启动，文件中不提供公用密码。

推荐直接使用上述十六进制随机值。手写密码若含 `$`，在 Compose YAML 中须写成 `$$`，避免被当作环境变量展开；不要删除密码前面的 YAML 锚点。

默认前台端口在 `web.ports`：

```yaml
ports: ["0.0.0.0:8888:8080"]
```

默认通过服务器的 8888 端口访问，容器内仍使用 8080。例如需要使用 9090，则改成 `"0.0.0.0:9090:8080"`；仅经本机代理或 SSH 访问，可改为 `"127.0.0.1:8888:8080"`。DTK 的 `8000` 默认只绑定本机，不要直接向公网开放数据库和 Redis。

不要更换已投入使用的 `APP_SECRET_KEY`、`DTK_SECRET_KEY`。数据库初始化后也不要仅修改 YAML 中的数据库密码，旧数据库不会随环境变量自动改密。管理员密码初始化后请在后台修改。

## 3. 拉取并启动

```bash
docker compose pull
docker compose up -d --wait
docker compose ps -a
```

首次拉取包含转写、数据库等依赖，镜像体积较大。完整版本的 `migrate` 容器退出码为 0 是正常现象，表示数据库迁移成功；其他长期服务应为 running/healthy。

- 前台：`http://服务器IP:8888/`
- 后台：`http://服务器IP:8888/admin`
- 登录密码：刚填写的 `ADMIN_PASSWORD`

云安全组放行实际网站端口，调试时先限制为自己的访问 IP。HTTP 下保持 `COOKIE_SECURE: "false"`；接入 HTTPS 后还需配置可信反向代理及协议转发，再开启 Secure Cookie。

## 4. 完整版初始化采集身份

在服务器查看采集服务的首次初始化链接：

```bash
docker compose logs --tail=100 collector
```

在自己的电脑另开终端，建立 SSH 隧道，替换用户名和服务器地址：

```bash
ssh -N -L 18000:127.0.0.1:8000 -L 18080:127.0.0.1:8888 user@server
```

保持终端打开，在本机浏览器访问 `http://127.0.0.1:18000/setup?token=日志中的token`，创建 DTK 管理员。长虹后台也可通过 `http://127.0.0.1:18080/admin` 访问。

1. 在 DTK 访问管理中创建 API Key，角色 Operator 或更高，权限包含 `douyin:read` 和 `identity:manage`。
2. 长虹后台的采集设置填写 `http://collector:8000` 和该 API Key，先保存。
3. 从同一已登录的抖音浏览器会话复制 Cookie、User-Agent，在长虹后台导入，确认得到身份 UUID。

简版不包含 `collector`，需填写 Web/Worker 能访问到的现有采集地址。单机另一 Compose 项目可显式接入其网络；不能将容器中的 `localhost` 当作宿主机地址。第三方 API 须符合 [接口契约](providers.md#第三方-json-采集)，不是任意接口地址都能直接接入。

## 5. 配置 AI、邮件和博主

1. 转写：默认本地 `small` 中文模型，首次运行需要下载模型；也可设置兼容的转写 API。
2. 模型：配置 API 基础 URL、模型 ID、API Key，测试连接并启用一个模型。
3. 提示词：保留默认提示词，或按财经信息追踪需求覆盖。
4. 邮件：配置 SMTP、端口、TLS、发件地址和授权密码，先发送测试邮件。
5. 添加订阅者；添加博主并选择自部署采集，验证时首次补抓设为 1。

首次补抓为 0 时只建立基线，等待下一次新视频；这并非漏抓。容器 healthy 仅证明服务可运行，不代表 Cookie、AI Key 和 SMTP 凭据有效。只有自己的真实账号配置成功后，才能完成端到端抓取与发信验证。

## 更新、日志与停止

```bash
docker compose logs -f --tail=100 web worker
docker compose stop
docker compose up -d --wait
```

升级前备份，将 YAML 中的 `pushytao/notify:0.1.0` 改成新版本，再运行：

```bash
docker compose pull
docker compose up -d --wait
```

提供 `latest` 供尝鲜，生产建议固定版本。保留自己已填写的 YAML，不要用下载的空模板覆盖。不要执行 `docker compose down -v`，它会删除数据卷。

应用数据卷默认是 `douyin-notify_notify-data`；完整模板另有 `dtk-postgres`、`dtk-redis`、`dtk-backups`、`dtk-media` 数据卷（均带项目名前缀）。数据卷独立于镜像，正常重建容器不会丢数据。

## 备份

```bash
mkdir -p backup
chmod 700 backup
docker compose exec -T web python -c "import sqlite3; a=sqlite3.connect('/data/notify.db'); b=sqlite3.connect('/data/backup.db'); a.backup(b); b.close(); a.close()"
docker compose cp web:/data/backup.db backup/notify.db
cp compose.yaml backup/compose.yaml
```

完整版还需备份采集数据库：

```bash
docker compose exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h 127.0.0.1 -U dtk -d dtk' > backup/dtk.sql
chmod 600 backup/*
```

检查命令成功、备份非空，并复制到另一台设备。YAML 含加密密钥和密码，应与数据库一起安全保存；不应上传公开仓库。转写模型缓存可重新下载。

## 维护者：构建和验证镜像

```bash
docker build --platform linux/amd64 --build-arg APP_VERSION=0.1.0 --build-arg VCS_REF="$(git rev-parse HEAD)" -t pushytao/notify:0.1.0 .
python scripts/smoke_image.py --image pushytao/notify:0.1.0
python scripts/smoke_image.py --full --image pushytao/notify:0.1.0
docker login
docker push pushytao/notify:0.1.0
docker tag pushytao/notify:0.1.0 pushytao/notify:latest
docker push pushytao/notify:latest
```

启动验证使用随机项目名、独立数据卷和本机随机端口，检查登录、Worker 共享心跳、重建后的数据持久化；完整版还检查迁移和内部采集连接。结束仅清理本次测试项目，不操作现有部署。镜像中不包含部署 YAML、运行数据库、Cookie、API Key 或本地 `.env`。
