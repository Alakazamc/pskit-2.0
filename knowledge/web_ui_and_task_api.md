# Web UI and Task API / 网页与任务 API

## Web UI overview / 网页总览
The Rust webserver serves the built Vue frontend from `webpage/dist`. Some pages run browser/WASM tools; others submit backend tasks to `/api/tasks`.

## Routes / 路由
`/` is Home. `/agent` is Agent chat. `/binding/nbsa` annotates binding sites by structure contact. `/binding/nbsp` predicts binding sites with task `pred_nbs`. `/binding/pnip` predicts sequence interaction with task `pred_pni`. `/features/structural` launches `emp_feats`. `/features/language-model` launches `lm_embed`. `/features/alphafold3` launches `af3_predict`. `/tools/split` splits structures. `/tools/extract` extracts fragments. `/contact-map` builds contact maps. `/viewer` visualizes structures. `/about/guide` and `/about/technical` provide docs.

## Task API / 任务 API
Important routes are `POST /api/tasks`, `GET /api/tasks/{task_id}`, `GET /api/tasks/{task_id}/results`, and `GET /api/tasks/{task_id}/results/{filename}`.

## Task lifecycle / 任务生命周期
Task statuses include `Pending`, `Processing`, `Completed`, and `Failed`. Pending responses may include queue position. Results are written under task result directories and exposed through list/download APIs.

## Task storage / 任务存储
Typical storage is `tasks/uploads/<task_id>`, `tasks/uploads/<task_id>/form_data.json`, `tasks/results/<task_id>`, and SQLite `tasks/tasks.db`.

## Task types / 任务类型
`pred_nbs` predicts nucleic-acid binding sites. `pred_pni` predicts protein-nucleic-acid interaction. `emp_feats` extracts empirical features. `lm_embed` generates ESM-2/SaProt embeddings. `af3_predict` launches AlphaFold 3.

## Limits / 限制
The upload body limit is 250 MB. Worker concurrency is controlled by `pskit-webserver <work_dir> <address> <max_workers>`. The queue is in-memory, so pending queue state may not survive a server restart.
