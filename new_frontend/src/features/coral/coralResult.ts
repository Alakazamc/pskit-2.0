import type { ComputeCapability, ComputeJob } from "../../api/types";

export const coralCapabilityId = "coral.generate_rna";
export const coralActive = (status?: ComputeJob["status"]) => status === "queued" || status === "running" || status === "cancelling";
type Property = { minimum?: number; maximum?: number };
export function coralBounds(capability: ComputeCapability | undefined, field: string) {
  const properties = capability?.input_schema.properties as Record<string, Property> | undefined;
  return { min: properties?.[field]?.minimum ?? 1, max: properties?.[field]?.maximum };
}

export function coralResult(job: ComputeJob) {
  if (job.report?.status !== "completed") return null;
  const data = job.report.result;
  const raw = data.candidates ?? data.sequences ?? data.generated_rnas;
  const sequences = Array.isArray(raw) ? raw.flatMap((item: unknown, index) => {
    const object = item && typeof item === "object" ? item as Record<string, unknown> : {};
    const sequence = typeof item === "string" ? item : object.sequence ?? object.rna_sequence;
    if (typeof sequence !== "string") return [];
    const clean = sequence.replace(/\s/g, "").toUpperCase();
    if (!clean || !/^[ACGURYSWKMBDHVN]+$/.test(clean)) return [];
    return [{ id: typeof object.id === "string" ? object.id : `RNA ${index + 1}`, sequence: clean }];
  }) : [];
  const declared = data.sequence_count ?? data.total_count ?? data.candidate_count;
  const returnedCount = Array.isArray(raw) ? raw.length : 0;
  const count = typeof declared === "number" && Number.isSafeInteger(declared) && declared >= 0
    ? Math.max(declared, returnedCount) : returnedCount;
  const reportedLength = data.sequence_length ?? data.length;
  const limits = sequences.reduce<{ min: number | null; max: number | null }>((previous, item) => ({
    min: previous.min === null ? item.sequence.length : Math.min(previous.min, item.sequence.length),
    max: previous.max === null ? item.sequence.length : Math.max(previous.max, item.sequence.length),
  }), { min: null, max: null });
  const reported = typeof reportedLength === "number" && Number.isSafeInteger(reportedLength) && reportedLength > 0 ? reportedLength : null;
  const min = count !== sequences.length && reported !== null ? reported : limits.min ?? reported;
  const max = count !== sequences.length && reported !== null ? reported : limits.max ?? reported;
  return { sequences, count, min, max, complete: count > 0 && count === sequences.length,
    artifacts: job.report.artifacts ?? [], usage: job.report.usage };
}

/** Keep full data in owned workspace files, not the model's prompt. */
export function coralDatasetFiles(job: ComputeJob): File[] {
  const result = coralResult(job);
  if (!result?.complete) return [];
  const name = job.id.replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 80);
  const files: File[] = [];
  let chunk = "";
  const flush = () => {
    if (chunk) files.push(new File([chunk], `coral-${name}-${files.length + 1}.fasta`, { type: "text/plain" }));
    chunk = "";
  };
  for (const [index, item] of result.sequences.entries()) {
    const record = `>candidate_${index + 1}\n${item.sequence}\n`;
    if (record.length > 900_000) throw new Error("DATASET_RECORD_TOO_LARGE");
    if (chunk.length + record.length > 900_000) flush();
    chunk += record;
  }
  flush();
  if (files.length > 10) throw new Error("DATASET_ATTACHMENT_LIMIT");
  return files;
}
