"""Adapt configured HTTP endpoints to business reports without timing remote GPUs."""

from urllib.parse import quote

from pydantic import TypeAdapter, ValidationError

from app.contracts.compute import ExecutionReport
from app.domain.compute.metering import validate_report
from pskit_compute.service import ProtocolError


def parse_report(data, capability):
    try:
        report = TypeAdapter(ExecutionReport).validate_python(data)
        validate_report(report, capability)
        return report
    except (ValueError, ValidationError) as exc:
        raise ProtocolError(str(exc)) from exc


class HttpAdapter:
    def __init__(self, client, *, submit_url, status_url=None, cancel_url=None, transform=lambda data: data):
        self.client, self.submit_url = client, submit_url
        self.status_url, self.cancel_url = status_url, cancel_url
        self.transform = transform

    async def execute(self, grant, *, context=None):
        response = await self.client.post(self.submit_url, json=grant.job.arguments,
                                         headers={"Idempotency-Key": grant.job.id})
        response.raise_for_status()
        return parse_report(self.transform(response.json()), grant.job.capability)

    async def poll(self, grant, pending):
        if not self.status_url:
            raise ProtocolError("STATUS_ENDPOINT_REQUIRED")
        response = await self.client.get(self.status_url.format(job_id=quote(pending.job_id, safe="")))
        response.raise_for_status()
        report = parse_report(self.transform(response.json()), grant.job.capability)
        if report.job_id != pending.job_id:
            raise ProtocolError("EXTERNAL_JOB_CONFLICT")
        return report

    async def cancel(self, grant, pending):
        if not self.cancel_url:
            return False
        response = await self.client.post(self.cancel_url.format(job_id=quote(pending.job_id, safe="")))
        response.raise_for_status()
        # A cancel ACK is only an intent; poll must subsequently return stopped terminal usage.
        return True
