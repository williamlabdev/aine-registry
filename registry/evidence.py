"""Local evidence and audit record primitives."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

try:
    from .common import *
except ImportError:  # direct execution: python3 registry/aine_registry.py
    from common import *

def evidence_record(report: dict[str, Any]) -> dict[str, Any]:
    """Return a report that is also directly consumable as aine.evidence.v1."""
    record = dict(report)
    record.update(report["evidence"])
    return record


# Record kinds this store holds. Structural validity is the producing tool's
# responsibility; the store owns identity, integrity, and append-only behavior.
# `aine.control-plane.integration-observation.v1` is the adapter boundary for
# enforcement products: it carries a producer run identifier, a shared
# correlation identifier, and the digest of the native payload, never the
# payload itself, so evidence from a separate product joins a snapshot without
# the core taking a dependency on that product.
EVIDENCE_STORE_SCHEMAS = {
    "aine.evidence.v1",
    "aine.handoff.v1",
    "aine.approval.v1",
    "aine.registry.v1",
    "aine.control-plane.integration-observation.v1",
}


# Fields whose value is the record_id of another record in this store, listed
# under the schema that defines them that way. A field belongs here only where
# the contract says the value identifies a stored record. `aine.registry.v1`
# also carries `snapshot_id`, but that one is the digest of the snapshot's own
# canonical content rather than the identity of a stored envelope, so chasing
# it would report every snapshot as a dangling reference to itself.
STORE_REFERENCE_FIELDS = {
    "aine.control-plane.integration-observation.v1": ("snapshot_id",),
}


def record_digest(record: dict[str, Any]) -> str:
    encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def load_store_record(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("record"), dict) or not payload.get("record_id"):
        raise ValueError("invalid evidence store envelope")
    expected = record_digest(payload["record"])
    if payload["record_id"] != expected:
        raise ValueError("evidence store integrity check failed")
    return payload


def store_record(store_root: Path, record: dict[str, Any]) -> dict[str, Any]:
    schema = record.get("schema")
    if schema not in EVIDENCE_STORE_SCHEMAS:
        raise ValueError(f"unsupported record schema: {schema or 'UNKNOWN'}")
    digest = record_digest(record)
    store_root.expanduser().mkdir(parents=True, exist_ok=True)
    target = store_root.expanduser() / f"{digest.removeprefix('sha256:')}.json"
    envelope = {"record_id": digest, "record": record}
    encoded = json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        existing = load_store_record(target)
        if existing["record_id"] != digest:
            raise ValueError("evidence store collision detected")
        return {"record_id": digest, "schema": schema, "status": "already_present"}
    target.write_text(encoded, encoding="utf-8")
    return {"record_id": digest, "schema": schema, "status": "stored"}


def store_references(record: dict[str, Any]) -> list[str]:
    """Return the record_ids of other stored records this record points at."""
    references = []
    for field in STORE_REFERENCE_FIELDS.get(record.get("schema"), ()):
        value = record.get(field)
        if isinstance(value, str) and value:
            references.append(value)
    return references


def list_store_records(store_root: Path, correlation_id: str | None = None) -> list[dict[str, Any]]:
    if not store_root.expanduser().is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(store_root.expanduser().glob("*.json")):
        try:
            envelope = load_store_record(path)
            record = envelope["record"]
            entry = {"record_id": envelope["record_id"], "schema": record.get("schema", "UNKNOWN"), "path": path.name}
            # A correlation identifier is what makes records from different
            # producers reviewable as one run, so it stays visible in the index.
            correlation = record.get("correlation_id")
            if isinstance(correlation, str) and correlation:
                entry["correlation_id"] = correlation
            if correlation_id is not None and entry.get("correlation_id") != correlation_id:
                continue
            records.append(entry)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            # A record that cannot be read has no readable correlation either.
            # Dropping it from a scoped listing would hide a broken store from
            # the review most likely to care that it is broken.
            records.append({"path": path.name, "status": "invalid", "error": str(exc)})
    return records


def unresolved_references(store_root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report referenced records the bundle does not carry, and why.

    Scoping a bundle to one correlation cuts the snapshot an observation was
    taken against out of the bundle, because a snapshot belongs to no single
    correlation. Silently pulling it back in would make the declared scope a
    lie, and silently omitting it would leave a join that cannot be followed,
    so the bundle names what is absent instead. `out_of_scope` means the store
    holds the record and this bundle excluded it; `missing` means the store
    cannot produce it at all, including when it holds only an unreadable copy
    already reported in `invalid_records`.
    """

    present = {record_digest(record) for record in records}
    readable = {entry["record_id"] for entry in list_store_records(store_root) if entry.get("record_id")}
    unresolved: dict[str, dict[str, Any]] = {}
    for record in records:
        for reference in store_references(record):
            if reference in present:
                continue
            entry = unresolved.setdefault(
                reference,
                {"record_id": reference, "status": "out_of_scope" if reference in readable else "missing", "referenced_by": []},
            )
            entry["referenced_by"].append(record_digest(record))
    for entry in unresolved.values():
        entry["referenced_by"].sort()
    return [unresolved[key] for key in sorted(unresolved)]


