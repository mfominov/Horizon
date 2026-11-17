# Container publishing

The fork publishes its runtime image to GitHub Container Registry after every
successful CI run on the default branch and for version tags matching `v*`.

## Image

The default image name is:

```text
ghcr.io/mfominov/horizon
```

Repository variable `GHCR_IMAGE` may override the name, but it must remain in
the `ghcr.io` registry. No registry password or personal access token is needed:
the workflow authenticates with its short-lived `GITHUB_TOKEN` and requests only
`packages: write` for the publishing job.

## Tags

- `latest` — successful default-branch build;
- `sha-<commit>` — immutable commit-oriented tag;
- `vX.Y.Z`, `X.Y.Z`, and `X.Y` — version-tag builds.

The workflow records the OCI source labels, emits an SBOM and BuildKit
provenance, and publishes a GitHub artifact attestation for the image digest.
Pulling by digest is preferred for deployment.

## Safety boundary

Pull requests and feature-branch pushes build and scan the image but never
publish it. Publishing does not dispatch or modify `devhint-tools`, and it does
not deploy containers.

The first GHCR publication is private by default. After the first successful
default-branch publication, review the package metadata and deliberately choose
whether it should remain private or become public before configuring a server
pull. A private package requires authenticated pull access on the target host.
