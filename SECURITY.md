# Security Policy

## Supported versions

The `main` branch is supported. Older branches and tags are not.

## Reporting

Email **security@rodgersintelligence.com** with:

- affected path / command
- impact
- proof of concept (non-destructive)

Do **not** open a public issue for secrets or exploitable bugs.

## Secrets

- Never commit `.env`, cookies, tokens, private keys, or customer data.
- The heartbeat runs with the workflow's own `GITHUB_TOKEN`; never log or print it.
- Rotate any credential that may have been exposed.
