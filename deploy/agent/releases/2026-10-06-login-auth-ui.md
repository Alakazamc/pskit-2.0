# 2026-10-06 登录页与验证码前端发布

## 范围

- 发布提交：`1d68953a5ca89355828aae8dbdbf6e6f3c3f20f4`
- 仅更新阿里云 Nginx 提供的 React `dist`。
- 登录页背景换为 3966×1586 的日间/夜间资源。
- Turnstile 固定为可见模式；其故障回调不再生成表单红色业务错误。
- Python、Pi、Supabase、PostgreSQL、LiteLLM、AF3 回调代理和 A6000 接收器均未重建。

## 制品

| 项目 | 值 |
| --- | --- |
| 构建参数 | `VITE_AUTH_MODE=supabase VITE_API_BASE_URL=/api/v1 VITE_TURNSTILE_SITE_KEY=<生产公开站点公钥>` |
| 前端 dist 内容哈希 | `121a130502515f2b4dd94f800436c8cee25920c843a1d3ae2acc14f85cc52c0c` |
| 传输归档 SHA256 | `e2556b62ef6629ce5520386befb91e8d2593ba1b6472b22778da9146aa2d3c34` |
| 前端源码归档 SHA256 | `86bdbbf541a2026c51bcc74cba6e2861aa6be662de33002c2646619a551b7641` |
| 生产入口 JS | `/assets/index-D09GZt3d.js` |
| 生产入口 CSS | `/assets/index-B9XHvA_1.css` |

阿里云版本目录：`/home/ecs-user/pskit-agent-releases/20261006-login-auth-1d68953`。版本目录权限为 0700，归档权限为 0600。

## 验收

- 前端验证码与登录页定向测试：14 个通过；TypeScript、ESLint 和生产构建通过。
- Staging 复用独立账号、密钥和卷；登录、文件上传、SSE、Token/GPU 配额和模拟 AF3 烟测通过。
- Staging 和生产均逐一比对本次 dist 的 387 个文件。
- 生产后端 readiness 为 200；`/login` 为 200；未登录 `/api/v1/usage` 为 401；`/internal/` 为 404；公网 `/admin/models` 为 403。
- 公网返回的入口 HTML、JS 和 CSS 与服务器制品一致，生产 Turnstile 公开站点公钥存在于入口 JS。
- 生产后端保持 `pskit-agent-backend:20261006-csrf-cdef1fe`，镜像 ID `sha256:bc8d0d320e6a8a0e336de6b3fe451f45fb1d4b4dc6054edf75723ab9c7de9a04`，容器未因本次前端发布重建。

## 部署源码漂移

首次 Staging 预检发现远端 `staging_preflight.py` 仍使用旧的 Compose 变量白名单，不能识别此前已加入 Staging 私有配置的 `TURNSTILE_SITE_KEY`。当前脚本的 28 个相关回归测试通过后，已原子同步该公开脚本。旧脚本保存在：

`/home/ecs-user/pskit-agent-releases/20261006-login-auth-1d68953/rollback/staging_preflight.py`

## 回滚

发布前完整生产静态目录保存在：

`/home/ecs-user/pskit-agent-releases/20261006-login-auth-1d68953/rollback/production-dist-before.tar.gz`

发布前静态目录内容哈希为 `25ef658007d5e07372209ea4d3c2b874d15523041ed36d36ef140a55e4a1ab5b`。若只回滚本次前端，在阿里云停止新的静态切换操作后执行：

```bash
tar -C /var/www -xzf \
  /home/ecs-user/pskit-agent-releases/20261006-login-auth-1d68953/rollback/production-dist-before.tar.gz
find /var/www/agent.bioailab.net -type d -exec chmod 0755 {} +
find /var/www/agent.bioailab.net -type f -exec chmod 0644 {} +
curl --noproxy '*' --fail --head https://agent.bioailab.net/login
```

该回滚不修改数据库、后端镜像或 Nginx 配置，也不需要 reload Nginx。新哈希资源可以保留；旧 `index.html` 恢复后不会引用它们。
