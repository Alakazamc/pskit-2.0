ALTER TABLE compute_job_data
    ADD COLUMN execution_binding_json jsonb;

CREATE FUNCTION reject_compute_execution_binding_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.execution_binding_json IS DISTINCT FROM OLD.execution_binding_json THEN
        RAISE EXCEPTION 'IMMUTABLE_EXECUTION_BINDING';
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER compute_execution_binding_immutable
    BEFORE UPDATE OF execution_binding_json ON compute_job_data
    FOR EACH ROW EXECUTE FUNCTION reject_compute_execution_binding_change();
