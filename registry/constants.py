"""Shared constants for the AINE Registry modules."""

from __future__ import annotations

import re
import sys
from pathlib import Path


SCHEMA = "aine.registry.v1"
DEFAULT_IGNORES = {
    ".git", ".claude", ".codex", ".agents", "node_modules", "target",
    "dist", ".next", "build", "out", "bench-runs", ".venv", "__pycache__",
    ".godot", ".pytest_cache", "coverage", ".orvena", ".cache", "bench",
    "bench-runs", "worktrees", "tmp",
}
DEFAULT_EXCLUDED_PROJECTS: set[str] = set()
MANIFEST_NAMES = {
    "package.json", "Cargo.toml", "go.mod", "pyproject.toml",
    "pnpm-workspace.yaml", "docker-compose.yml", "docker-compose.yaml",
    "Makefile", "manifest.yaml", "requirements.txt",
}
INSTRUCTION_NAMES = {"CLAUDE.md", "AGENTS.md"}
ARTIFACT_NAMES = {
    "SYNC_STAMP", "courses.generated.json", "ros_types.yaml", "specs-manifest.json",
}
API_CONTRACT_NAMES = {
    "api.json", "api.yaml", "api.yml", "openapi.json", "openapi.yaml", "openapi.yml",
    "swagger.json", "swagger.yaml", "swagger.yml",
}
ASYNCAPI_CONTRACT_NAMES = {"asyncapi.json", "asyncapi.yaml", "asyncapi.yml", "events.yaml", "events.yml"}
DEPLOYMENT_NAMES = {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "chart.yaml"}
PROJECT_MANIFEST = Path(".aine/registry.json")
RELATIONSHIP_OVERLAY_KEY = "relationship_overlays"
# A project manifest is committed with its project, so every target it names is
# published with it. Overlay evidence marks an edge declared in local
# configuration instead, and carries no project-relative path by construction.
OVERLAY_EVIDENCE = "<local-overlay>"
RELATIONSHIP_SOURCES = {"manifest", "overlay"}
# Projects whose repositories are published. Declared in the local configuration
# because publication is portfolio knowledge: a private project cannot be asked
# to prove it is private, and a public one declaring itself public proves nothing.
PUBLISHED_PROJECTS_KEY = "published_projects"
INVENTORY_KEY = "inventory"
GENERATED_MARKERS = ("generated", "_gen.", ".generated.", "SYNC_STAMP")
SCAN_SKIP_DIRS = {"data", "media", "models", "checkpoints", "logs", "reports", "fixtures", "vendor", "third_party"}
IMPORT_SOURCE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs"}
MAX_ARTIFACTS_PER_PROJECT = 500
LOCAL_PATH_RE = re.compile(r"^(?:/|[A-Za-z]:[\\/])")
# A Python import target is a dotted identifier path. The source patterns are
# textual, so prose that begins with "import " is rejected here rather than
# recorded as a dependency on a module that cannot exist.
PYTHON_MODULE_RE = re.compile(r"^\.*[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
SOURCE_TRUTH_SEED: list[dict[str, object]] = []

# Standard-library providers. A standard-library import is a resolved dependency
# on the language runtime, not an unknown external provider, so it is recorded
# for explainability without producing a project dependency edge.
#
# `sys.stdlib_module_names` exists from Python 3.10 onward. The static baseline
# keeps the same behavior on Python 3.9 and covers modules that later releases
# removed, so a snapshot does not change meaning with the interpreter that
# produced it.
PYTHON_STDLIB_BASELINE = frozenset({
    "abc", "aifc", "annotationlib", "antigravity", "argparse", "array", "ast", "asynchat",
    "asyncio", "asyncore", "atexit", "audioop", "base64", "bdb", "binascii", "binhex", "bisect",
    "builtins", "bz2", "cProfile", "calendar", "cgi", "cgitb", "chunk", "cmath", "cmd", "code",
    "codecs", "codeop", "collections", "colorsys", "compileall", "compression", "concurrent",
    "configparser", "contextlib", "contextvars", "copy", "copyreg", "crypt", "csv", "ctypes",
    "curses", "dataclasses", "datetime", "dbm", "decimal", "difflib", "dis", "distutils",
    "doctest", "dummy_threading", "email", "encodings", "ensurepip", "enum", "errno",
    "faulthandler", "fcntl", "filecmp", "fileinput", "fnmatch", "formatter", "fractions",
    "ftplib", "functools", "gc", "genericpath", "getopt", "getpass", "gettext", "glob",
    "graphlib", "grp", "gzip", "hashlib", "heapq", "hmac", "html", "http", "idlelib", "imaplib",
    "imghdr", "imp", "importlib", "inspect", "io", "ipaddress", "itertools", "json", "keyword",
    "lib2to3", "linecache", "locale", "logging", "lzma", "mailbox", "mailcap", "marshal",
    "math", "mimetypes", "mmap", "modulefinder", "msilib", "msvcrt", "multiprocessing", "netrc",
    "nis", "nntplib", "nt", "ntpath", "nturl2path", "numbers", "opcode", "operator", "optparse",
    "os", "ossaudiodev", "parser", "pathlib", "pdb", "pickle", "pickletools", "pipes",
    "pkgutil", "platform", "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc", "pydoc_data", "pyexpat", "queue",
    "quopri", "random", "re", "readline", "reprlib", "resource", "rlcompleter", "runpy",
    "sched", "secrets", "select", "selectors", "shelve", "shlex", "shutil", "signal", "site",
    "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd", "sqlite3", "sre_compile",
    "sre_constants", "sre_parse", "ssl", "stat", "statistics", "string", "stringprep", "struct",
    "subprocess", "sunau", "symbol", "symtable", "sys", "sysconfig", "syslog", "tabnanny",
    "tarfile", "telnetlib", "tempfile", "termios", "textwrap", "this", "threading", "time",
    "timeit", "tkinter", "token", "tokenize", "tomllib", "trace", "traceback", "tracemalloc",
    "tty", "turtle", "turtledemo", "types", "typing", "unicodedata", "unittest", "urllib", "uu",
    "uuid", "venv", "warnings", "wave", "weakref", "webbrowser", "winreg", "winsound",
    "wsgiref", "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport", "zlib", "zoneinfo",
})
PYTHON_STDLIB_MODULES = frozenset(getattr(sys, "stdlib_module_names", ())) | PYTHON_STDLIB_BASELINE
NODE_BUILTIN_MODULES = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants",
    "crypto", "dgram", "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2",
    "https", "inspector", "module", "net", "os", "path", "perf_hooks", "process", "punycode",
    "querystring", "readline", "repl", "stream", "string_decoder", "sys", "timers", "tls",
    "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
})
RUST_STDLIB_CRATES = frozenset({"alloc", "core", "proc_macro", "std", "test"})
