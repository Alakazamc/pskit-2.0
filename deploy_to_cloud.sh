#!/bin/bash
# Retired for every argument, including help and rollback. Use shell builtins only.
printf '%s\n' \
  'Deprecated: deploy_to_cloud.sh no longer performs deployments or rollbacks.' \
  'Use the application-only release procedure in docs/runbooks/DEPLOYMENT.md.' \
  'Existing-environment details: skills/pskit-cloud-deploy/references/runbook.md.' >&2
exit 2
