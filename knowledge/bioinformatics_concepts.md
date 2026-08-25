# Bioinformatics Concepts / 生物信息概念

## PDB and mmCIF / PDB 与 mmCIF
PDB and mmCIF are molecular structure formats. mmCIF is preferred for modern PDB entries; PDB remains widely used and is sometimes required by older tools.

## Chain and residue / 链与残基
A chain is a polymer entity such as protein, DNA, or RNA. A residue is an amino acid or nucleotide. Residue numbering comes from the input structure and may have gaps or insertion codes.

## Protein-nucleic-acid complex / 蛋白核酸复合物
A protein-nucleic-acid complex contains protein and DNA/RNA chains. PSKit uses these complexes for binding-pair annotation, binding-site analysis, split-complex workflows, and contact maps.

## Binding pair and binding site / 结合对与结合位点
A binding pair is a close residue pair under a distance cutoff. A binding site is a residue or region likely to interact with DNA/RNA. Annotation is structure geometry; prediction is model inference.

## Contact map / 接触图
A contact map records which residues are spatially close. It can be distance-threshold based or KNN-based depending on tool support.

## DSSP, Rosetta, Foldseek / 结构工具
DSSP assigns secondary structure. Rosetta scores and relaxes structures. Foldseek derives structure-aware representations and supports SaProt workflows.

## ESM-2, SaProt, RNA-FM / 基础模型
ESM-2 is a protein language model. SaProt is structure-aware for proteins. RNA-FM is an RNA foundation model.

## PAIR, INABe, AF3 / 任务模型
PAIR predicts protein-nucleic-acid sequence interaction. INABe-style models predict DNA/RNA binding residues. AlphaFold 3 predicts biomolecular structures from sequence/entity inputs and is GPU/Docker-heavy in PSKit.

## Computational caveat / 计算预测说明
PAIR, INABe, ESM-2, SaProt, RNA-FM, and AF3 outputs are computational results for exploration and hypothesis generation, not experimental proof.
