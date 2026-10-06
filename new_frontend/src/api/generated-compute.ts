// Generated from contracts/compute.schema.json. Do not edit by hand.

export type ArtifactRef = {
  "id": string;
  "name": string;
  "kind": string;
  "available"?: boolean;
  "size"?: number | null;
  "sha256"?: string | null;
};

export type CapabilityVersion = {
  "id": string;
  "version": string;
  "input_schema": Record<string, unknown>;
  "output_schema"?: Record<string, unknown>;
  "required_usage"?: ("wall_ms" | "cpu_core_ms" | "gpu_device_ms" | "peak_memory_bytes" | "peak_gpu_memory_bytes" | "gpu_count")[];
  "accepted_sources"?: ("service_reported" | "measured" | "estimated" | "unknown")[];
  "visibility"?: "draft" | "published";
  "allowed_users"?: (string)[] | null;
  "gpu_count"?: number;
  "max_budget"?: ComputeBudget;
  "concurrency"?: number;
  "max_execution_seconds"?: number;
  "cancellation"?: "none" | "cooperative" | "confirmed_stop";
  "limit_mode"?: "soft" | "hard";
  "exclusive_process"?: boolean;
};

export type Completed = {
  "status"?: "completed";
  "result": Record<string, unknown>;
  "usage": UsageReport;
  "artifacts"?: (ArtifactRef)[];
  "job_id"?: string | null;
};

export type ComputeBudget = {
  "cpu_core_ms"?: number;
  "gpu_device_ms"?: number;
};

export type ComputeJob = {
  "id": string;
  "user_id": string;
  "service_id": string;
  "capability": CapabilityVersion;
  "arguments": Record<string, unknown>;
  "budget": ComputeBudget;
  "status": "queued" | "running" | "cancelling" | "completed" | "failed" | "cancelled";
  "accounting_status": "reserved" | "settled" | "released" | "pending_reconciliation";
  "progress"?: number;
  "run_id"?: string | null;
  "tool_call_id"?: string | null;
  "report"?: Completed | Pending | Failed | null;
};

export type ComputeServiceManifest = {
  "schema_version"?: "pskit.compute.v1";
  "service_id": string;
  "model_version": string;
  "capabilities": (CapabilityVersion)[];
};

export type ComputeUsage = {
  "day": string;
  "cpu": ResourceCounter;
  "gpu": ResourceCounter;
  "sources": (string)[];
  "allocation_policy"?: string;
};

export type ExecutionBindingSnapshot = {
  "adapter": "immediate_mcp" | "job_mcp" | "mcp_tasks";
  "endpoint_url": string;
  "credential_ref"?: string | null;
  "submit_tool": string;
  "status_tool"?: string | null;
  "cancel_tool"?: string | null;
  "remote_output_schema": Record<string, unknown>;
  "result_mapping": ExecutionResultMapping;
};

export type ExecutionError = {
  "code": string;
  "message": string;
};

export type ExecutionGrant = {
  "job": ComputeJob;
  "worker_id": string;
  "attempt": number;
  "fencing_token": string;
  "stop_at": string;
  "lease_expires_at": string;
  "gpu_uuids"?: (string)[];
  "execution_binding"?: ExecutionBindingSnapshot | null;
  "recovered"?: boolean;
};

export type ExecutionResultMapping = {
  "kind"?: "json_pointer";
  "pointer": string;
  "renames"?: Record<string, string>;
  "transforms"?: (Record<string, unknown>)[];
};

export type Failed = {
  "status"?: "failed";
  "error": ExecutionError;
  "usage": UsageReport;
  "artifacts"?: (ArtifactRef)[];
  "job_id"?: string | null;
};

export type Pending = {
  "status"?: "pending";
  "job_id": string;
};

export type ResourceCounter = {
  "limit": number;
  "used": number;
  "reserved": number;
  "remaining": number;
};

export type UsageReceipt = {
  "receipt_id": string;
  "job_id": string;
  "accepted_seq": number;
  "payload_hash": string;
  "status": "completed" | "failed" | "cancelled" | "pending";
  "committed"?: true;
};

export type UsageReport = {
  "wall_ms"?: number | null;
  "cpu_core_ms"?: number | null;
  "gpu_device_ms"?: number | null;
  "peak_memory_bytes"?: number | null;
  "peak_gpu_memory_bytes"?: number | null;
  "gpu_count"?: number | null;
  "source": "service_reported" | "measured" | "estimated" | "unknown";
};

export type ExecutionReport = Completed | Pending | Failed;
