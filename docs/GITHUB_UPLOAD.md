# 上传到 GitHub · 操作说明（核实于 2026-09-12 22:47）

> 本文是**当前仓库真实状态**下核实出来的步骤，不是通用模板。所有数字都是实测。

## 一、现状（为什么要先看这一节）

| 项 | 实测结果 |
| --- | --- |
| 远端 | **一个都没有**（`git remote -v` 为空）→ 从未推送过 |
| 当前分支 | `feature/phase-1`，HEAD = `f4dc5b5`，共 **15 个提交** |
| 另一分支 `master` | 只有 **2 个提交**（`.gitignore` + 设计规格文档），且 **`feature/phase-1..master` = 0** → master 完全被 feature/phase-1 包含，**不需单独推** |
| 已跟踪文件 | **393 个 / 167.0 MB** |
| 超 GitHub 单文件 100MB 硬上限的 | **0 个**（最大是 3 个容器部署包 31.4 / 31.2 / 27.3 MB） |
| 对象库 | 2325 个**松散对象**、234.29 MiB、**`in-pack: 0`** → 从来没打包过 |
| `.sh` 行尾 | 13 个全部 **LF**（含 `apply-update.sh` / `entrypoint.sh` / `service.sh`）→ 推送安全，不会因 CRLF 在服务器上失效 |
| `.gitignore` 覆盖 | `.env`、`*.jks`、`*.keystore`、`node_modules/`、`build/`、`.venv/`、`dist/`、`.tmp/` 均已忽略（`git check-ignore` 逐条验证） |
| 密钥扫描 | 跟踪文件里只命中**变量名/占位符**（`.env.example` 的 `ADMIN_TOKEN=`、构建脚本里的 `RELAY_SIGNING_STORE_PASSWORD` 键名、测试里的假值），**没有真实凭据、没有私钥** |
| 工作树 | 干净（`git status --short` 为空） |
| `gh` CLI | **未安装**（PATH 上找不到） |
| git 身份 | `Codex <codex@localhost>` ← 推上去**不归属任何 GitHub 账号** |

本仓库是 **worktree**：`.git` 是指向 `C:\Users\12071\Documents\Codex\2026-08-14\new-chat\outputs\sms-relay-hub\.git\worktrees\sms-relay-hub-phase1` 的文件。
在 worktree 里执行 `git remote add` / `push` 完全可以（与主库共享同一个对象库与配置）。

---

## 二、推送前必须做的三件事

### 1. 先设好提交身份（否则提交不归属你的账号）

```bash
git config user.name  "你的名字"
git config user.email "你的GitHub邮箱"
```

只影响**之后**的提交；已有 15 个提交的作者仍是 `Codex <codex@localhost>`。
（想改历史作者要 rewrite，属额外风险，除非你明确要求，否则不建议为此重写历史。）

### 2. 先打包（可选但强烈建议）

```bash
git gc
```

现在 `in-pack: 0`，2325 个松散对象散着放。打包后推送更快、远端也更干净。
（`warning: garbage found: .../worktrees/.../refs` 是 worktree 的无害提示，可忽略。）

### 3. 决定可见性：**建议私有**

仓库里有：服务器安装路径与容器/库名、服务器备份路径、完整运维手册与事故复盘、设备入网二维码文档，
以及整套"短信中转 + 远程控制（含规划中的远程发短信）"实现。**私有仓库**是合适的默认选择。

---

## 三、推送步骤

### 路线 A：浏览器建仓（无需装任何东西，最省事）

```bash
# 1) 在 github.com 新建**空**仓库：名字建议 sms-relay-hub，Private，
#    不要勾 "Add a README" / ".gitignore" / "license"（勾了会产生远端提交，导致首次 push 冲突）

# 2) 绑定远端
git remote add origin https://github.com/<你的用户名>/sms-relay-hub.git

# 3) 把 feature/phase-1 推成远端的 main
git push -u origin feature/phase-1:main
```

- 首次 push 会由 **Git Credential Manager**（本仓库 `credential.helper=manager`）弹浏览器登录，
  **不需要手工创建 PAT**。
- 预期上传量 **约 150–170 MB**（内容多是已压缩的 `.zip`/`.apk`，打包后不会再小多少），会慢一些。
- `master` 不必单独推；若你就是想让它以 `master` 名字出现在远端，再加：`git push origin master`。

### 路线 B：用 gh CLI（需要先安装）

```powershell
winget install --id GitHub.cli
gh auth login
# 一行建私有仓并推送（注意：它会把当前分支以同名推上去）
gh repo create sms-relay-hub --private --source . --remote origin --push
```

若要走 gh 但仍要求远端叫 `main`，用路线 A 的显式 `git push -u origin feature/phase-1:main`。

---

## 四、之后怎么推

```bash
git push                # 已 -u 绑定，直接推
git push origin feature/phase-1:main   # 想显式指定时
```

---

## 五、红线与注意

1. **不要为了"仓库变小"去删发布物。** 167 MB 里主要是：
   3 个容器部署包（90 MB）、2 个 root 模块包（18 MB）、若干 APK、2 个原生二进制（`clients/root-module/bin/runtime-linux-arm{,64}` 各约 10 MB）。
   `backend/tests/test_deploy.py` 断言**发布清单里每条记录的 size_bytes + sha256 与这些归档逐一匹配**，删了测试直接红。
   真要瘦身，得改成"归档走 GitHub Releases 外链 + 清单指向外部 URL"，那是独立一件事，别顺手做。
2. **绝不能提交**：`deploy/.env`（真实 token/口令）、`~/.sms-relay/signing/release-signing.p12`（发布签名私钥）。
   两者都在仓库外或已被 `.gitignore` 挡住；但**每次 `git add` 之前先 `git status` 看一眼**。
3. 发布签名私钥（`~\.sms-relay\signing`）**不要上传到任何地方**，但要**单独备份**——
   丢了就无法再给老用户原包升级（`android/setup-release-signing.ps1` 末尾也这么警告）。
4. **不要 `git push --force`**，尤其别对 `main`。
5. 若之后新增文件，注意 `.gitignore` 是否覆盖；新增二进制前先想清楚是否需要入库。

---

## 六、可选增强（不是必须）

- 加 `.gitattributes` 固化行尾，避免其他机器上 `core.autocrlf` 差异：
  ```
  * text=auto eol=lf
  *.sh text eol=lf
  *.zip binary
  *.apk binary
  *.png binary
  ```
- 把 167 MB 发布物迁到 GitHub Releases + 更新 `release-catalog.json` 的 `download_url` 为外链
  （会牵动后端 `release_catalog.py` 与打包脚本，需单独立项）。
