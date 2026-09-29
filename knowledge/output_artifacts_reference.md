# Output Artifacts Reference / 输出文件参考

PSKit registers outputs in the database and stores local files below the
configured artifact directory. Every artifact belongs to one user and may also
reference an Agent session or task. Do not construct or guess server paths.

Structure downloads produce PDB or mmCIF artifacts. Structure splitting and
fragment extraction produce new structure artifacts. Contact maps produce JSON;
binding-pair annotation produces CSV. Prediction and AlphaFold 3 tasks can
produce JSON, logs, tables, structures, confidence files, or error reports.

List task outputs with `GET /api/tasks/{task_id}/files` and download one with
`GET /api/files/{artifact_id}/download`. `read_result_file` previews only a
bounded amount of an owned regular UTF-8 artifact. Use download or a suitable
viewer for binary arrays, images, archives, and model files.