def command_evidence(args: argparse.Namespace) -> int:
    store_root = Path(args.store).expanduser().resolve()
    if args.evidence_action == "store":
        try:
            input_path = Path(args.input).expanduser()
            source = json.loads(input_path.read_text(encoding="utf-8"))
            nested_evidence = source.get("evidence") if isinstance(source, dict) else None
            record = evidence_record(source) if isinstance(nested_evidence, dict) and nested_evidence.get("schema") == "aine.evidence.v1" and source.get("schema") != "aine.evidence.v1" else source
            if not isinstance(record, dict):
                raise ValueError("input record must be a JSON object")
            print_json(store_record(store_root, record))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"could not store evidence: {exc}", file=sys.stderr); return 2
    elif args.evidence_action == "list":
        print_json(list_store_records(store_root, args.correlation))
    elif args.evidence_action == "get":
        record_id = args.id if args.id.startswith("sha256:") else f"sha256:{args.id}"
        target = store_root / f"{record_id.removeprefix('sha256:')}.json"
        try:
            print_json(load_store_record(target)["record"])
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"could not read evidence: {exc}", file=sys.stderr); return 2
    elif args.evidence_action in {"export", "retention"}:
        try:
            correlation_id = getattr(args, "correlation", None)
            entries = list_store_records(store_root, correlation_id)
            valid_records = []
            for entry in entries:
                if entry.get("status") == "invalid":
                    continue
                envelope = load_store_record(store_root / entry["path"])
                valid_records.append(envelope["record"])
            if args.evidence_action == "export":
                payload = {"schema": "aine.audit.bundle.v1", "record_ids": [record_digest(record) for record in valid_records], "records": valid_records, "invalid_records": [entry for entry in entries if entry.get("status") == "invalid"], "unresolved_refs": unresolved_references(store_root, valid_records), "read_only": True}
                # An unscoped bundle must not claim a scope it does not have.
                if correlation_id is not None:
                    payload["correlation_id"] = correlation_id
            else:
                as_of = datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
                cutoff = as_of.timestamp() - (args.retain_days * 86400)
                retention_records = []
                for record in valid_records:
                    observed = record.get("observed_at") or record.get("created_at") or record.get("workspace", {}).get("observed_at")
                    observed_time = None
                    if isinstance(observed, str):
                        try: observed_time = datetime.fromisoformat(observed.replace("Z", "+00:00")).timestamp()
                        except ValueError: observed_time = None
                    retention_records.append({"record_id": record_digest(record), "status": "review" if observed_time is not None and observed_time < cutoff else ("keep" if observed_time is not None else "unknown"), "observed_at": observed})
                payload = {"schema": "aine.retention.manifest.v1", "as_of": args.as_of, "retain_days": args.retain_days, "records": retention_records, "read_only": True}
            if args.output:
                output = Path(args.output).expanduser().resolve(); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"); print_json({"status": "written", "output": output.name, "records": len(payload["records"])})
            else:
                print_json(payload)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"could not process evidence store: {exc}", file=sys.stderr); return 2
    return 0
