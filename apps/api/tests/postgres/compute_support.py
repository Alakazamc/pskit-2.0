"""Real database accounting: concurrent admission, cumulative usage and legacy quotas."""


import pytest

from app.contracts.compute import (
    CapabilityVersion,
    ComputeBudget,
    ComputeJobRequest,
    ComputeServiceManifest,
)
from app.db.postgres import PostgresDatabase
from app.db.postgres_migrations import migrate_postgres
from app.domain.compute.catalog import ComputeCatalog
from app.domain.compute.jobs import ComputeJobs
from app.domain.compute.ledger import ComputeLedger


@pytest.fixture
def ledger_system(pg_schema):
    dsn, schema = pg_schema
    migrate_postgres(dsn, schema=schema)
    database = PostgresDatabase(dsn, schema=schema)
    ledger = ComputeLedger(database, cpu_daily_limit_ms=60000, gpu_daily_limit_ms=60000)
    catalog = ComputeCatalog(database)
    catalog.register(ComputeServiceManifest(service_id="gpu", model_version="v1", capabilities=[
        CapabilityVersion(id="gpu.predict", version="1", visibility="published", gpu_count=1,
                          required_usage=["gpu_device_ms"], input_schema={"type": "object"},
                          max_budget=ComputeBudget(gpu_device_ms=60000)),
    ]))
    jobs = ComputeJobs(database, ledger)
    try:
        yield database, ledger, jobs
    finally:
        database.close()


def request():
    return ComputeJobRequest(capability_id="gpu.predict", version="1", arguments={},
                             budget=ComputeBudget(gpu_device_ms=40000))


