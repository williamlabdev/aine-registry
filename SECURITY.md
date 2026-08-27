# Security

Please do not report security-sensitive details in public issues. Contact the repository owner privately through GitHub.

The registry is designed to avoid exporting absolute local filesystem paths and credentials. New discovery behavior must preserve that boundary.

## Repository security gates

The repository configures Gitleaks and OSV-Scanner in GitHub Actions. Run the
same checks locally from the repository root after installing the tools:

```bash
brew install gitleaks osv-scanner
./tools/security_scan.sh
```

OSV-Scanner accepts the absence of lockfiles explicitly because the Registry
core declares no third-party runtime dependencies. A future dependency or
lockfile must be reviewed and will be included by the recursive scan.
