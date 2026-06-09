# Output Artifacts Reference / 输出文件参考

## Principles / 原则
PSKit writes artifacts under task result directories or Agent session directories. Text files can be summarized with `read_result_file`; binary files should be downloaded or viewed with the appropriate UI.

## Structures / 结构文件
`download_pdb_file` saves `<pdb_id>.cif` or `<pdb_id>.pdb`. Prefer CIF unless PDB is required.

## Split and fragment outputs / 拆分与片段
`split_pdb_by_chain` and `split_complex` write output files into an output directory. `extract_fragment` typically writes `<input_stem>_chain_<chain>.pdb`.

## Contact-map JSON / 接触图 JSON
`calculate_contact_map` commonly writes `contact_map_<input_stem>.json` with `axis` residue identifiers and `values` contact-map values.

## Binding outputs / 结合输出
`annotate_binding_pairs` writes `<input_stem>_binding_pairs.csv` with `pair` and `distance`. `predict_binding_sites` outputs residue-level predictions with columns such as `chain`, `residue_number`, `residue_name`, and `score`.

## Feature and embedding outputs / 特征与嵌入输出
`extract_empirical_features` can produce `.dssp`, Rosetta score files, relaxed PDB structures, logs, or error files. `lm_embed` can produce `*_esm2.npy` and `*_saprot.npy`; these are binary NumPy arrays.

## AlphaFold 3 outputs / AF3 输出
AF3 outputs can include `af3_input.json`, `af3_command.txt`, `af3_stdout.log`, `af3_stderr.log`, `af3_outputs_manifest.json`, generated structure files, confidence files, and `error.json`.

## Safe result reading / 安全读取
`read_result_file` only reads regular UTF-8 text files under Agent session artifacts. Do not use it for arbitrary paths, secrets, binary files, images, model weights, or guessed locations.
