# Windows Release Signing

Status: Not configured (required before 1.0).

Delta release provenance and SHA-256 verification establish where the portable ZIP
was built and whether its bytes changed. They do not establish a Windows Publisher
identity. Authenticode signing must therefore use a real certificate issued to the
release owner; never use a self-signed certificate for a public Delta release.

## Required repository secrets

- `WINDOWS_SIGNING_CERT` — base64-encoded PFX certificate.
- `WINDOWS_SIGNING_PASSWORD` — password for that PFX.

When a real certificate is available, add a Windows release step after the portable
executables are built and before the final ZIP is created. Decode the PFX only into
`$RUNNER_TEMP`, invoke the Windows SDK `signtool sign` with SHA-256 and a trusted
timestamp URL, verify with `signtool verify /pa /all`, then remove the temporary PFX.
The ZIP and its `.sha256` must be regenerated after signing.

Until that implementation is enabled and backed by a real certificate, signing status
for releases is **Not configured**. The current release workflow deliberately does
not expose these secrets or claim a trusted Publisher.
