"""Portfolio inventory adapter.

Registry discovers Git checkouts from the filesystem. The filesystem cannot say
whether a checkout is a maintained project, a private one, or a vendored runtime
dependency that happens to ship its own `.git`; inferring that from names or
paths would be a heuristic dressed as a fact. `portfolio/inventory.yaml` records
the answer deliberately, so the classification is joined in rather than guessed.

The reader below parses only the `core.portfolio.inventory.v1` shape and raises
on anything outside it. Registry declares no dependencies, so adding a YAML
library for one file is not available; a permissive hand-rolled parser would be
worse than no parser, because a misread would silently produce a wrong
classification. This one refuses instead: unsupported indentation, quoting,
anchors, flow collections, and nested blocks inside sequence items all raise.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "core.portfolio.inventory.v1"

# group name in the inventory -> lifecycle recorded on the project
REPOSITORY_GROUPS = {"active_core": "active", "active_private": "active-private"}
VENDORED_GROUP = "vendored_git_checkouts"
NON_PROJECT_GROUP = "non_projects"

_ENTRY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):(?:[ ](.*))?$")
_INT_RE = re.compile(r"-?\d+$")
_UNSUPPORTED_LEADS = "[{&*|>!%@`"


class InventoryFormatError(ValueError):
    """The document is not the supported inventory shape."""


def _tokenize(text: str) -> list[tuple[int, str, int]]:
    tokens: list[tuple[int, str, int]] = []
    for number, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if "\t" in raw:
            raise InventoryFormatError(f"line {number}: tab indentation is not supported")
        if raw.rstrip() != raw:
            raise InventoryFormatError(f"line {number}: trailing whitespace is not supported")
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise InventoryFormatError(f"line {number}: indent {indent} is not a multiple of two")
        tokens.append((indent, raw.strip(), number))
    return tokens


def _scalar(token: str, number: int) -> Any:
    if not token:
        raise InventoryFormatError(f"line {number}: empty value")
    if token[0] in _UNSUPPORTED_LEADS:
        raise InventoryFormatError(f"line {number}: unsupported YAML construct starting {token[0]!r}")
    if token[0] in "\"'" or token[-1] in "\"'":
        raise InventoryFormatError(f"line {number}: quoted scalars are not supported")
    if "#" in token:
        raise InventoryFormatError(f"line {number}: trailing comments are not supported")
    lowered = token.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    if lowered in ("null", "~"):
        return None
    if _INT_RE.match(token):
        return int(token)
    return token


def _entry(body: str, number: int) -> tuple[str, str | None]:
    match = _ENTRY_RE.match(body)
    if not match:
        raise InventoryFormatError(f"line {number}: expected 'key:' or 'key: value', got {body!r}")
    return match.group(1), match.group(2)


def _parse_block(tokens: list[tuple[int, str, int]], index: int, indent: int) -> tuple[Any, int]:
    if tokens[index][1].startswith("- "):
        return _parse_sequence(tokens, index, indent)
    return _parse_mapping(tokens, index, indent)


def _parse_mapping(tokens: list[tuple[int, str, int]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(tokens) and tokens[index][0] == indent:
        _, body, number = tokens[index]
        if body.startswith("- "):
            raise InventoryFormatError(f"line {number}: sequence item where a mapping key was expected")
        key, value = _entry(body, number)
        if key in result:
            raise InventoryFormatError(f"line {number}: duplicate key {key!r}")
        index += 1
        if value is None:
            if index >= len(tokens) or tokens[index][0] <= indent:
                raise InventoryFormatError(f"line {number}: key {key!r} has neither a value nor a block")
            result[key], index = _parse_block(tokens, index, tokens[index][0])
        else:
            result[key] = _scalar(value, number)
    if index < len(tokens) and tokens[index][0] > indent:
        raise InventoryFormatError(f"line {tokens[index][2]}: unexpected indent {tokens[index][0]}")
    return result, index


def _parse_sequence(tokens: list[tuple[int, str, int]], index: int, indent: int) -> tuple[list[dict[str, Any]], int]:
    items: list[dict[str, Any]] = []
    inner = indent + 2
    while index < len(tokens) and tokens[index][0] == indent and tokens[index][1].startswith("- "):
        _, body, number = tokens[index]
        key, value = _entry(body[2:], number)
        if value is None:
            raise InventoryFormatError(f"line {number}: sequence items must start with 'key: value'")
        item: dict[str, Any] = {key: _scalar(value, number)}
        index += 1
        while index < len(tokens) and tokens[index][0] == inner and not tokens[index][1].startswith("- "):
            _, body, number = tokens[index]
            key, value = _entry(body, number)
            if key in item:
                raise InventoryFormatError(f"line {number}: duplicate key {key!r}")
            if value is None:
                raise InventoryFormatError(f"line {number}: nested blocks inside sequence items are not supported")
            item[key] = _scalar(value, number)
            index += 1
        items.append(item)
    if index < len(tokens) and tokens[index][0] > indent:
        raise InventoryFormatError(f"line {tokens[index][2]}: unexpected indent {tokens[index][0]}")
    return items, index


def parse_document(text: str) -> dict[str, Any]:
    """Parse the supported subset, or raise `InventoryFormatError`."""
    tokens = _tokenize(text)
    if not tokens:
        raise InventoryFormatError("the document is empty")
    if tokens[0][0] != 0:
        raise InventoryFormatError(f"line {tokens[0][2]}: the document must start at indent 0")
    document, index = _parse_mapping(tokens, 0, 0)
    if index != len(tokens):
        raise InventoryFormatError(f"line {tokens[index][2]}: unparsed trailing content")
    return document


def _rows(document: dict[str, Any], group: str, container: str | None = None) -> list[dict[str, Any]]:
    holder = document if container is None else document.get(container, {})
    if container is not None and not isinstance(holder, dict):
        raise InventoryFormatError(f"{container!r} must be a mapping of groups")
    rows = holder.get(group, [])
    if not rows:
        return []
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise InventoryFormatError(f"{group!r} must be a list of mappings")
    for row in rows:
        if not isinstance(row.get("path"), str):
            raise InventoryFormatError(f"{group!r} has a row without a string 'path'")
    return rows


def load(path: Path) -> dict[str, Any]:
    """Read and validate an inventory file."""
    document = parse_document(path.read_text(encoding="utf-8"))
    schema = document.get("schema")
    if schema != SCHEMA:
        raise InventoryFormatError(f"{path}: expected schema {SCHEMA}, got {schema!r}")
    roots = document.get("workspace_roots")
    if not isinstance(roots, list) or not roots:
        raise InventoryFormatError(f"{path}: 'workspace_roots' must be a non-empty list")
    for entry in roots:
        if not isinstance(entry.get("id"), str) or not isinstance(entry.get("logical_path"), str):
            raise InventoryFormatError(f"{path}: every workspace root needs a string 'id' and 'logical_path'")
    for group in REPOSITORY_GROUPS:
        _rows(document, group, "repositories")
    _rows(document, VENDORED_GROUP)
    _rows(document, NON_PROJECT_GROUP)
    document["_source_path"] = str(path)
    return document


def _row_paths(document: dict[str, Any]) -> list[str]:
    rows: list[dict[str, Any]] = []
    for group in REPOSITORY_GROUPS:
        rows.extend(_rows(document, group, "repositories"))
    rows.extend(_rows(document, VENDORED_GROUP))
    rows.extend(_rows(document, NON_PROJECT_GROUP))
    return [row["path"] for row in rows]


def _base_for(document: dict[str, Any], roots: list[Path]) -> tuple[Path, dict[str, str]]:
    """The directory the inventory's row paths are relative to.

    `logical_path` and the row paths do not share an origin -- a root is recorded
    as `source/core` while its rows read `core/...` -- so the base is derived
    rather than assumed. For each configured root, the longest tail of its
    `logical_path` that actually prefixes a row is taken as that root's row
    prefix, and the base is what remains of the root once that prefix is
    removed. Every matched root must yield the same base: a disagreement means
    the inventory and the configured roots describe different layouts, which is
    exactly the case where a guessed base would join the wrong rows.
    """
    paths = _row_paths(document)
    bases: set[Path] = set()
    prefixes: dict[str, str] = {}
    for entry in document["workspace_roots"]:
        logical = PurePosixPath(entry["logical_path"])
        candidates = [root for root in roots if root.as_posix().endswith("/" + logical.as_posix())]
        if len(candidates) > 1:
            raise InventoryFormatError(f"workspace root {entry['id']!r} matches more than one configured root")
        if not candidates:
            continue
        root = candidates[0]
        parts = logical.parts
        for start in range(len(parts)):
            prefix = "/".join(parts[start:])
            if not any(path == prefix or path.startswith(prefix + "/") for path in paths):
                continue
            base = root
            for _ in parts[start:]:
                base = base.parent
            bases.add(base)
            prefixes[prefix] = entry["id"]
            break
    if not prefixes:
        raise InventoryFormatError("no configured workspace root matches any inventory row path")
    if len(bases) > 1:
        raise InventoryFormatError(f"inventory roots imply more than one base directory: {sorted(str(item) for item in bases)}")
    return bases.pop(), prefixes


def classifications(document: dict[str, Any], roots: list[Path]) -> dict[str, Any]:
    """Join keys and classification records for the configured roots.

    Returns the derived base, the classification for every inventory row that
    falls under a configured root, and the rows that fall outside them. Rows
    outside are not drift: an inventory describing a root Registry was not asked
    to scan is complete; the scan is simply narrower.
    """
    base, matched = _base_for(document, roots)
    prefixes = sorted(matched)
    source = rel_path(base, Path(document["_source_path"]))
    index: dict[str, dict[str, Any]] = {}
    out_of_scope: list[dict[str, Any]] = []

    def record(row: dict[str, Any], lifecycle: str, group: str) -> None:
        path = row["path"]
        entry = {
            "lifecycle": lifecycle,
            "group": group,
            "inventory_path": path,
            "source": source,
        }
        for key in ("role", "owner", "classification", "reason", "git_repository"):
            if key in row:
                entry[key] = row[key]
        if not any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes):
            out_of_scope.append(entry)
            return
        if path in index:
            raise InventoryFormatError(f"inventory lists {path!r} more than once")
        index[path] = entry

    for group, lifecycle in REPOSITORY_GROUPS.items():
        for row in _rows(document, group, "repositories"):
            record(row, lifecycle, group)
    for row in _rows(document, VENDORED_GROUP):
        record(row, "vendored", VENDORED_GROUP)
    for row in _rows(document, NON_PROJECT_GROUP):
        record(row, "non-project", NON_PROJECT_GROUP)
    return {"base": base, "index": index, "out_of_scope": out_of_scope, "source": source}


def rel_path(base: Path, path: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.as_posix()
