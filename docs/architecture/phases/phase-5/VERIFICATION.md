# Phase 5 验证

前端最终检查 **53文件387测试通过**，typecheck/lint/build及Molstar分包门禁通过。独立审查增加了 equal timestamp顺序、多设备gap、并发cursor/anchor及切换会话loading的7项回归；没有删除既有断言。Molstar18个chunks，最大436,643 bytes、无initial import；已有大chunk warning保留。

后端分页14测试通过，其中专用PG17用例1项零skip；105条同created_at消息分页、owner/归档/非法游标/limit/旧列表105条均覆盖。已有索引为session+ordinal，本轮没有执行真实索引DDL；created_at+ordinal的服务端排序成本仍需大数据EXPLAIN验证，不能将有界客户端物化宣传为索引效率已验收。

## 实际浏览器

已安装Chromium，隔离源码/mock API、120条合成消息，浏览器网络仅允许本机loopback。模型目录为本地合成有标签选项；没有真实Supabase或付费模型。8组：1440×900和390×844，各light/dark及en/zh。

最终387项测试对应源码重新运行8组，全部通过；验证50→100→120分页、无横向overflow、模型Popover Enter打开/Escape关闭并返回focus、草稿保留、设置checkbox渲染、无pageerror。[矩阵JSON](screenshots/results.json)与16张聊天/设置截图在screenshots目录。

浏览器尚未覆盖真实WebGL分子结构、Supabase登录、真实上传速率或线上模型；这些不能用本地截图或Vitest替代。现有Artifact/Molstar单测与build guard仍为本轮防回归证据。截图证明视觉/布局范围，不声称已完成完整WCAG对比度审计。

## 最终 canonical gate

`python scripts/ci_new_stack.py frontend --timeout 300 --report-dir /tmp/pskit-remediation-final-frontend` 在独立快照运行typecheck/lint/test/build，53文件 **387 passed / 50.77s**，Molstar guard通过。既有mock测试的SSE关闭/重复fixture key警告与大chunk warning未隐藏。8组实际浏览器最终重跑与截图对应同一源码；未连接真实服务。

112个既有OpenAPI paths与170个schema结构比较通过：路径/方法/原响应码及属性保留，既有DTO未增加required字段。旧接口语义仍由回归覆盖；结构比较不代替实际客户或项目分页HTTP的外部验收。
