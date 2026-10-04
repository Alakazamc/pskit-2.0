---
name: pskit-cloud-deploy
description: Use when publishing or rolling back PSKit cloud releases, updating the Aliyun React dist or Python/Pi image, or validating an isolated Staging release from WSL with Windows SSH keys.
---

# PSKit 云端部署

同一份镜像和前端制品依次发布到 Staging、生产。日常发布保留环境的账号、密钥、数据库及工作区，不重复执行首次迁移。

## 先确定事实

1. 找到 PSKit checkout，读取 `deploy/agent/DEPLOYMENT.md`、`STAGING.md` 和当前 Compose。`OPERATIONS.md` 中的 A6000 后端说明属于历史架构。
2. 查看 Git 状态，选定发布提交。通过 SSH 检查容器的 Compose 标签、镜像 ID、挂载、允许公开的运行参数及 Nginx root。实际主机状态优先于记忆。
3. 当前常规拓扑：阿里云运行宿主 Nginx、React dist、Python/Pi、Supabase 的单 PostgreSQL 和 LiteLLM；A6000 只运行 AF3 接收器。发布不能改变拓扑或 Pi 执行模式。

按 [操作手册](references/runbook.md) 执行下面的步骤。

## 发布流程

1. **制作制品。** 使用固定 Dockerfile、锁文件和唯一版本标签。前端显式设置 `VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1`。记录提交、构建参数、镜像 ID、dist 哈希及传输归档 SHA256；有未提交内容则先形成可追溯的源码快照。包内只含源码和制品。
2. **验收 Staging。** 复用已有私有配置，停止其专属项目后运行 `pin_staging_release.py` 更新镜像及 manifest。保留原密钥和测试卷。启动后运行 seed/smoke，通过实际私网 Nginx 检查登录、SSE、额度和 mock AF3；核对生产快照未变。特性涉及真实模型时，再做有预算上限的隔离联调。
3. **准备回滚。** 保存旧镜像 ID、发布参数及完整静态目录，比较 schema 和依赖。等待生产活动 Run/Job 安全结束，短暂阻止新 Run；有不可中断工作就继续等待或采用已验证的兼容发布方式。
4. **发布生产。** 使用验收过的相同制品，保持生产 env/卷/网络不变。代码发布优先定向更新 backend；代理代码改变才更新回调代理。不要用首次安装或迁移脚本替代日常发布。
5. **更新静态文件。** 检查权限。现有目录可写且 Nginx 配置不变时，先复制新 assets，再原子替换 index，保留旧 assets。无需 sudo 或 reload。只有真实权限或路由变更才准备 root 命令，解释具体原因。
6. **验收并记录。** 验证 HTTPS、登录 200、未登录 usage 401、internal 404、就绪、制品 ID、模型能力和相关交互。保存发布记录和回滚命令，再按用户授权提交。

## 限制

- WSL 使用 Windows OpenSSH 和 Windows config；不复制私钥。
- 不打印完整 env、resolved Compose、JWT 或 API key。
- 不使用 `latest`、`down -v`，不复制 Staging 数据到生产。
- 沙箱模式必须同时验收 manager 和用户容器版本；现有 Staging 脚本只支持本地 Pi，不可据此宣称沙箱验收通过。
- 回滚先确认旧代码兼容当前 schema/transcript；有新写入时不覆盖数据库备份。
