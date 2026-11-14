# 主机目录映射与旧命名卷迁移

当前的源码 Compose、简版和完整镜像 Compose 都把数据映射到主机 `/data/changhong` 下。程序代码留在镜像里；拉取新镜像、重建容器会更新代码，继续使用原来的数据。

## 目录和权限

| 主机路径 | 容器路径 | UID:GID |
| --- | --- | --- |
| `/data/changhong/app` | 长虹 `/data` | 10001:10001 |
| `/data/changhong/dtk/postgres` | PostgreSQL `/home/postgres/pgdata/data` | 1000:1000 |
| `/data/changhong/dtk/redis` | Redis `/data` | 999:1000 |
| `/data/changhong/dtk/backups` | DTK `/var/lib/dtk/backups` | 10001:10001 |
| `/data/changhong/dtk/media` | DTK `/var/lib/dtk/media` | 10001:10001 |

`init-data` 在业务服务前执行：建立目录、拒绝符号链接、设置目录属主和 0700 权限。只有这个一次性容器使用 root，业务服务继续使用非 root 用户。不使用递归 chown，不修改已有数据库文件的内容；不要手动将目录设为 777。

可以把 `compose.yaml` 和备份也保存在 `/data/changhong`，但配置文件不会挂载进业务容器。转写模型保存在 `app/models`，SQLite 主库是 `app/notify.db`。只需长虹时，简版仅创建 `app`。

自定义根目录：在部署目录的 `.env` 中写入 `CHANGHONG_DATA_DIR=/其他绝对路径`，并始终在同一目录使用 Compose。不设置时默认 `/data/changhong`。测试脚本会覆盖为独立临时目录，不会访问生产路径。

## 已用旧版本部署：先迁移，再换 YAML

**旧命名卷不会自动变成绑定目录。直接更换 YAML 会启动一个新的空数据库，看起来像数据丢失。** 旧数据此时仍在原卷中，应停机并正确迁移，不能删除旧卷。

下面适用于本项目此前的 **单份完整镜像 YAML**，默认项目名 `douyin-notify`。如果改过项目名，请替换卷名前缀；若此前分开部署了 DTK，则它通常属于另一项目，不能套用这里的 DTK 卷名。

### 1. 备份并停止旧服务

先按 README 进行 SQLite 在线备份、PostgreSQL 导出，并安全保存旧 YAML 中的全部密钥。随后在旧部署目录执行：

```bash
docker compose stop
docker compose ps -a
docker volume ls
```

从源码部署并使用覆盖文件时，停止命令也必须带原来的 `-f` 参数。确认没有其他实例访问这些卷。不要执行 `down -v` 或删除卷。

### 2. 将冷数据复制到空主机目录

以下在服务器 Bash 中以 root 执行；Docker 必须指向保存旧卷的同一个引擎。使用固定的已有镜像作为复制工具，不启动应用。

```bash
sudo -i
set -e
docker pull pushytao/notify:0.1.0

copy_volume() {
  local volume="$1" destination="$2"
  # 不存在就停止，防止 Docker 悄悄创建一个空源卷。
  docker volume inspect "$volume" >/dev/null
  if [ -n "$(docker ps -q --filter "volume=$volume")" ]; then
    echo "请先停止仍在使用 $volume 的容器" >&2
    return 1
  fi
  mkdir -p "$destination"
  docker run --rm --user 0:0 --entrypoint sh \
    --mount "type=volume,src=$volume,dst=/old,readonly" \
    --mount "type=bind,src=$destination,dst=/new" \
    pushytao/notify:0.1.0 -ec '
      test -z "$(ls -A /new)" || { echo "目标非空，停止复制" >&2; exit 1; }
      cp -a /old/. /new/
    '
}

copy_volume douyin-notify_notify-data /data/changhong/app
```

如果是此前的完整采集版，在同一终端继续：

```bash
copy_volume douyin-notify_dtk-postgres /data/changhong/dtk/postgres
copy_volume douyin-notify_dtk-redis /data/changhong/dtk/redis
copy_volume douyin-notify_dtk-backups /data/changhong/dtk/backups
copy_volume douyin-notify_dtk-media /data/changhong/dtk/media
```

简版只复制 `notify-data`。`cp -a` 保留所有文件及原有 UID/GID，包括 SQLite 的 WAL 文件和 PostgreSQL 数据目录；源卷只读。目标必须为空，不能覆盖或混合新旧数据库。中途失败时不要继续启动，先检查失败原因和已复制目录；保留旧卷作为恢复来源。

### 3. 下载新 YAML 并保留原密钥

```bash
cd /data/changhong
curl -fL https://raw.githubusercontent.com/PushyTao/notify/main/deploy/compose.full.yaml -o compose.new.yaml
chmod 600 compose.new.yaml
```

简版改用 `compose.image.yaml` 的下载地址。编辑 `compose.new.yaml`，从旧 YAML 原样保留 `APP_SECRET_KEY`、`ADMIN_PASSWORD`、`DTK_SECRET_KEY`、数据库与 Redis 密码，以及实际使用的端口。**不要生成新密钥或新数据库密码。** 原来的 AI、邮箱、博主等设置已经在复制的数据库中，无需重新填写。

核对原数据库镜像版本，不要在存储迁移时同时更换 PostgreSQL 主版本。原来使用了 `CHANGHONG_DATA_DIR`、项目名或其他环境覆盖时，保持路径与实际复制目标一致。

```bash
docker compose -f compose.new.yaml config --quiet
# 如果已有 compose.yaml，先另存备份，不要直接覆盖。
test ! -e compose.yaml
mv compose.new.yaml compose.yaml
docker compose up -d --wait
docker compose ps -a
```

用旧后台密码登录，确认博主、模型、记录、订阅者和 DTK 身份都存在。确认新实例正常且备份可用后，再自行决定旧卷的保留时间。本文不包含删除旧卷的命令。

若启动失败，停止新实例后，可以用原 YAML 和原卷恢复旧部署；新旧实例不要同时发送邮件。切换后新产生的数据不会自动同步回旧卷。

## 日常更新与备份

存储迁移只需进行一次，之后在原目录更新：

```bash
cd /data/changhong
docker compose pull init-data web worker
docker compose up -d --no-deps --wait web worker
```

容器重建不会删除绑定目录。备份仍需使用 SQLite 在线备份与 `pg_dump`，或在所有服务停机后复制完整数据目录；不要把在线复制 PostgreSQL 目录当作一致性备份。必须同时保存 YAML 中的加密密钥。删除 `/data/changhong` 会删除实际数据，Docker 镜像无法恢复它。
