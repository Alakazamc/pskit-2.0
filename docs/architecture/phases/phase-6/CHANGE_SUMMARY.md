# Phase 6：目录迁移准备与归档入口

## 决策

按任务书 §3 的显式例外保留 `new_backend/`、`new_frontend/`。Phase 4/5 已先在原路径分拆模块；没有把业务修改混入 `apps/*` 大迁移。真实 Staging、source bind 与回退路径尚未验收，纯路径搬迁 Gate 保持 pending。

本阶段只有根 README 的当前入口更正、原 README 原样归档和迁移清单；旧栈代码与历史数据没有删除。该文档提交独立于业务提交。

## 必须一起核对的引用方

| 当前引用 | 下一阶段目标/验证 |
| --- | --- |
| `deploy/agent/compose.yaml` 的 `../../new_backend` context | `apps/api` Docker context/COPY/lock/Pi extension 相对路径 |
| `compose.cloud.yaml` 的 AF3 callback script source bind | 宿主文件与镜像脚本版本、活动旧 proxy 不受影响 |
| `compose.opensandbox*.yaml` 与 workspace scripts | manager/socket/rollout/工作区挂载；不能遗漏 overlay |
| `.github/workflows/new-stack.yml` | install、测试、镜像上下文、OpenAPI/TS生成与 Pi npm 入口 |
| `scripts/ci_new_stack.py`、contracts 导出与 smoke | clean snapshot、PYTHONPATH、tsconfig/Vite、测试 fixture 相对路径 |
| `new_backend/app/domain/catalog.py` / `skills/` | 构造器默认 Skill root、legacy Skill import 与本地技能映射 |
| `new_backend/pi/extension.js` / sandbox runner | 安装固定版本、沙箱路径与工具 JSON 环境白名单 |
| `backend/`、`frontend/`、旧 workflow、根 Docker/部署文件 | 必须保留旧恢复入口；先逐方迁移再搬 `legacy/` |
| 云端 Compose labels/source binds/Nginx root/旧 Manifest | 仅授权后只读核对；本轮未读取云端实际配置 |

实施步骤：冻结已测试提交 → 保存旧路径契约及完整 checkout → 单独纯路径 rename → 同步 import/workdir/COPY/CI → 全套新栈与旧恢复检查 → 独立 Staging 同制品演练 → 再申请生产路径切换。不能把文档准备描述为已完成目录搬迁。
