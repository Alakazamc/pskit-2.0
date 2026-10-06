# 2026-10-07 MCP 工具产品与 Molstar 全量代码发布

生产入口：<https://agent.bioailab.net>。发布当前已完成的 Python/Pi 与 React 代码，包含配置驱动 Tool Product、MCP 接入验收及管理台、登录页更新和 Molstar 体积优化。现有账号、模型配置、会话、工作卷及配额保留。

## 固定制品

| 字段 | 值 |
| --- | --- |
| Release | `20261007-mcp-ui-molstar-86d842c` |
| 源码提交 | `86d842caed1f0e80e23bebdb41eaafc72dbfbdcf` |
| 阿里云版本目录 | `/home/ecs-user/pskit-agent-releases/20261007-mcp-ui-molstar-86d842c` |
| 后端发布标签 | `pskit-agent-backend:20261007-mcp-ui-molstar-86d842c` |
| 后端镜像 ID | `sha256:03107b7953e5e214b624233265b07cba1dd7df039a6c03323ed1aa9d4f071282` |
| 后端复用来源 | `pskit-agent-backend:20261007-tool-products-runtime-r4` |
| 本地完整重建候选 ID | `sha256:2c72c7a0e8679a420a20b99ae2c32b8054b47c3c9445da8bf0632dba39be246f` |
| 前端 dist 内容哈希 | `677fc1532bdb44162700861306a50b0c8d0cd90fa19e0417cbf41e0580d8058b` |
| 入口 HTML SHA256 | `205d547bd24f32242f991d6a833be7b39efa0b03d990a0c5cf429678aa03f6a8` |
| 发布归档 SHA256 | `4c2705e3d9bb2c9c9c657c15ad357e3f81ac6f866872c581bfea80c8ef412136` |
| 源码归档 SHA256 | `9145a660f43a6adaf37c76fd9140c87f8f6c62993b170e8c2f4e1656cd748de4` |
| 前端构建参数 | `VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1`，保留生产公开 Turnstile site key |

完整固定 Dockerfile 重建使用缓存，产出的 RootFS 文件层和执行 Config（除 Labels）与已验收 r4 镜像完全一致。本次采用相同 r4 镜像的发布别名，避免再次传输完整镜像；该复用镜像没有 OCI revision 标签，不能宣称它带有本次 revision。源码、完整重建候选及文件层一致性记录保存在服务器 `release.json`。

源码与前端归档为 9,031,232 字节，通过 Windows OpenSSH 传输并核对 SHA256；没有复制 SSH 私钥或私有环境配置。

## Staging 与生产验收

- 同一镜像和前端制品先在隔离 Staging 验收；通过真实私网 Nginx 检查登录、文件、Agent SSE、Token/GPU 额度、超额拒绝及模拟 AF3。烟测前后生产表计数及 LiteLLM 计费摘要一致。
- 收尾发现一条旧 Staging 工具记录仍为 queued，其关联计算任务早已 cancelled，接收器 journal 为空。通过该测试账号的正常取消 API 同步工具终态后，确认 Run、Job、Tool Run 均无活动记录，停止三个 Staging 项目并保留测试卷。没有停止活动计算或删除持久记录。
- 通用 CORAL 生命周期证据沿用相同 r4 镜像的最终真实服务验收，见 `new_backend/fixtures/tool_products/coral.staging.acceptance.yaml`；四能力科学验收见 `coral.acceptance.yaml`。没有把测试产品、账号、验收报告或审批记录迁入生产。
- 生产切换前确认活动 Agent Run 与 Job 为零。显式执行 v9/v10 兼容结构扩展、授予应用表权限，随后只定向重建 backend。
- 切换前后，历史 Run、消息、项目、会话、Pi 会话、Artifact、Job、管理员角色和 Token 使用记录的计数与内容摘要完全一致。
- 生产后端 healthy，restart count 为 0；保持 `live / pi / local Pi`、原 LiteLLM 配置及认证 observe 模式。Supabase、PostgreSQL、LiteLLM、AF3 回调代理、A6000 和其他项目没有参与生产更新。
- 401 个发布文件逐一与生产静态目录核对一致；保留旧哈希资源并原子替换入口。现有 Nginx gzip、immutable 静态缓存和管理员私网 ACL 已生效，因此没有修改或 reload 宿主 Nginx。
- 公网登录页 200，未登录 usage 和新 Tool Product API 为 401，internal 为 404，公网管理 UI/API 为 403。WireGuard 管理页 200、未登录管理 API 401。
- 使用服务器已有测试账号验收生产登录、身份、额度、模型目录及 Tool Product 目录；目录返回 21 个模型。没有执行付费模型调用或新增生产测试任务。
- 通过 HTTPS 比对入口资源、登录背景及 Molstar 的 26 个公开文件；20 个文本资源 gzip 返回，哈希资源均携带一年 immutable 缓存。
- 浏览器使用实际云端 HTML/JS/CSS、测试身份 API 替身和 RCSB 1CRN 样本：远程 BinaryCIF、本地 PDB/mmCIF、旋转和主题切换通过，没有运行错误。打开结构前没有 Molstar 请求。

