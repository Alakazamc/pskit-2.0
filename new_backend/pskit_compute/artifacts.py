"""Bounded MCP artifact transport with no authority to request provider paths or URLs."""

import base64
import binascii
import hashlib

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, ValidationError

from app.domain.compute.artifacts import MAX_ARTIFACT_BYTES, ComputeArtifacts
from pskit_compute.service import ProtocolError

CHUNK_BYTES = 512 * 1024


class ArtifactChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_id: str
    offset: StrictInt
    size: StrictInt
    sha256: str
    data_base64: str
    eof: StrictBool


async def read_artifact_chunks(call, artifact, job_id):
    try:
        ComputeArtifacts.validate_metadata(artifact)
    except ValueError as exc:
        raise ProtocolError(str(exc)) from exc
    raw = bytearray()
    while True:
        response = await call({"job_id": job_id, "artifact_id": artifact.id,
                               "offset": len(raw), "length": CHUNK_BYTES})
        try:
            chunk = ArtifactChunk.model_validate(response)
            if len(chunk.data_base64) > 4 * ((CHUNK_BYTES + 2) // 3):
                raise ValueError("oversized encoded chunk")
            data = base64.b64decode(chunk.data_base64, validate=True)
            if (chunk.artifact_id != artifact.id or chunk.offset != len(raw)
                    or chunk.size != artifact.size or chunk.sha256 != artifact.sha256
                    or len(data) > CHUNK_BYTES or len(raw) + len(data) > MAX_ARTIFACT_BYTES
                    or len(raw) + len(data) > artifact.size
                    or chunk.eof != (len(raw) + len(data) == artifact.size)
                    or (not data and not chunk.eof)):
                raise ValueError("inconsistent chunk")
        except (ValidationError, ValueError, binascii.Error) as exc:
            raise ProtocolError("ARTIFACT_CHUNK_INVALID") from exc
        raw.extend(data)
        if chunk.eof:
            break
    if hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise ProtocolError("ARTIFACT_DIGEST_MISMATCH")
    return bytes(raw)
