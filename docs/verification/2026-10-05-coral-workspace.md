# CORAL 工作区验收（2026-10-05）

## 范围与事实来源

入口 `/tools/coral`，蛋白质 PDB/链输入、服务范围内的长度与数量、真实任务状态节点、结果/下载、单工具历史，以及预设任务的新 Session 交接。

CORAL 输入取自旧后端 `CoralInput` 与 `call_remote_rna_expert`。实现使用新后端既有通用计算接口，新加的服务器接口仅为 owned、按能力过滤的轻量任务历史。接入与边界见 [CORAL.md](../../new_backend/CORAL.md)。没有注册或部署真实 CORAL 模型，没有消耗 GPU。

## TDD 证据

- 初始公开 UI 测试因没有 CORAL 目录入口失败，再实现面板、运行、进度、结果与会话交接。
- PostgreSQL 公开 HTTP 历史测试初始返回 405；实现 GET 列表后通过。覆盖 Alice/Bob 隔离、精确能力过滤、读取顺序、范围校验和服务重建后历史仍在。
- 历史恢复输入最初为空；补充恢复 job.arguments，并验证之后修改表单不会篡改原任务分析上下文。
- 大数据下载测试最初触发两次下载；实现合并下载后只导出一个完整 FASTA，上传仍遵守单文件限制拆成两块。
- 非 CORAL 能力任务链接的负面测试促成能力检查，避免把其他任务当作 CORAL 显示或持续轮询。
- UI 另覆盖服务未连接、原服务 100 条上限、非法 PDB、排队取消、部分结果提示，以及第二块上传/消息提交失败后的重试。已确认上传失败不提前创建空 Session，成功上传块不重复传，同一已创建 Session 和消息 key 被复用。

## 自动验证

```bash
cd /home/jhli/pskit-2.0/new_frontend
npm test -- --maxWorkers=2
npm run typecheck
npm run lint
npm run build

cd /home/jhli/pskit-2.0/new_backend
PYTHONPATH=..:. .venv/bin/pytest tests -q
TEST_POSTGRES_DSN=postgresql://postgres:pskit-test-only@127.0.0.1:15433/postgres \
  PYTHONPATH=..:. .venv/bin/pytest \
  tests/postgres/test_compute_jobs.py tests/postgres/test_compute_claims.py \
  tests/postgres/test_compute_ledger.py tests/postgres/test_compute_end_to_end.py -q
```

上述 DSN 是既有本机临时测试数据库，不是生产凭据。PostgreSQL 测试创建并清理随机隔离 schema。

- 后端最终全量：440 passed、196 skipped；未提供数据库的 PostgreSQL 用例按既有规则跳过，相关用例另接真实本机 PostgreSQL 验证。
- PostgreSQL 计算链路：19 passed。
- CORAL 公开 UI：9 passed（2 个文件）。
- 前端最终全量：229 passed（37 个文件）。
- Typecheck、Lint、Build 均以最终代码通过。
- 后端改动 Ruff 通过。

首次后端全量中既有 Pi 重试测试出现消息顺序倒置；单独复现通过，随后最终全量通过。本次未声称修复该非确定性问题，也未改动 Pi 的消息排序。一次受限环境中的前端全量命令收到 SIGTERM，重新在本地执行后完整通过；没有以中断结果当作通过。构建保留已有大包体提示，后端保留 Starlette TestClient 弃用提示。

## 浏览器交互

Chromium 对 1360×950 和 390×950、明暗主题分别验收，计算和模型 HTTP 使用外部接口替身。四种组合均通过：

- 卡片实测 100×80；详情面板没有横向溢出，输入聚焦无白色 outline/shadow。
- queued → running（真实响应的 63%）→ completed；界面只绘制前五条。
- 手机完成后折叠参数，点击编辑正确恢复输入并聚焦；刷新 owned 任务再次折叠。桌面预览可通过 End 键滚动。
- 一个完整 FASTA 实际下载包含 10000 个序列记录；新会话上传两个文件，大小为 899910 和 268984 字节。
- “分析序列”创建新 Session 后进入规范对话地址，完整数据作为两个附件；提示词约 1.4 KB，含原蛋白质输入、任务/版本和结果摘要。
- 没有浏览器 pageerror。精确尺寸及文件大小保存在 [checks.json](assets/coral-2026-10-05/checks.json)。重复序列仅为验证体积/传递的合成测试数据，不代表模型效果。

## 独立 UI 审阅

按用户要求，将真实浏览器截图交给独立 `coral_ui_judge` Agent，以科研用户可读性、任务可发现性、明暗主题/手机、简约规范为依据审阅。

第一轮发现下载在首屏外、手机完成态参数占用过多空间、预览截断缺少提示、再次生成过于醒目、序列正文偏小、菜单交接语义不清。

整改：下载与 Agent 操作移到数字摘要旁；手机折叠完成参数；桌面限定预览滚动，手机使用详情滚动；重新生成改次级样式；提高序列/标签字号、缩短帮助文案；菜单触控项 44 px 并说明新对话交接。

第二轮确认核心可发现性问题解决，要求补充滚动说明。增加“预览前 5 条 · 滚动查看”和键盘聚焦后，第三轮实际查看明暗桌面截图，报告没有剩余必须改的视觉问题。视觉评审不等同于真实模型/GPU 联调。

### 最终截图（合成接口数据）

| 桌面 | 手机 |
| --- | --- |
| ![暗色桌面](assets/coral-2026-10-05/dark-desktop.png) | ![暗色手机](assets/coral-2026-10-05/dark-mobile.png) |
| ![亮色桌面](assets/coral-2026-10-05/light-desktop.png) | ![亮色手机](assets/coral-2026-10-05/light-mobile.png) |

## 发布状态

仅在当前工作区实现与验证；未发布云端、未改生产配置。真实运行需要维护者提供 CORAL executor 和可信用量报告，并审核发布 `coral.generate_rna`。未连接时运行按钮禁用。
