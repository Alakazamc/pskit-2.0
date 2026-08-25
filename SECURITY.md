# Security Policy

## Supported Version

The `main` branch is the active PSKit 2.0 development branch.

## Secrets

Do not commit:

- `.env` files
- API keys
- model weights
- AlphaFold3 parameters
- SQLite databases
- task outputs
- generated artifacts

Use `.env.example` as a template and keep real deployment secrets outside Git.

## Auth And Data Isolation

PSKit 2.0 uses server-side sessions with HttpOnly cookies. The backend is the source of truth for user ownership. Agent sessions, tasks, reports, and artifact downloads must always be checked against the authenticated user.

Any change touching file downloads, `read_result_file`, task artifacts, or session history must preserve ownership checks.

## Reporting Issues

For private deployments, report security issues to the repository maintainer directly. For public GitHub use, open a private security advisory if the repository has advisories enabled.
