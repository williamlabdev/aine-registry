# Contributing

Please keep the core local-first, read-only, deterministic, and portable.

Contributions should include tests for new relationship inference or snapshot behavior. Do not add customer data, private workspace paths, credentials, or portfolio-specific assumptions to the public core.

## Verification

Run the repository-local verification gates before opening a change for review:

```bash
python3 tools/verify.py
```

The command runs the unit tests, bytecode compilation, schema parsing, CLI
version/help smoke checks, and the public polyrepo discover/validate/impact/
preflight fixture. Python bytecode and fixture data are written only to
temporary locations outside the repository.
