#!/usr/bin/env sh
# 一键部署（Linux / macOS / WSL；Windows 用 deploy/bootstrap.ps1 + 同一条 compose 命令）
#
#   用法：  sh deploy/install.sh [--skip-preflight] [--no-build]
#
# 做四件事：
#   1. 初始化 deploy/.env（自动生成缺失的安全密钥；已有的非空值不会被覆盖）
#   2. 跑 deploy/preflight.sh 做配置闸门（缺键或端口非法就停下，让你先修正）
#   3. 构建并启动 Compose 栈（项目名固定 deploy，避免连到另一个空库卷）
#   4. 打印容器状态、管理台入口和健康检查命令
#
# 幂等：重复执行只做必要动作；不会覆盖任何已生成的凭据。
# 注意：这是"首次部署/重装"入口。**已有数据的服务器做版本更新请用 deploy/apply-update.sh**，
#       它会先备份源码、.env 和 PostgreSQL dump，失败时把三者一起回退。
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
ENV_FILE="$SCRIPT_DIR/.env"
COMPOSE_FILE="$SCRIPT_DIR/compose.yml"
PROJECT=deploy

SKIP_PREFLIGHT=0
NO_BUILD=0
for arg in "$@"; do
    case "$arg" in
        --skip-preflight) SKIP_PREFLIGHT=1 ;;
        --no-build) NO_BUILD=1 ;;
        -h|--help)
            printf '%s\n' '用法: sh deploy/install.sh [--skip-preflight] [--no-build]'
            printf '%s\n' '  --skip-preflight  跳过配置闸门（仅在确认配置无误时使用）'
            printf '%s\n' '  --no-build        不重新构建镜像（仅重建/启动容器）'
            exit 0
            ;;
        *)
            printf '未知参数: %s\n' "$arg" >&2
            exit 2
            ;;
    esac
done

if ! command -v docker >/dev/null 2>&1; then
    printf '%s\n' '未找到 docker，请先安装 Docker Engine' >&2
    exit 2
fi
if ! docker compose version >/dev/null 2>&1; then
    printf '%s\n' '未找到 docker compose（需要 v2 插件）' >&2
    exit 2
fi

env_value() {
    sed -n "s/^$1=//p" "$ENV_FILE" | tail -n 1 | tr -d '\r'
}

printf '%s\n' '==> 1/4 初始化配置（deploy/.env）'
sh "$SCRIPT_DIR/bootstrap.sh" "$ENV_FILE"

if [ "$SKIP_PREFLIGHT" -eq 0 ]; then
    printf '%s\n' '==> 2/4 配置预检'
    if ! sh "$SCRIPT_DIR/preflight.sh" "$ENV_FILE"; then
        printf '%s\n' '配置预检未通过：按上面的提示修正 deploy/.env 后重跑。' >&2
        printf '%s\n' '确实要跳过就加 --skip-preflight。' >&2
        exit 1
    fi
else
    printf '%s\n' '==> 2/4 配置预检（已按要求跳过）'
fi

printf '%s\n' "==> 3/4 构建并启动（Compose 项目固定为 $PROJECT）"
if [ "$NO_BUILD" -eq 1 ]; then
    docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d
else
    docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up --build -d
fi

printf '%s\n' '==> 4/4 容器状态'
docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps

http_port=$(env_value HTTP_PORT)
http_port=${http_port:-39300}
admin_user=$(env_value ADMIN_USERNAME)

printf '\n'
printf '部署完成。\n'
printf '  管理台入口 : http://<服务器IP>:%s/\n' "$http_port"
printf '  本机健康检查: curl -fsS http://127.0.0.1:%s/api/v1/health\n' "$http_port"
printf '  管理员用户名: %s\n' "${admin_user:-admin}"
printf '  管理员密码  : 见 %s 的 ADMIN_PASSWORD（bootstrap 生成时只在首次打印一次）\n' "$ENV_FILE"
printf '\n'
printf '后续：\n'
printf '  - 配置项含义见 README.md 的"生产部署"与 deploy/README.md\n'
printf '  - 已有数据的服务器做版本更新：用 deploy/apply-update.sh（会先备份源码/.env/数据库）\n'
printf '  - 反复起不来时：docker compose -p %s --env-file %s -f %s logs --tail 200 app\n' \
    "$PROJECT" "$ENV_FILE" "$COMPOSE_FILE"
