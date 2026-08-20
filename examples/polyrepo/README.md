# AINE public polyrepo example

This fixture models a small portfolio with two workspace roots and three
independent Git projects:

```text
examples/polyrepo/
├── core/
│   ├── checkout-service/   API provider and source of truth
│   └── web-app/            runtime API consumer
└── side-projects/
    └── content-tool/       generated content producer
```

The fixture contains no private paths, credentials, or external services. Run
the setup script once to initialize each example project as an independent Git
checkout:

```bash
cd examples/polyrepo
python3 setup.py
```

On Windows PowerShell, use `py setup.py`. The `setup.sh` wrapper remains
available for Unix shells.

Discover the portfolio from the repository root:

```bash
python3 ../../registry/aine_registry.py discover \
  --root core --root side-projects --output snapshot.json
python3 ../../registry/aine_registry.py dependencies \
  --root core --root side-projects
python3 ../../registry/aine_registry.py impact \
  --root core --root side-projects --project core.checkout-service
python3 ../../registry/aine_registry.py preflight \
  --root core --root side-projects \
  --change core/checkout-service/openapi.yaml
```

Expected relationships include:

- `core.web-app` → `core.checkout-service` as a runtime API dependency.
- `side-projects.content-tool` → `core.checkout-service` as a cross-root
  declared dependency.
- `core.checkout-service/openapi.yaml` as the `checkout.api` source of truth.
- `side-projects/content-tool/generated/content.json` as a generated artifact.

The example demonstrates the registry boundary only. AINE does not run the
projects, call an API, generate content, or deploy anything.
