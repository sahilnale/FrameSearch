# Local development credential remediation

The GitGuardian incident linked to commit `8ccf944`,
`infra/docker-compose.yml` line 209, flagged the PostgreSQL development password
fallback inside the integration-test database URL. That default was used by the
local Docker stacks, so it required rotation even though it was a demo value.

Remediation:

- Remove PostgreSQL and MinIO secret defaults from Compose and `.env.example`.
- Require private environment values for every Compose client, including tests.
- Generate independent random credentials using `make setup-env`; create `.env`
  with mode 0600 and never overwrite an existing environment automatically.
- Rotate the database role password and MinIO root password on the existing
  `framesearch` and `framesearch-integration` stacks, recreate their clients,
  and retain database, object storage, and model-cache volumes.
- Keep private environment files ignored by Git. Do not attach their contents,
  rendered Compose configuration, or container environment dumps to issues.

For existing installations, initialization environment variables do not change
an existing PostgreSQL role password: rotate the role in PostgreSQL before
restarting clients with the new private configuration. MinIO root credential
changes take effect when its container is recreated. Old signed URLs may fail;
request fresh URLs from the API.

The historical commits still contain the revoked development defaults. Git
history was not rewritten: that is a separate coordinated operation affecting
other developers' branches and clones. Rotation invalidates the exposed values
on the two known local stacks. No hosted deployment was configured in this work;
other people's installations cannot be rotated from this workstation.

GitGuardian's incident status must be updated in the incident dashboard after
reviewing this evidence. This change does not close the incident automatically.
Reference: https://blog.gitguardian.com/leaking-secrets-on-github-what-to-do/

Validation performed on this workstation:

- Both stacks rejected the old PostgreSQL password from a separate network client
  and rejected the old MinIO credentials using the real MinIO client.
- The full application returned readiness 200 after client recreation.
- Backend unit/database/storage/Kafka integration tests passed with new credentials.
- Real-video smoke passed upload, indexing, search, private thumbnails, signed
  playback bytes, range 206, and 900-second URL expiration.
- Environment generation passed random-value, URL consistency, permission 0600,
  existing-file preservation, and Git-ignore checks. Compose accepted the private
  environment and rejected the blank example. Formatting checks passed.
