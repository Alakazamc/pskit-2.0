# Phase 1 迁移与回滚

无数据库 schema 或数据迁移；无目录移动；无生产部署。

## 运维影响

旧根发布脚本总是非零退出，新维护者使用当前应用发布手册；不要恢复其停库、
自动 provision 或测试 key 回退。生产配置仍为 observe 或缺少必需字段时，新
预检会拒绝。此拒绝是 fail closed，需在授权的 Staging 中验证配置后另行切换，
本次没有替操作员编辑真实 env。

## 回滚单位

1. 前端修复为独立 `fix(frontend-events)` 提交；可 revert 并重新构建旧前端。
   浏览器草稿、模型偏好和公开资源 ID 没有变更。
2. 预检与旧入口为独立 `fix(deploy-release)` 提交。回退应用时使用已知好镜像和
   旧 index，保留完整 OpenSandbox overlays、旧 hashed assets 和工作区卷；
   不回退数据库、不停止 LiteLLM/Supabase。
3. 云端演练需明确授权。检查 ready、鉴权、SSE/cursor、文件产物以及实际
   enforce/429/Turnstile；无这些证据不能声明部署或防护验收完成。

后续 CI、契约生成物、离线迁移工具修复分别提交，避免回退流式输出时同时
撤销其他领域修改。
