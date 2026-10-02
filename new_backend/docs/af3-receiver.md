# AF3 常驻任务接收器

`new_backend` 的 SQLite `agent_jobs` 是持久任务队列，不需要额外 MQ。`scripts/af3_receiver.py` 只依赖 Python 标准库，适合放进已固定版本的 AF3 镜像。它分为两个常驻进程，部署在两个容器中，共享一个持久卷：

- **receiver**：通过后端 HTTP 领取任务、续报进度、上传产物及回传结果；领取与上传状态写入共享卷中的 SQLite journal。
- **compute-loop**：监视共享卷内的输入文件，直接调用镜像内 `/app/alphafold/run_alphafold.py`。每个任务在同一容器内运行，不再为每次预测调用 `docker run`。

接收器容器重启不会终止计算容器中的 AF3 进程。网络断开时，计算继续，结果及产物留在本地；后端明确确认终态结果后，接收器才删除 journal 记录和该任务目录。领取响应丢失后，接收器可通过 `GET /internal/compute/af3/jobs/owned` 找回任务。后端不会仅因心跳过期就重新分派仍在运行的任务，以免重复消耗 GPU。相同进度、产物和结果回报可重试。

## 当前连接

- 镜像：`af3_mar5_jhli_2026_0923:v1`，ID `sha256:5b5b00bad590...`；没有使用 `latest`。
- A6000 容器：`pskit-af3-receiver-cloud-20261002`、`pskit-af3-compute-real-test-20261002`。旧接收器 `pskit-af3-receiver-real-test-20261002` 已停，旧 PSKit/AF3 容器未修改。计算容器固定使用主机 GPU 3，容器内设备索引 0，最多 5 个 diffusion samples。
- 持久目录：`/data/jhli/pskit-af3-receiver-test-20261002/spool`。计算容器只读挂载 `/home/public/database/alphafold3` 到 `/data/af3/database`，`/data/hzeng/af3/model-parameters` 到 `/data/af3/models`；进程以 UID 1006 运行。
- 当前接收器从 A6000 的 WireGuard 地址 `10.9.8.2` 主动访问阿里云 `10.9.8.1:18184`。阿里云 Nginx 仅允许 A6000，并转发到只暴露在云端回环地址的 AF3 回调代理；代理只开放指定 worker 的 AF3 路由并校验回调密钥。新密钥放在 A6000 的 `receiver.cloud.env`（`0600`），不进入 Git。带密钥的 owned-jobs 只读请求已从 A6000 验证通过；云端测试账号的 GPU 额度仍为 0，尚未提交真实任务。云端部署记录见 [`../../deploy/agent/OPERATIONS.md`](../../deploy/agent/OPERATIONS.md)。
- 原 WSL `127.0.0.1:18080` 后端、`127.0.0.1:18084` 回调代理和 SSH 反向隧道的用户级服务定义仍保留在 `deploy/systemd/`，供本地联调使用；旧接收器停用后不再承接当前任务。计算容器和新接收器均设为 Docker `restart=unless-stopped`。断网期间本地 spool 会保留未确认结果。

隔离数据库 `/tmp/pskit-af3-real-integration.sqlite3` 中的真实 AF3 小任务 `d7839662-29d9-4230-b534-c778b26bbd0e` 已完成：`simulation=false`、7 个产物、可下载的 CIF 为 12774 字节、GPU 结算 1 分钟；后端确认后远端任务目录清除。烟测使用 1 个 diffusion sample，常驻计算容器当前配置为 5 个。该任务验证了领取、真实镜像调用、进度、产物和用量回调。**现有 live 用户账号尚未提交过 AF3 任务**，Pi 在后台任务结束后的自动分析唤醒也尚未做真实模型端到端验证。

可只读检查：

```bash
docker ps --filter name=pskit-af3- --format '{{.Names}} {{.Status}}'
docker logs --tail 20 pskit-af3-receiver-cloud-20261002
docker logs --tail 20 pskit-af3-compute-real-test-20261002
```

在 WSL 查看服务：

```bash
systemctl --user status pskit-new-backend pskit-af3-callback-proxy pskit-af3-ssh-tunnel
curl http://127.0.0.1:18080/health/ready
```

AF3 输入显式提供蛋白质 `unpairedMsa`、`pairedMsa`、`templates` 时跳过数据搜索；否则运行镜像内的数据管线。实际科研任务的输入、GPU 时长和结果质量仍需按具体模型任务核验。单个产物必须小于 20 MiB。

**故障边界：** receiver 容器故障不会中断 compute 容器中的运行任务；compute 容器或主机故障会终止正在运行的 AF3，恢复后可依据本地输入重新执行。AF3 自身的中间检查点恢复尚未实现。用户取消任务时，目前后端会停止接收结果并保留 GPU 费用待对账，计算进程不会立即被终止；正式使用前需要补齐取消信号和进程终止流程。超过 `RESEARCH_AGENT_AF3_EXECUTION_TIMEOUT_SECONDS` 的任务会失败并进入费用对账，不会无限占据队列。

**容量边界：** 当前每个 receiver 同时领取一个任务。多个 receiver 若分别声明同一块 GPU，后端尚无跨 worker 的 GPU 设备互斥；正式多 GPU 部署需固定每个 worker 对应的设备并由统一调度器管理资源。SQLite BLOB 产物上限为单件 20 MiB，大文件需要对象存储适配。
