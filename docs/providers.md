# 采集、转写和通知服务

## 自部署采集：DTK v5.1.10

新部署推荐 [本项目完整镜像 YAML](../deploy/compose.full.yaml)：采用不带 HA 工具的标准 TimescaleDB，省去下面克隆上游仓库的步骤。完整操作见 [README](../README.md#拉取镜像部署推荐)。下文是上游 DTK 的独立部署方案，继续使用上游自己的数据库配置。

本项目接入 [Douyin_TikTok_Download_API v5.1.10](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/releases/tag/v5.1.10)，镜像固定为 `evil0ctal/douyin_tiktok_download_api:sha-26299ef8649a12be82c3769b05b316fdeec8d40d`。需要 Docker Compose 2.24 或更新版本。只启动上游的 API、Worker、迁移、PostgreSQL 和 Redis；导入登录 Cookie 后不需要启用浏览器、下载或转写组件。若已有此版本的实例，可跳到后台设置步骤。

在 Linux 或 WSL 的 Bash 终端进入本项目根目录，执行：

```bash
NOTIFY_DIR="$PWD"
git clone --branch v5.1.10 --single-branch \
  https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git ../douyin-notify-dtk
cd ../douyin-notify-dtk
test "$(git rev-parse HEAD)" = 26299ef8649a12be82c3769b05b316fdeec8d40d || exit 1
docker network inspect douyin-notify-collector >/dev/null 2>&1 || \
  docker network create douyin-notify-collector

# 仅首次安装时生成。保留这份文件；勿覆盖已有实例的密码和密钥。
if [ ! -e .env ]; then
  umask 077
  PG_PASS=$(openssl rand -hex 24)
  REDIS_PASS=$(openssl rand -hex 24)
  cat > .env <<EOF
DTK_SECRET_KEY=$(openssl rand -base64 48)
POSTGRES_PASSWORD=$PG_PASS
REDIS_PASSWORD=$REDIS_PASS
DTK_DATABASE_URL=postgresql+asyncpg://dtk:$PG_PASS@postgres:5432/dtk
DTK_REDIS_URL=redis://:$REDIS_PASS@redis:6379/0
DTK_IMAGE=evil0ctal/douyin_tiktok_download_api
DTK_IMAGE_TAG=sha-26299ef8649a12be82c3769b05b316fdeec8d40d
DTK_BIND_HOST=127.0.0.1
DTK_BIND_PORT=8000
EOF
fi
docker compose --env-file .env -p dtk -f docker/compose.yml \
  -f "$NOTIFY_DIR/deploy/dtk.notify.yaml" up -d --no-build --wait
docker compose --env-file .env -p dtk -f docker/compose.yml \
  -f "$NOTIFY_DIR/deploy/dtk.notify.yaml" logs api
```

打开 DTK 日志里给出的本地 `/setup?token=…` 链接，创建上游管理员。采集控制台只监听服务器的 `127.0.0.1:8000`；远程机器可通过 `ssh -L 8000:127.0.0.1:8000 user@server` 后在自己的浏览器访问。部署细节见 [上游固定版本安装文档](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.10/documents/en/02-installation.md)。

配置本项目的 `.env`，再用共享网络启动应用：

```bash
cd "$NOTIFY_DIR"
docker compose -f compose.yaml -f deploy/notify.collector.yaml up -d --build --wait
```

1. 在 DTK 控制台的访问管理页面创建 API Key。若通过本项目导入 Cookie，使用 Operator（或更高）角色和 `douyin:read`、`identity:manage` 两个 scope。仅使用已有身份池自动分配时，可用只授予 `douyin:read` 的 Key。
2. 在本项目 `/admin` 的采集设置保存 DTK 地址 `http://collector:8000` 和 API Key。若用现有 DTK，填写 Web 和 Worker 都可访问的根地址；跨主机使用 HTTPS。
3. 在同一已登录的抖音浏览器会话复制 Cookie 和 User-Agent，在本项目后台导入。应用通过 [DTK 身份导入 API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.10/src/dtk/api/routes/admin/identities.py#L753) 先校验后导入，并只保存返回的身份 UUID。DTK 负责加密存储 Cookie。身份只服务显式携带该 UUID 的请求；替换 Cookie 会新建身份，确认新身份可用后可在 DTK 控制台停用旧身份。
4. 在本项目添加博主，采集方式选自部署，填抖音主页 URL 或 `sec_user_id`。不导入也可在 DTK 直接添加可供调度的登录身份，或手动在本项目设置已有的身份 UUID。访客列表可能缺最新视频，应用会将标记为 `guest_fallback` 的结果视为失败。登录 Cookie 失效、平台拒绝或限流时可在 DTK 控制台检查该身份的登录和请求状态。

适配器调用 `GET /api/v1/douyin/user/posts`，用 `X-API-Key` 鉴权，按上游返回的游标分页。`created_at` 来自作品的原始发布时间，媒体来自 `media.video.url`，图文、直播、非公开和已删除作品不纳入。接口协议和解析字段见 [DTK API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.10/documents/en/11-api.md#pagination) 和 [固定版本的抖音解析器](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/v5.1.10/src/dtk/platforms/douyin/parser.py#L357)。

## 第三方 JSON 采集

第三方地址是完整的 API 路径，例如 `https://provider.example/feed`。请求为 `GET`，首次只传 `author_id`，后续原样传上页 `next_cursor`：

```text
GET /feed?author_id=creator-id&cursor=opaque-value
Authorization: Bearer <供应商 API Key>
```

返回一个 JSON 对象，格式如下：

```json
{
  "items": [
    {
      "video_id": "7123456789012345678",
      "author_name": "示例博主",
      "published_at": 1770000000,
      "title": "示例标题",
      "url": "https://www.douyin.com/video/7123456789012345678",
      "media_url": "https://cdn.example/video.mp4"
    }
  ],
  "next_cursor": null
}
```

- `video_id` 是稳定字符串，`published_at` 是 Unix 秒（整数），不可用采集时间或毫秒代替。博主的后台显示名优先，空标题会显示为 `（无标题）`。
- 返回最近作品在前、按原始发布时间倒序的稳定分页。末页必须用 `next_cursor: null`，中间页返回非空游标字符串；可能重复的视频 ID 会去重。
- 正常响应必须含 `items` 和 `next_cursor`。合法的空末页可用于建立基线。HTTP 错误、返回错误对象或格式不符均视为采集失败；访问受限时须返回错误。
- 采集默认最多 20 页，上限可调至 100 页。实际有未读页、尚未读到本次应覆盖的时间窗口时会明确报错，不会拿部分结果更新基线。首次不补抓时只需要确认来源可用；有补抓数时会继续读取到覆盖最近 N 条。常规轮询会覆盖上次成功起的更新，并保留最多一天重叠。
- 第三方格式不同的接口需要额外适配或网关。本项目只按博主选择的方式采集，不自动切换到可能收费的第三方服务。

## 语音转写

默认使用 `faster-whisper` 的 `small` 多语言模型，在 CPU 以 INT8 运行。Docker 镜像包括 `ffmpeg`，模型首次需要从 Hugging Face 下载，保存在持久化的数据卷；首次执行比日常任务慢。建议给应用数 GB 可用内存，并根据 CPU 和模型大小预留运行时间。原文为可识别语音，不含画面 OCR。

API 模式填入兼容 OpenAI 转写协议的地址（如服务的 `/v1`）和模型 ID（如 `whisper-1`）。应用先下载媒体，在本地用 `ffmpeg` 提取音频，再以 multipart `file` 发送到 `/audio/transcriptions`，读取响应的 `text`。空白文本、没有音轨或无法解码会使任务失败。

### 小米 MiMo ASR

在长虹 `/admin` → 设置 → 语音转写中选择 **小米 MiMo ASR**，填写并保存：

| 字段 | 值 |
| --- | --- |
| 转写方式 | 小米 MiMo ASR |
| 转写 API 基础地址 | `https://api.xiaomimimo.com/v1`（选择时自动填入；留空也使用此地址） |
| 转写模型 | `mimo-v2.5-asr`（自动填入） |
| 转写 API 密钥 | 小米 MiMo 开放平台的 API Key，不是 DTK 的 Key |
| 语言代码 | 中文 `zh`、英文 `en`，或 `auto`；留空自动识别 |

此方式在小米云端转写，不下载或加载本地 Whisper 模型；服务器仍需 FFmpeg 提取音频，镜像已包含。音频会发送给所配置的服务并按其规则计费。切换转写方式后重新填写对应密钥，避免沿用其他提供方的凭据；同一方式保存时密钥留空仍保留原值。想恢复本地转写，选择“本地 faster-whisper”，确认模型（默认 `small`）后保存。

小米接口使用 `/chat/completions` 和 Base64 WAV 音频，与通用 API 的 `/audio/transcriptions` 协议不同，因此必须选择“小米 MiMo ASR”。程序将提取后的 16 kHz 单声道音频按最多 60 秒顺序分段，每段低于官方的 10 MB Base64 限制，读取 `choices[0].message.content` 后按顺序合并。总时长仍最多 12 分钟；任一段失败、返回空白或被截断时，整次转写失败，不保存部分全文。分段边界可能影响个别词句，财经数字和专有名词仍应核对原视频。任务重试会重新发送音频，可能重复计费。

配置后可对一个首次补抓设为 1 的博主“立即检查”，在视频任务和 Worker 日志中确认转写结果。需要小米有效凭据和可用额度才能验证真实识别效果；保存配置本身不会调用计费接口。协议参考：[小米语音识别指南](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/Speech-Recognition)、[API 文档](https://mimo.mi.com/docs/zh-CN/api/audio/Speech-Recognition)。

媒体仅允许指向公共地址的 HTTP(S) 默认端口；每次跳转重新验证，DNS 验证后按检查通过的 IP 连接，HTTPS 仍校验证书。最多 4 次跳转、128 MiB 下载、12 分钟音频；临时媒体和音频文件在处理完成或失败后清理。重试转写时，通过 DTK 视频详情接口刷新签名媒体地址，或在第三方来源的作品列表中按视频 ID 查找。第三方来源须重新提供有效链接；作品已从列表移除或超出分页上限时，无法刷新。

## 总结模型与 SMTP

总结模型地址是兼容 Chat Completions 的 API 根路径，例如 `https://api.deepseek.com` 或供应商提供的 `/v1`，填写真实模型 ID 和 API Key。后台的模型测试会发出一次实际请求；长转写会分段总结再合并，且模型不返回有效内容时任务会失败。

SMTP 请按供应商要求选择 `starttls`（通常端口 587）、`ssl`（通常端口 465）或可信本地中继使用的 `none`，设置允许发信的地址并在后台测试。每次投递只发给一位订阅者，邮件包含纯文本和 HTML 两种版本。
