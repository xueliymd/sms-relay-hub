import { rmSync } from "node:fs";
import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// 容器更新包归档（web/public/assets/releases/deploy/*.zip）留在仓库里作为**本地发布历史**：
// deploy/publish-update.ps1 会往里写新包，backend/tests/test_deploy.py 断言这些归档存在且
// size/sha256 与 catalog 一致，deploy/package.ps1 打包时也会把它们从更新包里剔除。
// 但它们绝不该进入网页产物 —— Vite 会把整个 web/public 复制进 dist，Dockerfile 再把
// dist 复制进镜像（/app/app/static），于是约 95MB 的完整源码更新包会被公开下载。
// deploy/apply-update.sh 本身也主动拒绝"带 deploy 条目/目录"的更新包。
// 所以在构建结束时从产物里删掉该目录，堵住这条公开路径（源码里的归档不受影响）。
function dropContainerUpdateArchive(): Plugin {
  let outDir = "";
  return {
    name: "relay:drop-container-update-archive",
    apply: "build",
    configResolved(config) {
      outDir = path.resolve(config.root, config.build.outDir);
    },
    closeBundle() {
      rmSync(path.join(outDir, "assets", "releases", "deploy"), { recursive: true, force: true });
    },
  };
}

export default defineConfig({
  plugins: [react(), dropContainerUpdateArchive()],
  server: {
    proxy: {
      "/api": {
        // 仅本地 `npm run dev` 使用：后端容器内部应用端口是 APP_PORT（默认 39200），
        // 不存在 :8000 的部署形态。本机临时换端口时改这一行即可，不要写进任何设备接入文档。
        target: "http://127.0.0.1:39200",
        ws: true,
      },
    },
  },
});