Molstar 的原 4.88 MB 单文件已拆成 18 个按需加载文件，最大 436,643 字节；结构查看器新增按需 JS 总 gzip 为 1,023,683 字节。构建脚本验证最大文件、总 gzip 预算及初始导入隔离。

## 正式服务开放范围

管理入口为 `https://agent.bioailab.net/admin/tool-products`，仅 WireGuard 可访问。生产尚无已审核发布的 Tool Product；CORAL/MCP 生产接收器 profile 保持关闭，原 Agent MCP 执行模式也未改变。

本次是应用代码发布。正式开放 CORAL 或其他 MCP 服务仍需按管理台完成生产探测、发现、验收及管理员批准，并配置对应生产接收器、服务密钥与允许的网络区域；不得用 Staging 的审批记录或私有配置替代这些步骤。

## 回滚

私有材料在该版本目录的 `production-backup`：原发布配置、活动后端环境、完整旧前端和 PSKit 自定义格式数据库备份。数据库备份 SHA256 为 `0c6bd3b5b56bf6e7afaa1c3ac959fa86549c26f28774b9e843dfb988df4f2403`。目录 0700，私有文件 0600。

旧程序原本拒绝 schema v10。已准备并在 Staging schema v10 上验收兼容回退镜像：

- 标签：`pskit-agent-backend:20261007-pre-tool-products-schema10`
- ID：`sha256:828caf24dbb6b2838664c88843261fdab614c2d73722a03686ff5f268a17f02b`
- 仅将旧程序的 `SCHEMA_VERSION` 校验从 8 改为 10；旧八版迁移与其余代码不变，新表和可空列由旧程序忽略。
- 实际启动通过，ready 200、未登录 usage 401；补丁与校验记录在版本目录的 `rollback-build` 和 `rollback-verified.json`。

回滚前等待无活动任务，把 `cloud.env` 中唯一的 `AGENT_BACKEND_IMAGE` 与 `.env.stack` 中存在的 `STACK_BACKEND_IMAGE` 改为该兼容回退标签，再用同一 Compose 项目定向更新 backend。原子恢复旧 `index.html`：

```bash
cd /home/ecs-user/pskit-agent-cloud-20261002
# 先将上述两处 image pin 改成已验收的兼容回退标签。
docker compose --env-file deploy/agent/cloud.env \
  -f deploy/agent/compose.yaml -f deploy/agent/compose.cloud.yaml \
  -f deploy/agent/compose.postgres.yaml -p pskit-agent-cloud \
  up -d --no-deps --wait backend
tar -xOf /home/ecs-user/pskit-agent-releases/20261007-mcp-ui-molstar-86d842c/production-backup/frontend-before.tar.gz \
  frontend/index.html > /var/www/agent.bioailab.net/.index.rollback
chmod 0644 /var/www/agent.bioailab.net/.index.rollback
mv /var/www/agent.bioailab.net/.index.rollback /var/www/agent.bioailab.net/index.html
```

回退保留 v10 数据库及发布后的用户写入，不覆盖数据库备份，不删除用户卷。新产生的 Tool Product 历史在 v10 表中保留；旧程序不展示这些记录。恢复后重新检查 ready、模型目录、HTTPS 入口和管理边界。
