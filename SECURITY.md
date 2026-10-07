# Security policy

## Supported versions

Only the latest minor release gets security fixes. Update to the latest version before you report a problem.

## Reporting a vulnerability

Do not open a public GitHub issue for a security problem.

Report it through GitHub's private vulnerability reporting instead:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability**.
3. Fill in the form.

Please include:

- The version of Deckwright you tested (or the image tag, for the Docker service).
- Steps to reproduce the problem.
- What you expected to happen, and what happened instead.
- Why you believe this is a security issue, not a normal bug.

We aim to acknowledge a new report within 5 working days.

## Security model

The Docker service is built to be secure by default: auth is required in remote mode unless an admin sets an explicit insecure flag, tokens and secrets are hashed at rest, template admin tools are not exposed remotely, and download links are signed and time-limited.

See [docs/plan-docker.md#security-baseline](docs/plan-docker.md#security-baseline) for the full security baseline this service meets.
