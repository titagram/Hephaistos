"""Local execution for Hades backend requested read-only jobs."""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import tomllib
from typing import Any
from urllib.parse import urlsplit

from hermes_cli.hades_backend_client import redact_secret


SKIP_DIRS = {
    ".cache",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}
SECRET_FILE_NAMES = {
    ".env",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
}
SECRET_SUFFIXES = {
    ".cert",
    ".crt",
    ".der",
    ".key",
    ".p12",
    ".pem",
    ".pfx",
}
BINARY_SUFFIXES = {
    ".7z",
    ".avi",
    ".bmp",
    ".dmg",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".pdf",
    ".png",
    ".tar",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}
LANGUAGE_SUFFIXES = {
    ".css": "css",
    ".go": "go",
    ".js": "javascript",
    ".jsx": "javascript",
    ".md": "markdown",
    ".php": "php",
    ".prisma": "prisma",
    ".py": "python",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "shell",
    ".sql": "sql",
    ".ts": "typescript",
    ".tsx": "typescript",
}
DEPENDENCY_MANIFESTS = {
    "composer.json": "composer",
    "package.json": "npm",
    "pyproject.toml": "python",
    "requirements.txt": "python",
}
ROUTE_CALL_RE = re.compile(
    r"Route::(?P<method>get|post|put|patch|delete|options|any)\s*"
    r"\(\s*['\"](?P<uri>[^'\"]+)['\"]\s*,\s*(?P<handler>.*?)\)\s*"
    r"(?:->name\(\s*['\"](?P<name>[^'\"]+)['\"]\s*\))?",
    re.IGNORECASE | re.DOTALL,
)
ROUTE_RESOURCE_RE = re.compile(
    r"Route::(?P<kind>resource|apiResource)\s*"
    r"\(\s*['\"](?P<resource>[^'\"]+)['\"]\s*,\s*(?P<controller>\\?[A-Za-z0-9_\\]+)::class\s*\)",
    re.IGNORECASE | re.DOTALL,
)
LARAVEL_HANDLER_RE = re.compile(
    r"\[\s*(?P<class>[A-Za-z0-9_\\\\]+)::class\s*,\s*['\"](?P<method>[A-Za-z0-9_]+)['\"]\s*\]"
)
PHP_LOG_LEVEL_PATTERN = r"debug|info|notice|warn|warning|error|critical|alert|emergency"
PHP_NAMESPACE_RE = re.compile(r"^\s*namespace\s+(?P<namespace>[A-Za-z0-9_\\]+)\s*;", re.MULTILINE)
PHP_USE_RE = re.compile(
    r"^\s*use\s+(?P<class>[A-Za-z0-9_\\]+)(?:\s+as\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*))?\s*;",
    re.MULTILINE,
)
PHP_CLASS_RE = re.compile(
    r"\b(?P<kind>class|interface|trait|enum)\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\s+extends\s+(?P<extends>[A-Za-z0-9_\\]+))?",
    re.MULTILINE,
)
PHP_METHOD_RE = re.compile(
    r"\b(?P<visibility>public|protected|private)\s+(?:static\s+)?function\s+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)",
    re.MULTILINE,
)
PHP_ELOQUENT_RELATION_RE = re.compile(
    r"\$this->(?P<relation>hasOne|hasMany|belongsTo|belongsToMany|morphOne|morphMany|morphToMany)"
    r"\s*\(\s*(?P<target>[A-Za-z0-9_\\]+)::class",
    re.MULTILINE,
)
PHP_STATIC_CALL_RE = re.compile(r"\b(?P<class>[A-Z][A-Za-z0-9_\\]+)::(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
PHP_NEW_RE = re.compile(r"\bnew\s+(?P<class>[A-Z][A-Za-z0-9_\\]+)\s*\(")
PHP_ROUTE_NAME_RE = re.compile(r"->name\(\s*['\"](?P<name>[^'\"]+)['\"]\s*\)")
PHP_ROUTE_MIDDLEWARE_RE = re.compile(r"->middleware\(\s*(?P<value>.*?)\s*\)", re.DOTALL)
PHP_ROUTE_PARAM_RE = re.compile(r"\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:\?)?[^}]*\}")
PHP_QUOTED_VALUE_RE = re.compile(r"['\"](?P<value>[^'\"]+)['\"]")
PHP_THIS_AUTHORIZE_RE = re.compile(
    r"\$this->authorize\s*\(\s*['\"](?P<ability>[^'\"]+)['\"]\s*,\s*\$(?P<var>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
PHP_GATE_AUTHORIZATION_RE = re.compile(
    r"\bGate::(?P<method>authorize|allows|denies|check)\s*\(\s*['\"](?P<ability>[^'\"]+)['\"]\s*,\s*\$(?P<var>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
PHP_ARRAY_ENTRY_CLASS_RE = re.compile(
    r"['\"](?P<key>[^'\"]+)['\"]\s*=>\s*(?:\\\\)?(?P<class>[A-Za-z_][A-Za-z0-9_\\]+)::class"
)
PHP_ARRAY_ENTRY_LIST_RE = re.compile(
    r"['\"](?P<key>[^'\"]+)['\"]\s*=>\s*\[(?P<items>.*?)\]",
    re.DOTALL,
)
PHP_CLASS_CONST_RE = re.compile(r"(?:\\\\)?(?P<class>[A-Za-z_][A-Za-z0-9_\\]+)::class")
PHP_MODEL_TABLE_RE = re.compile(r"\bprotected\s+\$table\s*=\s*['\"](?P<table>[^'\"]+)['\"]", re.MULTILINE)
PHP_MODEL_LIST_PROPERTY_RE = re.compile(
    r"\bprotected\s+(?:array\s+)?\$(?P<property>fillable|guarded|hidden|visible|appends)\s*=\s*\[(?P<body>.*?)\]\s*;",
    re.DOTALL | re.MULTILINE,
)
PHP_MODEL_CASTS_PROPERTY_RE = re.compile(
    r"\bprotected\s+(?:array\s+)?\$casts\s*=\s*\[(?P<body>.*?)\]\s*;",
    re.DOTALL | re.MULTILINE,
)
PHP_MODEL_CASTS_METHOD_RE = re.compile(
    r"\bfunction\s+casts\s*\([^)]*\)\s*(?::\s*array)?\s*\{(?P<body>.*?)\}",
    re.DOTALL | re.MULTILINE,
)
PHP_CLASS_TRAIT_USE_RE = re.compile(
    r"^[ \t]*use\s+(?P<traits>\\?[A-Za-z0-9_\\]+(?:\s*,\s*\\?[A-Za-z0-9_\\]+)*)\s*;",
    re.MULTILINE,
)
PHP_RETURN_ARRAY_RE = re.compile(r"\breturn\s*\[(?P<body>.*?)\]\s*;", re.DOTALL)
PHP_ARRAY_STRING_PAIR_RE = re.compile(
    r"['\"](?P<key>[^'\"]+)['\"]\s*=>\s*['\"](?P<value>[^'\"]+)['\"]"
)
PHP_CONFIG_RE = re.compile(r"\bconfig\s*\(\s*['\"](?P<key>[^'\"]+)['\"]", re.MULTILINE)
PHP_ENV_RE = re.compile(r"\benv\s*\(\s*['\"](?P<key>[^'\"]+)['\"]", re.MULTILINE)
PHP_LOG_STATIC_RE = re.compile(
    rf"(?<![A-Za-z0-9_\\])(?P<logger>\\?(?:[A-Za-z_][A-Za-z0-9_]*\\)*Log|Logger)\s*::\s*"
    rf"(?P<level>{PHP_LOG_LEVEL_PATTERN})\s*\(\s*(?P<quote>['\"])(?P<message>(?:\\.|(?!(?P=quote)).)*?)(?P=quote)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
PHP_LOG_INSTANCE_RE = re.compile(
    rf"(?P<logger>\$this->[A-Za-z_][A-Za-z0-9_]*|\$[A-Za-z_][A-Za-z0-9_]*)\s*->\s*"
    rf"(?P<level>{PHP_LOG_LEVEL_PATTERN})\s*\(\s*(?P<quote>['\"])(?P<message>(?:\\.|(?!(?P=quote)).)*?)(?P=quote)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
PHP_LOGGER_CHAIN_RE = re.compile(
    rf"\blogger\s*\(\s*\)\s*->\s*(?P<level>{PHP_LOG_LEVEL_PATTERN})\s*\(\s*"
    r"(?P<quote>['\"])(?P<message>(?:\\.|(?!(?P=quote)).)*?)(?P=quote)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
PHP_LOGGER_HELPER_RE = re.compile(
    r"\blogger\s*\(\s*(?P<quote>['\"])(?P<message>(?:\\.|(?!(?P=quote)).)*?)(?P=quote)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
PHP_GATE_POLICY_RE = re.compile(
    r"\bGate::policy\s*\(\s*(?P<model>\\?[A-Za-z0-9_\\]+)::class\s*,\s*(?P<policy>\\?[A-Za-z0-9_\\]+)::class",
    re.MULTILINE,
)
PHP_POLICIES_PROPERTY_RE = re.compile(
    r"\bprotected\s+(?:array\s+)?\$policies\s*=\s*\[(?P<body>.*?)\]\s*;",
    re.DOTALL | re.MULTILINE,
)
PHP_POLICY_MAP_ENTRY_RE = re.compile(
    r"(?P<model>\\?[A-Za-z0-9_\\]+)::class\s*=>\s*(?P<policy>\\?[A-Za-z0-9_\\]+)::class",
    re.MULTILINE,
)
PHP_TYPED_PARAM_RE = re.compile(r"(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s+\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)")
PHP_PROMOTED_PROPERTY_PARAM_RE = re.compile(
    r"\b(?:public|protected|private)\s+(?:readonly\s+)?"
    r"(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s+\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
)
PHP_PROPERTY_RE = re.compile(
    r"\b(?P<visibility>public|protected|private)\s+"
    r"(?:readonly\s+)?(?P<type>\??[A-Za-z_\\][A-Za-z0-9_\\|]*)?\s*"
    r"\$(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
PHP_LIVEWIRE_RULES_PROPERTY_RE = re.compile(
    r"\b(?:public|protected)\s+(?:array\s+)?\$rules\s*=\s*\[(?P<body>.*?)\]\s*;",
    re.DOTALL | re.MULTILINE,
)
PHP_THIS_PROPERTY_ASSIGN_RE = re.compile(
    r"\$this->(?P<property>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*\$(?P<param>[A-Za-z_][A-Za-z0-9_]*)\s*;"
)
PHP_VALIDATE_ARRAY_RE = re.compile(
    r"(?:\$[A-Za-z_][A-Za-z0-9_]*|request\s*\(\))->validate\s*\(\s*\[(?P<body>.*?)\]\s*\)",
    re.DOTALL,
)
PHP_ARRAY_FIELD_KEY_RE = re.compile(r"['\"](?P<field>[A-Za-z0-9_.*-]+)['\"]\s*=>")
PHP_THIS_INPUT_MUTATION_RE = re.compile(r"\$this->(?P<operation>merge|replace)\s*\(", re.MULTILINE)
PHP_ABORT_HELPER_RE = re.compile(r"\babort(?P<suffix>_if|_unless)?\s*\(", re.IGNORECASE | re.MULTILINE)
PHP_RESPONSE_HELPER_RE = re.compile(
    r"\bresponse\s*\(\s*\)\s*->\s*(?P<method>json|noContent)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_RESPONSE_COOKIE_CHAIN_RE = re.compile(
    r"\bresponse\s*\(\s*\)\s*->\s*(?P<method>cookie|withoutCookie)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_COOKIE_HELPER_RE = re.compile(r"(?<!->)(?<!::)\bcookie\s*\(", re.IGNORECASE | re.MULTILINE)
PHP_COOKIE_FACADE_RE = re.compile(r"\bCookie::(?P<method>queue|forget|make)\s*\(", re.IGNORECASE | re.MULTILINE)
PHP_REDIRECT_HELPER_RE = re.compile(
    r"\b(?P<helper>redirect|back)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_REDIRECT_CHAIN_RE = re.compile(
    r"\bredirect\s*\(\s*\)\s*->\s*(?P<method>route|to|away|back)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_SESSION_HELPER_RE = re.compile(r"\bsession\s*\(", re.IGNORECASE | re.MULTILINE)
PHP_SESSION_CHAIN_RE = re.compile(
    r"\bsession\s*\(\s*\)\s*->\s*(?P<method>get|put|flash|forget|has|pull|remove)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_REQUEST_SESSION_CHAIN_RE = re.compile(
    r"(?P<receiver>\$this->[A-Za-z_][A-Za-z0-9_]*|\$[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*->\s*session\s*\(\s*\)\s*->\s*(?P<method>get|put|flash|forget|has|pull|remove)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_SESSION_FACADE_RE = re.compile(
    r"\bSession::(?P<method>get|put|flash|forget|has|pull|remove)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_CACHE_HELPER_RE = re.compile(r"\bcache\s*\(", re.IGNORECASE | re.MULTILINE)
PHP_CACHE_CHAIN_RE = re.compile(
    r"\bcache\s*\(\s*\)\s*->\s*"
    r"(?P<method>get|put|add|forever|remember|rememberForever|forget|has|pull|increment|decrement)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_CACHE_FACADE_RE = re.compile(
    r"\bCache::(?P<method>get|put|add|forever|remember|rememberForever|forget|has|pull|increment|decrement)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_HTTP_FACADE_RE = re.compile(
    r"\bHttp::(?P<method>get|post|put|patch|delete|head|send)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_HTTP_FACADE_CHAIN_RE = re.compile(
    r"\bHttp::(?P<prefix>withToken|withHeaders|acceptJson|asJson|timeout|retry|baseUrl)\s*\([^;]*?\)"
    r"\s*->\s*(?P<method>get|post|put|patch|delete|head|send)\s*\(",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
PHP_STORAGE_FACADE_RE = re.compile(
    r"\bStorage::(?P<method>get|put|prepend|append|exists|missing|delete|url|temporaryUrl|path|download|files|allFiles|directories|makeDirectory|deleteDirectory)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_STORAGE_DISK_CHAIN_RE = re.compile(
    r"\bStorage::(?P<selector>disk|cloud)\s*\((?P<selector_args>[^)]*)\)\s*->\s*"
    r"(?P<method>get|put|prepend|append|exists|missing|delete|url|temporaryUrl|path|download|files|allFiles|directories|makeDirectory|deleteDirectory)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_REQUEST_INPUT_CHAIN_RE = re.compile(
    r"(?P<receiver>request\s*\(\s*\)|\$[A-Za-z_][A-Za-z0-9_]*)\s*->\s*"
    r"(?P<method>input|get|query|header|cookie|route)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_REQUEST_FILE_CHAIN_RE = re.compile(
    r"(?P<receiver>request\s*\(\s*\)|\$[A-Za-z_][A-Za-z0-9_]*)\s*->\s*"
    r"(?P<method>file|hasFile)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_REQUEST_HELPER_RE = re.compile(r"\brequest\s*\(", re.IGNORECASE | re.MULTILINE)
PHP_INSTANCE_METHOD_CALL_RE = re.compile(
    r"(?P<receiver>\$this->[A-Za-z_][A-Za-z0-9_]*|\$[A-Za-z_][A-Za-z0-9_]*)\s*->\s*"
    r"(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*\(",
    re.MULTILINE,
)
PHP_MODEL_INSTANCE_OPERATION_ACCESS = {
    "decrement": "write",
    "delete": "delete",
    "forceDelete": "delete",
    "increment": "write",
    "push": "write",
    "restore": "restore",
    "save": "write",
    "touch": "write",
    "update": "write",
}
PHP_LISTEN_ARRAY_RE = re.compile(
    r"(?P<event>\\?[A-Za-z0-9_\\]+)::class\s*=>\s*\[(?P<listeners>.*?)\]",
    re.DOTALL,
)
PHP_CLASS_CONST_RE = re.compile(r"(?P<class>\\?[A-Za-z0-9_\\]+)::class")
PHP_DISPATCH_JOB_RE = re.compile(
    r"\b(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)::(?P<method>dispatch(?:Sync|AfterResponse)?)\s*\("
)
PHP_EVENT_FUNCTION_RE = re.compile(r"\bevent\s*\(\s*new\s+(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s*\(")
PHP_EVENT_DISPATCH_RE = re.compile(
    r"\b(?:Event::dispatch|event)\s*\(\s*(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)::class"
)
PHP_MAIL_CHAIN_RE = re.compile(
    r"\bMail::(?:to|cc|bcc)\s*\([^;]*?->\s*(?P<method>send|queue|later)\s*\(\s*new\s+"
    r"(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s*\(",
    re.DOTALL,
)
PHP_MAIL_DIRECT_RE = re.compile(
    r"\bMail::(?P<method>send|queue|later)\s*\(\s*new\s+(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s*\(",
    re.DOTALL,
)
PHP_NOTIFY_NEW_RE = re.compile(
    r"->\s*notify\s*\(\s*new\s+(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s*\(",
    re.DOTALL,
)
PHP_NOTIFICATION_SEND_RE = re.compile(
    r"\bNotification::(?P<method>send|sendNow)\s*\([^;]*?,\s*new\s+"
    r"(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s*\(",
    re.DOTALL,
)
PHP_THROW_NEW_RE = re.compile(r"\bthrow\s+new\s+(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)\s*\(")
PHP_COMMAND_SIGNATURE_RE = re.compile(r"\bprotected\s+\$signature\s*=\s*['\"](?P<signature>[^'\"]+)['\"]")
PHP_SCHEDULE_COMMAND_RE = re.compile(
    r"\$schedule->command\s*\(\s*['\"](?P<command>[^'\"]+)['\"]\s*\)(?P<chain>(?:\s*->[A-Za-z_][A-Za-z0-9_]*\([^)]*\))*)",
    re.DOTALL,
)
PHP_SCHEDULE_JOB_RE = re.compile(
    r"\$schedule->job\s*\(\s*(?:new\s+)?(?P<class>\\?[A-Z][A-Za-z0-9_\\]+)(?:\([^)]*\))?\s*\)"
    r"(?P<chain>(?:\s*->[A-Za-z_][A-Za-z0-9_]*\([^)]*\))*)",
    re.DOTALL,
)
PHP_SCHEDULE_CADENCE_RE = re.compile(r"->(?P<cadence>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
PHP_DB_TABLE_RE = re.compile(r"\bDB::table\s*\(\s*['\"](?P<table>[^'\"]+)['\"]")
PHP_DB_TRANSACTION_RE = re.compile(
    r"\bDB::(?P<method>transaction|beginTransaction|commit|rollBack)\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
PHP_QUERY_FROM_RE = re.compile(r"->from\s*\(\s*['\"](?P<table>[^'\"]+)['\"]")
PHP_QUERY_JOIN_RE = re.compile(r"->(?P<method>join|leftJoin|rightJoin|crossJoin)\s*\(\s*['\"](?P<table>[^'\"]+)['\"]")
PHP_QUERY_CHAIN_CALL_RE = re.compile(r"->(?P<method>[A-Za-z_][A-Za-z0-9_]*)\s*\(")
PHP_QUERY_READ_TERMINALS = {
    "all",
    "chunk",
    "count",
    "cursor",
    "doesntexist",
    "exists",
    "find",
    "first",
    "firstorfail",
    "get",
    "lazy",
    "paginate",
    "pluck",
    "simplepaginate",
    "value",
}
PHP_QUERY_WRITE_TERMINALS = {
    "create",
    "decrement",
    "delete",
    "firstorcreate",
    "forcedelete",
    "forcecreate",
    "increment",
    "insert",
    "insertgetid",
    "insertorignore",
    "restore",
    "truncate",
    "update",
    "updateorcreate",
    "updateorinsert",
    "upsert",
}
PHP_QUERY_TABLE_METHODS = {"from", "join", "leftjoin", "rightjoin", "crossjoin"}
PHP_QUERY_SCOPE_METHODS = {
    "onlytrashed",
    "withtrashed",
    "withoutglobalscope",
    "withoutglobalscopes",
    "withouttrashed",
}
PHP_QUERY_LOCK_METHODS = {"lock", "lockforupdate", "sharedlock"}
PHP_QUERY_SHAPE_METHODS = {
    "addselect",
    "groupby",
    "having",
    "limit",
    "orderby",
    "offset",
    "query",
    "select",
    "with",
}
PHP_QUERY_FILTER_METHODS = {
    "doesnthave",
    "has",
    "orwhere",
    "where",
    "wherebetween",
    "wherehas",
    "wherein",
    "wherenotbetween",
    "wherenotin",
    "wherenotnull",
    "wherenull",
}
PHP_QUERY_TRACKED_METHODS = (
    PHP_QUERY_READ_TERMINALS
    | PHP_QUERY_WRITE_TERMINALS
    | PHP_QUERY_TABLE_METHODS
    | PHP_QUERY_SCOPE_METHODS
    | PHP_QUERY_LOCK_METHODS
    | PHP_QUERY_SHAPE_METHODS
    | PHP_QUERY_FILTER_METHODS
)
PHP_CONTAINER_BIND_RE = re.compile(
    r"(?:\$this->app->|app\(\)->|App::)(?P<method>bind|singleton|scoped|instance)\s*"
    r"\(\s*(?P<abstract>\\?[A-Za-z0-9_\\]+)::class\s*,\s*"
    r"(?P<concrete>\\?[A-Za-z0-9_\\]+)::class",
    re.MULTILINE,
)
PHP_OBSERVER_RE = re.compile(
    r"(?P<model>\\?[A-Za-z0-9_\\]+)::observe\s*\(\s*(?P<observer>\\?[A-Za-z0-9_\\]+)::class\s*\)",
    re.MULTILINE,
)
PHP_OBSERVER_LIFECYCLE_METHODS = {
    "retrieved",
    "creating",
    "created",
    "updating",
    "updated",
    "saving",
    "saved",
    "deleting",
    "deleted",
    "trashed",
    "forceDeleting",
    "forceDeleted",
    "restoring",
    "restored",
    "replicating",
}
PHP_VIEW_FUNCTION_RE = re.compile(r"\bview\s*\(\s*['\"](?P<view>[^'\"]+)['\"]", re.MULTILINE)
PHP_VIEW_MAKE_RE = re.compile(r"\bView::make\s*\(\s*['\"](?P<view>[^'\"]+)['\"]", re.MULTILINE)
PHP_INERTIA_RENDER_RE = re.compile(r"\bInertia::render\s*\(\s*['\"](?P<view>[^'\"]+)['\"]", re.MULTILINE)
PHP_BROADCAST_CHANNEL_RE = re.compile(
    r"\bBroadcast::channel\s*\(\s*['\"](?P<channel>[^'\"]+)['\"]\s*,\s*(?P<handler>.*?)\)",
    re.DOTALL,
)
PHP_SYMFONY_ROUTE_ATTRIBUTE_RE = re.compile(
    r"#\[\s*(?:[A-Za-z0-9_\\]+\\)?Route\s*\((?P<args>.*?)\)\s*\]",
    re.DOTALL,
)
PHP_SYMFONY_ROUTE_ANNOTATION_RE = re.compile(
    r"@\s*(?:[A-Za-z0-9_\\]+\\)?Route\s*\((?P<args>.*?)\)",
    re.DOTALL,
)
PHP_DOCBLOCK_RE = re.compile(r"/\*\*(?P<body>.*?)\*/", re.DOTALL)
PHP_NAMED_ROUTE_ARG_RE = re.compile(r"\b(?P<name>path|name)\s*[:=]\s*['\"](?P<value>[^'\"]+)['\"]")
PHP_ROUTE_METHODS_ARG_RE = re.compile(
    r"\bmethods\s*[:=]\s*(?P<value>\[[^\]]*\]|\{[^}]*\}|['\"][^'\"]+['\"])",
    re.DOTALL,
)
PHP_ATTRIBUTE_RE = re.compile(
    r"#\[\s*(?P<name>[A-Za-z0-9_\\]+)\s*(?:\((?P<args>.*?)\))?\s*\]",
    re.DOTALL,
)
PHP_NAMED_ATTR_STRING_RE = re.compile(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*['\"](?P<value>[^'\"]+)['\"]")
PHP_NAMED_ATTR_BOOL_RE = re.compile(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>true|false)", re.IGNORECASE)
PHP_NAMED_ATTR_INT_RE = re.compile(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>\d+)")
PHP_NAMED_ATTR_CLASS_RE = re.compile(r"\b(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>\\?[A-Za-z0-9_\\]+)::class")
BLADE_EXTENDS_RE = re.compile(r"@extends\s*\(\s*['\"](?P<view>[^'\"]+)['\"]", re.MULTILINE)
BLADE_INCLUDE_RE = re.compile(
    r"@(?:include|includeIf|each)\s*\(\s*['\"](?P<view>[^'\"]+)['\"]",
    re.MULTILINE,
)
BLADE_CONDITIONAL_INCLUDE_RE = re.compile(
    r"@(?:includeWhen|includeUnless)\s*\(\s*[^,]+,\s*['\"](?P<view>[^'\"]+)['\"]",
    re.MULTILINE,
)
BLADE_COMPONENT_DIRECTIVE_RE = re.compile(r"@component\s*\(\s*['\"](?P<component>[^'\"]+)['\"]", re.MULTILINE)
BLADE_ANONYMOUS_COMPONENT_RE = re.compile(r"<x[-:](?P<component>[A-Za-z0-9_.:-]+)\b", re.MULTILINE)
BLADE_LIVEWIRE_RE = re.compile(
    r"(?:@livewire\s*\(\s*['\"](?P<directive>[^'\"]+)['\"]|<livewire:(?P<tag>[A-Za-z0-9_.:-]+)\b)",
    re.MULTILINE,
)
BLADE_ROUTE_FUNCTION_RE = re.compile(r"\broute\s*\(\s*['\"](?P<route>[^'\"]+)['\"]", re.MULTILINE)
BLADE_ROUTE_ARRAY_PARAMS_RE = re.compile(
    r"\broute\s*\(\s*['\"](?P<route>[^'\"]+)['\"]\s*,\s*\[(?P<params>.{0,512}?)\]\s*\)",
    re.MULTILINE | re.DOTALL,
)
BLADE_ROUTE_PARAM_KEY_RE = re.compile(r"['\"](?P<param>[A-Za-z_][A-Za-z0-9_]*)['\"]\s*=>")
BLADE_FORM_METHOD_RE = re.compile(r"@method\s*\(\s*['\"](?P<method>GET|POST|PUT|PATCH|DELETE)['\"]\s*\)", re.IGNORECASE | re.MULTILINE)
BLADE_CSRF_RE = re.compile(r"(?:@csrf\b|csrf_field\s*\(\s*\))", re.MULTILINE)
BLADE_FORM_BLOCK_RE = re.compile(r"<form\b(?P<attrs>[^>]*)>(?P<body>.*?)</form>", re.IGNORECASE | re.DOTALL)
BLADE_FORM_HTML_METHOD_RE = re.compile(r"\bmethod\s*=\s*['\"](?P<method>GET|POST)['\"]", re.IGNORECASE)
BLADE_AUTHORIZATION_RE = re.compile(
    r"@(?P<helper>can|cannot|elsecan|elsecannot)\s*\(\s*['\"](?P<ability>[A-Za-z0-9_.:-]{1,128})['\"]",
    re.MULTILINE,
)
BLADE_FORM_FIELD_RE = re.compile(
    r"<(?P<tag>input|select|textarea)\b(?P<attrs>[^>]*)\bname\s*=\s*['\"](?P<field>[A-Za-z0-9_.*:-]{1,128})['\"]",
    re.IGNORECASE | re.MULTILINE,
)
BLADE_OLD_INPUT_RE = re.compile(r"\bold\s*\(\s*['\"](?P<field>[A-Za-z0-9_.*:-]{1,128})['\"]", re.MULTILINE)
BLADE_ERROR_DIRECTIVE_RE = re.compile(r"@error\s*\(\s*['\"](?P<field>[A-Za-z0-9_.*:-]{1,128})['\"]", re.MULTILINE)
BLADE_WIRE_MODEL_RE = re.compile(
    r"\bwire:model(?P<modifiers>(?:\.[A-Za-z0-9_-]{1,32}){0,6})\s*=\s*['\"](?P<model>[A-Za-z0-9_.*:-]{1,128})['\"]",
    re.IGNORECASE | re.MULTILINE,
)
BLADE_ALPINE_MODEL_RE = re.compile(
    r"\bx-model(?P<modifiers>(?:\.[A-Za-z0-9_-]{1,32}){0,6})\s*=\s*['\"](?P<model>\$?[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*){0,8})['\"]",
    re.IGNORECASE | re.MULTILINE,
)
BLADE_WIRE_ACTION_RE = re.compile(
    r"\bwire:(?P<event>click|submit|change|keydown|keyup|blur|focus)"
    r"(?P<modifiers>(?:\.[A-Za-z0-9_-]{1,32}){0,6})\s*=\s*['\"](?P<action>[A-Za-z_][A-Za-z0-9_:-]{0,127})(?:\s*\([^'\"]{0,256}\))?['\"]",
    re.IGNORECASE | re.MULTILINE,
)
PHP_ELOQUENT_QUERY_METHODS = {
    "all",
    "count",
    "create",
    "delete",
    "doesntHave",
    "exists",
    "find",
    "first",
    "firstOrFail",
    "firstOrCreate",
    "forceDelete",
    "get",
    "has",
    "lock",
    "lockForUpdate",
    "onlyTrashed",
    "pluck",
    "query",
    "restore",
    "sharedLock",
    "update",
    "updateOrCreate",
    "value",
    "where",
    "whereHas",
    "withTrashed",
    "withoutGlobalScope",
    "withoutGlobalScopes",
    "withoutTrashed",
    "with",
}
PHP_SCHEMA_ACTION_RE = re.compile(
    r"\bSchema::(?P<action>create|table|drop|dropIfExists)\s*\(\s*['\"](?P<table>[^'\"]+)['\"]",
    re.IGNORECASE | re.MULTILINE,
)
PHP_TABLE_CALL_RE = re.compile(
    r"\$table->(?P<type>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<args>[^)]*)\)(?P<chain>(?:\s*->[A-Za-z_][A-Za-z0-9_]*\([^)]*\))*)",
    re.MULTILINE,
)
TS_IMPORT_RE = re.compile(r"\bimport(?:\s+type)?(?:\s+[^;]*?\s+from)?\s+['\"](?P<target>[^'\"]+)['\"]", re.MULTILINE)
TS_EXPORT_DECL_RE = re.compile(
    r"\bexport\s+(?:default\s+)?(?:(?:async\s+)?function|class|const|let|var)\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)",
    re.MULTILINE,
)
TS_FUNCTION_RE = re.compile(r"\b(?:async\s+)?function\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(", re.MULTILINE)
TS_ARROW_COMPONENT_RE = re.compile(
    r"\b(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Z][A-Za-z0-9_$]*)\s*=\s*(?:\([^)]*\)|[A-Za-z_$][A-Za-z0-9_$]*)\s*=>",
    re.MULTILINE,
)
TS_CLASS_RE = re.compile(r"\bclass\s+(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)", re.MULTILINE)
TS_LOG_CALL_RE = re.compile(
    r"\b(?P<logger>console|logger|log)\s*\.\s*(?P<level>debug|info|warn|warning|error|exception|critical|log)\s*"
    r"\(\s*(?P<quote>['\"])(?P<message>(?:\\.|(?! (?P=quote)).)*?)(?P=quote)",
    re.MULTILINE | re.DOTALL | re.VERBOSE,
)
EXPRESS_ROUTE_RE = re.compile(
    r"\b(?P<router>app|router)\s*\.\s*(?P<method>get|post|put|patch|delete|options|all|use)\s*"
    r"\(\s*['\"](?P<path>[^'\"]+)['\"]\s*,\s*(?P<handler>[A-Za-z_$][A-Za-z0-9_.$]*)?",
    re.IGNORECASE | re.MULTILINE,
)
DRIZZLE_TABLE_RE = re.compile(
    r"(?:export\s+)?(?:const|let|var)\s+(?P<var>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"(?P<fn>pgTable|mysqlTable|sqliteTable)\s*\(\s*['\"](?P<table>[^'\"]+)['\"]\s*,\s*\{",
    re.MULTILINE,
)
DRIZZLE_FIELD_RE = re.compile(r"^\s*(?P<field>[A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*(?P<expr>.+)$", re.DOTALL)
DRIZZLE_COLUMN_RE = re.compile(r"(?P<type>[A-Za-z_$][A-Za-z0-9_$]*)\s*\(\s*['\"](?P<column>[^'\"]+)['\"]", re.DOTALL)
DRIZZLE_REFERENCES_RE = re.compile(
    r"\.references\s*\(\s*\(\s*\)\s*=>\s*(?P<table>[A-Za-z_$][A-Za-z0-9_$]*)\.(?P<column>[A-Za-z_$][A-Za-z0-9_$]*)",
    re.DOTALL,
)
PRISMA_MODEL_RE = re.compile(r"^model\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\{(?P<body>.*?)^\}", re.MULTILINE | re.DOTALL)
PRISMA_FIELD_RE = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s+(?P<type>[A-Za-z_][A-Za-z0-9_]*(?:\[\])?\??)(?P<attrs>.*)$", re.MULTILINE)
PRISMA_MAP_RE = re.compile(r"@@map\(\s*['\"](?P<table>[^'\"]+)['\"]\s*\)")
PRISMA_RELATION_RE = re.compile(r"@relation\((?P<body>[^)]*)\)")
PRISMA_LIST_ARG_RE = re.compile(r"\b(?P<name>fields|references)\s*:\s*\[(?P<values>[^\]]+)\]")
PRISMA_SCALAR_TYPES = {"String", "Boolean", "Int", "BigInt", "Float", "Decimal", "DateTime", "Json", "Bytes"}
SQL_CREATE_TABLE_RE = re.compile(
    r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(?P<table>[`\"A-Za-z0-9_.]+)\s*\((?P<body>.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)
SQL_INLINE_REFERENCE_RE = re.compile(
    r"\bREFERENCES\s+(?P<table>[`\"A-Za-z0-9_.]+)\s*\(\s*(?P<column>[`\"A-Za-z0-9_]+)\s*\)",
    re.IGNORECASE,
)
SQL_TABLE_FOREIGN_KEY_RE = re.compile(
    r"\bFOREIGN\s+KEY\s*\(\s*(?P<column>[`\"A-Za-z0-9_]+)\s*\)\s*REFERENCES\s+"
    r"(?P<table>[`\"A-Za-z0-9_.]+)\s*\(\s*(?P<ref_column>[`\"A-Za-z0-9_]+)\s*\)",
    re.IGNORECASE,
)
NEXT_ROUTE_FILE_RE = re.compile(r"(?:^|/)app/(?P<route>.+)/route\.(?:ts|tsx|js|jsx)$")
NEXT_PAGE_FILE_RE = re.compile(r"(?:^|/)app/(?P<route>.+)/page\.(?:ts|tsx|js|jsx)$")
NEXT_HTTP_EXPORT_RE = re.compile(r"\bexport\s+(?:async\s+)?function\s+(?P<method>GET|POST|PUT|PATCH|DELETE|OPTIONS)\s*\(")
PHP_TEST_METHOD_RE = re.compile(r"\bfunction\s+(?P<name>test[A-Za-z0-9_]*|it_[A-Za-z0-9_]+)\s*\(")
PY_TEST_FUNCTION_RE = re.compile(r"\b(?:async\s+)?def\s+(?P<name>test_[A-Za-z0-9_]+)\s*\(")
JS_TEST_CALL_RE = re.compile(r"\b(?:it|test)\s*\(")
PY_IMPORT_LINE_RE = re.compile(r"^\s*(?:from\s+(?P<from>[A-Za-z0-9_.]+)\s+import|import\s+(?P<import>[A-Za-z0-9_., ]+))", re.MULTILINE)
PY_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "api_route", "route"}
PY_DJANGO_ROUTE_FUNCS = {"path", "re_path"}
PY_DJANGO_RELATION_FIELDS = {"ForeignKey", "OneToOneField", "ManyToManyField"}
PY_SQLALCHEMY_COLUMN_CALLS = {"Column", "mapped_column"}
TEST_FILE_SUFFIXES = {".php", ".py", ".js", ".jsx", ".ts", ".tsx"}
MAX_TEST_FILES = 500
MAX_TEST_CASES_PER_FILE = 50
MAX_TEST_REFS_PER_FILE = 25
MAX_LOG_EVENTS = 500
PY_LOG_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}


def _safe_relpath(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("/")


def _resolve_inside(root: Path, rel: str) -> Path:
    candidate = (root / rel).resolve()
    root_resolved = root.resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace: {rel}") from exc
    return candidate


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _io_error_reason(prefix: str, exc: OSError) -> str:
    errno = getattr(exc, "errno", None)
    return f"{prefix}:{errno}" if errno is not None else prefix


def _safe_exception_reason(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return _io_error_reason("read_error", exc)
    if isinstance(exc, ValueError):
        return str(exc)
    return exc.__class__.__name__


def _read_text_bounded(path: Path, max_bytes: int) -> tuple[str, bool, str]:
    limit = max(0, max_bytes)
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    truncated = len(raw) > limit or path.stat().st_size > limit
    bounded = raw[:limit]
    digest = _hash_bytes(bounded)
    return bounded.decode("utf-8", errors="replace"), truncated, digest


def _read_lines(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return handle.readlines()


def _gitignore_spec(root: Path):
    ignore_file = root / ".gitignore"
    if not ignore_file.is_file():
        return None
    try:
        import pathspec

        return pathspec.PathSpec.from_lines("gitignore", _read_lines(ignore_file))
    except Exception:
        return None


def _skip_file_reason(path: Path, rel: str, ignore_spec=None) -> str | None:
    if path.is_symlink():
        return "symlink"
    name = path.name
    lowered = name.lower()
    if lowered in SECRET_FILE_NAMES or lowered.startswith(".env."):
        return "sensitive_name"
    if lowered.startswith("secret") or lowered.startswith("secrets."):
        return "sensitive_name"
    if path.suffix.lower() in SECRET_SUFFIXES:
        return "sensitive_suffix"
    if path.suffix.lower() in BINARY_SUFFIXES:
        return "binary_or_archive"
    if ignore_spec is not None and ignore_spec.match_file(rel):
        return "gitignored"
    return None


def _language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return LANGUAGE_SUFFIXES.get(suffix, "other")


def _language_counts(files: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for item in files:
        language = _language_for_path(str(item.get("path") or ""))
        current = counts.setdefault(language, {"files": 0, "bytes": 0})
        current["files"] += 1
        current["bytes"] += int(item.get("bytes") or 0)
    return {key: counts[key] for key in sorted(counts)}


def _dependency_packages_from_json(path: Path, manager: str) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    packages: set[str] = set()
    if manager == "composer":
        for section in ("require", "require-dev"):
            values = data.get(section)
            if isinstance(values, dict):
                packages.update(str(name) for name in values if str(name).lower() != "php")
    elif manager == "npm":
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            values = data.get(section)
            if isinstance(values, dict):
                packages.update(str(name) for name in values)
    return sorted(packages)


def _dependency_packages_from_pyproject(path: Path) -> list[str]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    packages: set[str] = set()
    project = data.get("project")
    if isinstance(project, dict):
        dependencies = project.get("dependencies")
        if isinstance(dependencies, list):
            packages.update(str(dep).split(";", 1)[0].split("[", 1)[0].split("=", 1)[0].strip() for dep in dependencies)
        optional = project.get("optional-dependencies")
        if isinstance(optional, dict):
            for values in optional.values():
                if isinstance(values, list):
                    packages.update(str(dep).split(";", 1)[0].split("[", 1)[0].split("=", 1)[0].strip() for dep in values)
    tool = data.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict):
        for section in ("dependencies", "dev-dependencies"):
            values = poetry.get(section)
            if isinstance(values, dict):
                packages.update(str(name) for name in values if str(name).lower() != "python")
    return sorted(pkg for pkg in packages if pkg)


def _dependency_packages_from_requirements(path: Path) -> list[str]:
    packages: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        clean = line.strip()
        if not clean or clean.startswith("#") or clean.startswith(("-", "git+")):
            continue
        packages.add(re.split(r"[<>=~!;\[]", clean, maxsplit=1)[0].strip())
    return sorted(pkg for pkg in packages if pkg)


def _dependency_manifests(root: Path, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for item in files:
        rel = str(item.get("path") or "")
        name = Path(rel).name
        manager = DEPENDENCY_MANIFESTS.get(name)
        if manager is None:
            continue
        path = root / rel
        try:
            if name in {"composer.json", "package.json"}:
                packages = _dependency_packages_from_json(path, manager)
            elif name == "pyproject.toml":
                packages = _dependency_packages_from_pyproject(path)
            else:
                packages = _dependency_packages_from_requirements(path)
        except Exception:
            packages = []
        manifests.append({"manager": manager, "path": rel, "packages": packages[:200]})
    return sorted(manifests, key=lambda item: (item["manager"], item["path"]))


def _is_test_path(rel: str) -> bool:
    path = _safe_relpath(rel)
    lowered = path.lower()
    suffix = Path(path).suffix.lower()
    if suffix not in TEST_FILE_SUFFIXES:
        return False
    parts = lowered.split("/")
    if any(part in {"tests", "test", "spec", "__tests__", "__specs__"} for part in parts):
        return True
    stem = Path(lowered).stem
    return (
        stem.startswith("test_")
        or stem.startswith("test-")
        or stem.endswith("_test")
        or stem.endswith("-test")
        or stem.endswith(".test")
        or stem.endswith(".spec")
        or stem.endswith("test")
        or stem.endswith("spec")
    )


def _test_framework_for_path(rel: str) -> str:
    path = _safe_relpath(rel)
    suffix = Path(path).suffix.lower()
    lowered = path.lower()
    if suffix == ".php":
        return "phpunit"
    if suffix == ".py":
        return "pytest"
    if "/cypress/" in lowered or lowered.startswith("cypress/"):
        return "cypress"
    if "/playwright/" in lowered or lowered.startswith("playwright/"):
        return "playwright"
    return "js_test"


def _test_cases_from_source(source: str, rel: str) -> list[dict[str, Any]]:
    suffix = Path(rel).suffix.lower()
    cases: list[dict[str, Any]] = []
    if suffix == ".php":
        pattern = PHP_TEST_METHOD_RE
    elif suffix == ".py":
        pattern = PY_TEST_FUNCTION_RE
    else:
        pattern = JS_TEST_CALL_RE
    for index, match in enumerate(pattern.finditer(source)):
        if len(cases) >= MAX_TEST_CASES_PER_FILE:
            break
        name = match.groupdict().get("name") or f"test@{_line_number(source, match.start())}"
        cases.append(
            {
                "name": str(name)[:120],
                "line": _line_number(source, match.start()),
                "ordinal": index + 1,
            }
        )
    return cases


def _test_import_refs(source: str, rel: str) -> list[dict[str, Any]]:
    suffix = Path(rel).suffix.lower()
    refs: list[dict[str, Any]] = []
    if suffix == ".php":
        for match in PHP_USE_RE.finditer(source):
            refs.append({"target": match.group("class").strip("\\"), "line": _line_number(source, match.start())})
    elif suffix == ".py":
        for match in PY_IMPORT_LINE_RE.finditer(source):
            raw = match.group("from") or match.group("import") or ""
            for target in raw.split(","):
                clean = target.strip().split(" ", 1)[0]
                if clean:
                    refs.append({"target": clean, "line": _line_number(source, match.start())})
    else:
        for match in TS_IMPORT_RE.finditer(source):
            refs.append({"target": match.group("target"), "line": _line_number(source, match.start())})
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in refs:
        target = str(ref.get("target") or "").strip()
        if not target or target in seen:
            continue
        seen.add(target)
        deduped.append(ref)
        if len(deduped) >= MAX_TEST_REFS_PER_FILE:
            break
    return deduped


def _test_target_candidates_from_path(rel: str) -> list[str]:
    path = _safe_relpath(rel)
    name = Path(path).name
    stem = name
    for suffix in (".test.tsx", ".test.ts", ".test.jsx", ".test.js", ".spec.tsx", ".spec.ts", ".spec.jsx", ".spec.js"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    if stem == name:
        stem = Path(path).stem
    candidates = {stem}
    for prefix in ("test_", "test-"):
        if stem.lower().startswith(prefix):
            candidates.add(stem[len(prefix) :])
    for suffix in ("Test", "Tests", "_test", "-test", ".test", ".spec", "Spec"):
        if stem.endswith(suffix):
            candidates.add(stem[: -len(suffix)])
    parent = Path(path).parent.name
    if parent and parent not in {"tests", "test", "spec", "__tests__", "__specs__"}:
        candidates.add(parent)
    return sorted(candidate for candidate in candidates if candidate)


def _match_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _symbol_refs_for_test(candidates: list[str], symbols: list[dict[str, Any]], *, test_path: str) -> list[str]:
    candidate_keys = {_match_key(candidate) for candidate in candidates if _match_key(candidate)}
    refs: list[str] = []
    seen: set[str] = set()
    for symbol in symbols:
        name = str(symbol.get("name") or "").strip()
        if not name:
            continue
        if str(symbol.get("path") or "") == test_path:
            continue
        values = [
            name,
            symbol.get("short_name"),
            symbol.get("class"),
            symbol.get("method"),
            Path(str(symbol.get("path") or "")).stem,
        ]
        symbol_keys = {_match_key(value) for value in values if _match_key(value)}
        if not any(
            candidate == symbol_key
            or (len(candidate) >= 4 and candidate in symbol_key)
            for candidate in candidate_keys
            for symbol_key in symbol_keys
        ):
            continue
        if name in seen:
            continue
        seen.add(name)
        refs.append(name)
        if len(refs) >= MAX_TEST_REFS_PER_FILE:
            break
    return refs


def _route_ref(route: dict[str, Any]) -> str:
    name = str(route.get("name") or "").strip()
    if name:
        return f"route:{name}"
    route_path = str(route.get("uri") or route.get("path") or "").strip()
    method = str(route.get("method") or "").strip()
    return f"route:{method} {route_path}".strip()


def _route_refs_for_test(source: str, routes: list[dict[str, Any]]) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for route in routes:
        route_path = str(route.get("uri") or route.get("path") or "").strip()
        ref = _route_ref(route)
        if not route_path or not ref or route_path not in source or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
        if len(refs) >= MAX_TEST_REFS_PER_FILE:
            break
    return refs


def _build_test_map(
    workspace_root: Path,
    candidates: list[Path],
    routes: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
    max_file_bytes: int,
) -> tuple[dict[str, Any], bool]:
    files: list[dict[str, Any]] = []
    truncated = False
    for path in candidates:
        rel = path.relative_to(workspace_root).as_posix()
        if not _is_test_path(rel):
            continue
        if len(files) >= MAX_TEST_FILES:
            truncated = True
            break
        try:
            if path.stat().st_size > max_file_bytes:
                truncated = True
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                truncated = True
                continue
        except OSError:
            truncated = True
            continue

        cases = _test_cases_from_source(source, rel)
        import_refs = _test_import_refs(source, rel)
        target_candidates = _test_target_candidates_from_path(rel)
        symbol_refs = _symbol_refs_for_test(target_candidates, symbols, test_path=rel)
        route_refs = _route_refs_for_test(source, routes)
        test_node = f"test:{rel}"
        for ref in symbol_refs:
            truncated = not _edge_append(
                edges,
                {"kind": "test_covers_symbol", "from": test_node, "to": ref, "path": rel},
                max_edges=max_edges,
            ) or truncated
        for ref in route_refs:
            truncated = not _edge_append(
                edges,
                {"kind": "test_covers_route", "from": test_node, "to": ref, "path": rel},
                max_edges=max_edges,
            ) or truncated
        for ref in import_refs:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "test_imports",
                    "from": test_node,
                    "to": str(ref.get("target") or ""),
                    "path": rel,
                    "line": ref.get("line"),
                },
                max_edges=max_edges,
            ) or truncated

        files.append(
            {
                "path": rel,
                "language": _language_for_path(rel),
                "framework": _test_framework_for_path(rel),
                "test_count": len(cases),
                "cases": cases,
                "target_candidates": target_candidates[:MAX_TEST_REFS_PER_FILE],
                "symbol_refs": symbol_refs,
                "route_refs": route_refs,
                "import_count": len(import_refs),
            }
        )
    return {
        "schema": "hades.test_map.v1",
        "file_count": len(files),
        "files": files,
        "truncated": truncated,
        "raw_source_included": False,
    }, truncated


def _normalize_laravel_handler(raw: str) -> str:
    compact = " ".join(str(raw or "").split())
    match = LARAVEL_HANDLER_RE.search(compact)
    if match:
        class_name = match.group("class").split("\\")[-1]
        return f"{class_name}@{match.group('method')}"
    return compact[:160]


def _laravel_resource_param(resource: str) -> str:
    tail = str(resource or "").strip("/").split("/")[-1].replace("-", "_")
    if tail.endswith("ies") and len(tail) > 3:
        return tail[:-3] + "y"
    if tail.endswith("s") and len(tail) > 1:
        return tail[:-1]
    return tail or "id"


def _laravel_resource_routes(
    *,
    resource: str,
    controller: str,
    api: bool,
    rel: str,
    line: int,
    chain: str,
) -> list[dict[str, Any]]:
    base_uri = "/" + str(resource or "").strip("/")
    route_name = base_uri.strip("/").replace("/", ".")
    param = _laravel_resource_param(resource)
    actions = [
        ("GET", base_uri, "index"),
        ("POST", base_uri, "store"),
        ("GET", f"{base_uri}/{{{param}}}", "show"),
        ("PUT", f"{base_uri}/{{{param}}}", "update"),
        ("PATCH", f"{base_uri}/{{{param}}}", "update"),
        ("DELETE", f"{base_uri}/{{{param}}}", "destroy"),
    ]
    if not api:
        actions.insert(2, ("GET", f"{base_uri}/create", "create"))
        actions.insert(4, ("GET", f"{base_uri}/{{{param}}}/edit", "edit"))
    middleware = _route_middleware_values(chain)
    routes: list[dict[str, Any]] = []
    controller_short = _php_short_name(controller)
    for method, uri, action in actions:
        route = {
            "framework": "laravel",
            "method": method,
            "uri": uri,
            "handler": f"{controller_short}@{action}",
            "path": rel,
            "line": line,
            "name": f"{route_name}.{action}",
            "resource": resource,
            "resource_action": action,
        }
        if middleware:
            route["middleware"] = middleware
        routes.append(route)
    return routes


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, max(0, offset)) + 1


def _php_namespace(content: str) -> str:
    match = PHP_NAMESPACE_RE.search(content)
    return match.group("namespace") if match else ""


def _php_use_map(content: str) -> dict[str, str]:
    uses: dict[str, str] = {}
    for match in PHP_USE_RE.finditer(content):
        fqcn = match.group("class").strip("\\")
        alias = match.group("alias") or _php_short_name(fqcn)
        uses[alias] = fqcn
    return uses


def _php_fqcn(namespace: str, name: str) -> str:
    clean = name.strip("\\")
    if "\\" in clean or namespace == "":
        return clean
    return f"{namespace}\\{clean}"


def _php_fqcn_resolved(namespace: str, name: str, uses: dict[str, str]) -> str:
    clean = name.strip("\\")
    if "\\" in clean:
        return clean
    return uses.get(clean) or _php_fqcn(namespace, clean)


def _php_resolved_simple_type(namespace: str, type_name: str, uses: dict[str, str]) -> str:
    clean = type_name.strip().lstrip("?").strip("\\")
    if not clean or "|" in clean:
        return ""
    if not _php_short_name(clean)[:1].isupper():
        return ""
    return _php_fqcn_resolved(namespace, clean, uses)


def _php_short_name(name: str) -> str:
    return name.strip("\\").split("\\")[-1]


def _php_context_id(class_info: dict[str, Any] | None, rel: str) -> str:
    return str(class_info["name"]) if class_info else rel


def _php_method_context_id(source: str, classes: list[dict[str, Any]], offset: int, rel: str) -> str:
    class_info = _class_context(classes, offset)
    if class_info is None:
        return rel
    class_offset = int(class_info["offset"])
    method_name = ""
    for match in PHP_METHOD_RE.finditer(source):
        if match.start() < class_offset:
            continue
        if match.start() > offset:
            break
        method_class = _class_context(classes, match.start())
        if method_class and method_class.get("name") == class_info.get("name"):
            method_name = match.group("name")
    if method_name:
        return f"{_php_short_name(str(class_info['name']))}@{method_name}"
    return _php_context_id(class_info, rel)


def _php_route_id(route: dict[str, Any]) -> str:
    return str(route.get("name") or f"{route.get('method', '')} {route.get('uri', '')}".strip())


def _php_route_params(route: dict[str, Any]) -> set[str]:
    uri = str(route.get("uri") or "")
    return {match.group("name") for match in PHP_ROUTE_PARAM_RE.finditer(uri)}


def _php_required_route_params(route: dict[str, Any]) -> set[str]:
    uri = str(route.get("uri") or "")
    required: set[str] = set()
    for match in PHP_ROUTE_PARAM_RE.finditer(uri):
        token = match.group(0)
        name = match.group("name")
        if not token.startswith(f"{{{name}?"):
            required.add(name)
    return required


def _php_role(path: str, class_name: str, extends: str) -> str:
    short = _php_short_name(class_name)
    if path.startswith("app/Http/Controllers/") or short.endswith("Controller"):
        return "controller"
    if path.startswith(("app/Livewire/", "app/Http/Livewire/")) or extends.strip("\\") == "Livewire\\Component":
        return "livewire_component"
    if path.startswith("app/Http/Requests/") or _php_short_name(extends) == "FormRequest" or short.endswith("Request"):
        return "form_request"
    if (
        path.startswith("app/Http/Resources/")
        or _php_short_name(extends) in {"JsonResource", "ResourceCollection"}
        or short.endswith(("Resource", "ResourceCollection"))
    ):
        return "api_resource"
    if path.startswith("app/Models/") or _php_short_name(extends) == "Model":
        return "model"
    if path.startswith("app/Http/Middleware/") or short.endswith("Middleware"):
        return "middleware"
    if path.startswith("app/Jobs/") or short.endswith("Job"):
        return "job"
    if path.startswith("app/Events/") or short.endswith("Event"):
        return "event"
    if path.startswith("app/Listeners/") or short.endswith("Listener"):
        return "listener"
    if path.startswith("app/Mail/") or short.endswith(("Mail", "Mailable")):
        return "mailable"
    if path.startswith("app/Notifications/") or short.endswith("Notification"):
        return "notification"
    if path.startswith("app/Console/Commands/") or _php_short_name(extends) == "Command" or short.endswith("Command"):
        return "artisan_command"
    if path.startswith("app/Policies/") or short.endswith("Policy"):
        return "policy"
    if path.startswith("app/Services/") or short.endswith("Service"):
        return "service"
    return "php_class"


def _class_context(classes: list[dict[str, Any]], offset: int) -> dict[str, Any] | None:
    current = None
    for item in classes:
        if int(item["offset"]) > offset:
            break
        current = item
    return current


def _edge_append(edges: list[dict[str, Any]], edge: dict[str, Any], *, max_edges: int) -> bool:
    if len(edges) >= max_edges:
        return False
    edges.append({key: value for key, value in edge.items() if value not in ("", None)})
    return True


def _route_chain(content: str, start: int) -> str:
    end = content.find(";", start)
    if end == -1:
        return content[start : start + 240]
    return content[start:end]


def _route_middleware_values(chain: str) -> list[str]:
    values: list[str] = []
    for match in PHP_ROUTE_MIDDLEWARE_RE.finditer(chain):
        raw = match.group("value")
        quoted = [item.group("value").strip() for item in PHP_QUOTED_VALUE_RE.finditer(raw)]
        if quoted:
            values.extend(quoted)
            continue
        clean = raw.strip().strip("'\"")
        if clean:
            values.append(clean)
    return sorted({value for value in values if value})


def _middleware_base_name(value: str) -> str:
    return str(value or "").split(":", 1)[0].strip()


def _middleware_parameters(value: str) -> list[str]:
    _base, separator, params = str(value or "").partition(":")
    if not separator:
        return []
    values: list[str] = []
    for raw_param in params.split(","):
        param = raw_param.strip()
        if re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", param):
            values.append(param)
        if len(values) >= 8:
            break
    return values


def _php_class_ref(value: str) -> str:
    return str(value or "").strip().lstrip("\\")


def _php_middleware_list_items(raw: str, namespace: str = "", uses: dict[str, str] | None = None) -> list[str]:
    use_map = uses or {}
    items: list[str] = []
    seen: set[str] = set()
    for match in PHP_CLASS_CONST_RE.finditer(raw or ""):
        item = _php_fqcn_resolved(namespace, _php_class_ref(match.group("class")), use_map)
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    for match in PHP_QUOTED_VALUE_RE.finditer(raw or ""):
        item = match.group("value").strip()
        if item and item not in seen:
            seen.add(item)
            items.append(item)
    return items


def _laravel_middleware_catalog(root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = {
        "app/Http/Kernel.php",
        "bootstrap/app.php",
    }
    aliases: list[dict[str, Any]] = []
    groups: list[dict[str, Any]] = []
    seen_aliases: set[tuple[str, str]] = set()
    seen_groups: set[tuple[str, tuple[str, ...]]] = set()
    for item in files:
        rel = str(item.get("path") or "")
        if rel not in candidates:
            continue
        try:
            content, truncated, _digest = _read_text_bounded(root / rel, 256_000)
        except OSError:
            continue
        if truncated:
            continue
        namespace = _php_namespace(content)
        uses = _php_use_map(content)

        for match in PHP_ARRAY_ENTRY_CLASS_RE.finditer(content):
            name = match.group("key").strip()
            class_name = _php_fqcn_resolved(namespace, _php_class_ref(match.group("class")), uses)
            if not name or not class_name:
                continue
            key = (name, class_name)
            if key in seen_aliases:
                continue
            seen_aliases.add(key)
            aliases.append(
                {
                    "name": name,
                    "class": class_name,
                    "path": rel,
                    "line": _line_number(content, match.start()),
                }
            )

        for match in PHP_ARRAY_ENTRY_LIST_RE.finditer(content):
            name = match.group("key").strip()
            members = _php_middleware_list_items(match.group("items"), namespace, uses)
            if not name or not members:
                continue
            key = (name, tuple(members))
            if key in seen_groups:
                continue
            seen_groups.add(key)
            groups.append(
                {
                    "name": name,
                    "members": members,
                    "path": rel,
                    "line": _line_number(content, match.start()),
                }
            )

    alias_map = {item["name"]: item["class"] for item in aliases}
    group_map = {item["name"]: item["members"] for item in groups}
    return {
        "schema": "hades.laravel_middleware.v1",
        "aliases": aliases[:500],
        "groups": groups[:200],
        "alias_count": len(aliases),
        "group_count": len(groups),
        "raw_source_included": False,
        "_alias_map": alias_map,
        "_group_map": group_map,
    }


def _php_route_arg(args: str, name: str) -> str:
    for match in PHP_NAMED_ROUTE_ARG_RE.finditer(args or ""):
        if match.group("name") == name:
            return match.group("value")
    return ""


def _php_route_path_arg(args: str) -> str:
    path = _php_route_arg(args, "path")
    if path:
        return path
    stripped = str(args or "").lstrip()
    if stripped.startswith(("'", '"')):
        match = PHP_QUOTED_VALUE_RE.match(stripped)
        return match.group("value") if match else ""
    return ""


def _php_route_methods(args: str) -> list[str]:
    match = PHP_ROUTE_METHODS_ARG_RE.search(args or "")
    if not match:
        return []
    methods = [item.group("value").upper() for item in PHP_QUOTED_VALUE_RE.finditer(match.group("value"))]
    return sorted({method for method in methods if method})


def _php_route_metadata(args: str, *, line: int, source: str) -> dict[str, Any]:
    route = {
        "uri": _php_route_path_arg(args),
        "name": _php_route_arg(args, "name"),
        "methods": _php_route_methods(args),
        "line": line,
        "source": source,
    }
    return {key: value for key, value in route.items() if value not in ("", None, [])}


def _php_route_metadata_before(source: str, offset: int) -> list[dict[str, Any]]:
    start = max(0, offset - 2_000)
    segment = source[start:offset]
    routes: list[dict[str, Any]] = []
    for match in PHP_SYMFONY_ROUTE_ATTRIBUTE_RE.finditer(segment):
        tail = segment[match.end() :].strip()
        if tail and not tail.startswith("#["):
            continue
        routes.append(
            _php_route_metadata(
                match.group("args"),
                line=_line_number(source, start + match.start()),
                source="attribute",
            )
        )
    if routes:
        return routes

    docblocks = list(PHP_DOCBLOCK_RE.finditer(segment))
    if not docblocks:
        return []
    docblock = docblocks[-1]
    if segment[docblock.end() :].strip():
        return []
    body_start = start + docblock.start("body")
    return [
        _php_route_metadata(
            match.group("args"),
            line=_line_number(source, body_start + match.start()),
            source="annotation",
        )
        for match in PHP_SYMFONY_ROUTE_ANNOTATION_RE.finditer(docblock.group("body"))
    ]


def _php_combine_route_names(prefix: str, name: str) -> str:
    if prefix and name:
        return f"{prefix}{name}"
    return name or prefix


def _php_symfony_route(
    class_route: dict[str, Any],
    method_route: dict[str, Any],
    *,
    handler: str,
    controller: str,
    rel: str,
    fallback_line: int,
) -> dict[str, Any]:
    methods = method_route.get("methods") or class_route.get("methods") or ["ANY"]
    route = {
        "framework": "symfony",
        "method": "|".join(methods),
        "uri": _join_url_path(str(class_route.get("uri") or ""), str(method_route.get("uri") or "")),
        "handler": handler,
        "controller": controller,
        "path": rel,
        "line": int(method_route.get("line") or class_route.get("line") or fallback_line),
    }
    name = _php_combine_route_names(str(class_route.get("name") or ""), str(method_route.get("name") or ""))
    if name:
        route["name"] = name
    return route


def _php_attribute_short_name(name: str) -> str:
    return str(name or "").split("\\")[-1]


def _php_attributes_before(source: str, offset: int) -> list[dict[str, Any]]:
    start = max(0, offset - 2_000)
    segment = source[start:offset]
    attrs: list[dict[str, Any]] = []
    tail_start = 0
    for match in PHP_ATTRIBUTE_RE.finditer(segment):
        if segment[tail_start : match.start()].strip():
            attrs = []
        attrs.append(
            {
                "name": match.group("name"),
                "short_name": _php_attribute_short_name(match.group("name")),
                "args": match.group("args") or "",
                "line": _line_number(source, start + match.start()),
            }
        )
        tail_start = match.end()
    if segment[tail_start:].strip():
        return []
    return attrs


def _php_attr_by_short_name(attrs: list[dict[str, Any]], *names: str) -> dict[str, Any] | None:
    wanted = set(names)
    for attr in attrs:
        if attr.get("short_name") in wanted:
            return attr
    return None


def _php_attr_string(args: str, name: str) -> str:
    for match in PHP_NAMED_ATTR_STRING_RE.finditer(args or ""):
        if match.group("name") == name:
            return match.group("value")
    stripped = str(args or "").lstrip()
    if name == "name" and stripped.startswith(("'", '"')):
        match = PHP_QUOTED_VALUE_RE.match(stripped)
        return match.group("value") if match else ""
    return ""


def _php_attr_bool(args: str, name: str) -> bool | None:
    for match in PHP_NAMED_ATTR_BOOL_RE.finditer(args or ""):
        if match.group("name") == name:
            return match.group("value").lower() == "true"
    return None


def _php_attr_int(args: str, name: str) -> int | None:
    for match in PHP_NAMED_ATTR_INT_RE.finditer(args or ""):
        if match.group("name") == name:
            return int(match.group("value"))
    return None


def _php_attr_class(args: str, name: str) -> str:
    for match in PHP_NAMED_ATTR_CLASS_RE.finditer(args or ""):
        if match.group("name") == name:
            return match.group("value")
    return ""


def _php_doctrine_table_name(attrs: list[dict[str, Any]], class_name: str) -> str:
    table_attr = _php_attr_by_short_name(attrs, "Table")
    if table_attr:
        table = _php_attr_string(str(table_attr.get("args") or ""), "name")
        if table:
            return table
    return _snake_name(class_name) + "s"


def _php_doctrine_entity_meta(source: str, offset: int, class_name: str) -> dict[str, Any] | None:
    attrs = _php_attributes_before(source, offset)
    if not _php_attr_by_short_name(attrs, "Entity") and not _php_attr_by_short_name(attrs, "Table"):
        return None
    return {"table": _php_doctrine_table_name(attrs, class_name), "line": attrs[0]["line"] if attrs else _line_number(source, offset)}


def _php_doctrine_column(attrs: list[dict[str, Any]], prop_name: str, rel: str, line: int) -> dict[str, Any] | None:
    column_attr = _php_attr_by_short_name(attrs, "Column")
    if column_attr is None:
        return None
    args = str(column_attr.get("args") or "")
    column = {
        "name": _php_attr_string(args, "name") or prop_name,
        "field": prop_name,
        "type": _php_attr_string(args, "type"),
        "path": rel,
        "line": line,
    }
    for key in ("nullable", "unique"):
        value = _php_attr_bool(args, key)
        if value is not None:
            column[key] = value
    for key in ("length", "precision", "scale"):
        value = _php_attr_int(args, key)
        if value is not None:
            column[key] = value
    if _php_attr_by_short_name(attrs, "Id"):
        column["primary_key"] = True
    return {key: value for key, value in column.items() if value not in ("", None)}


def _php_doctrine_relation_target(attrs: list[dict[str, Any]], prop_type: str, namespace: str, uses: dict[str, str]) -> str:
    relation_attr = _php_attr_by_short_name(attrs, "ManyToOne", "OneToOne")
    if relation_attr:
        target = _php_attr_class(str(relation_attr.get("args") or ""), "targetEntity")
        if target:
            return _php_fqcn_resolved(namespace, target, uses)
    clean_type = prop_type.lstrip("?")
    if clean_type and "\\" not in clean_type and clean_type[:1].isupper():
        return _php_fqcn_resolved(namespace, clean_type, uses)
    if "\\" in clean_type:
        return clean_type.strip("\\")
    return ""


def _php_doctrine_join_column(attrs: list[dict[str, Any]], prop_name: str, rel: str, line: int) -> dict[str, Any] | None:
    join_attr = _php_attr_by_short_name(attrs, "JoinColumn")
    if join_attr is None:
        return None
    args = str(join_attr.get("args") or "")
    return {
        "column": _php_attr_string(args, "name") or f"{prop_name}_id",
        "references_column": _php_attr_string(args, "referencedColumnName") or "id",
        "nullable": _php_attr_bool(args, "nullable"),
        "path": rel,
        "line": line,
    }


def _php_array_field_keys(source: str, body: str, base_offset: int) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in PHP_ARRAY_FIELD_KEY_RE.finditer(body):
        field = match.group("field")
        if field in seen:
            continue
        seen.add(field)
        fields.append({"field": field, "line": _line_number(source, base_offset + match.start())})
    return fields


def _php_top_level_array_field_keys(source: str, body: str, base_offset: int) -> list[dict[str, Any]]:
    open_index = body.find("[")
    if open_index == -1:
        return []
    open_abs = base_offset + open_index
    close_abs = _balanced_end(source, open_abs, "[", "]")
    if close_abs == -1:
        return []
    array_body = source[open_abs + 1 : close_abs]
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item, item_offset in _split_top_level_items(array_body):
        match = re.match(r"\s*['\"](?P<field>[A-Za-z0-9_.*-]+)['\"]\s*=>", item)
        if not match:
            continue
        field = match.group("field")
        if field in seen:
            continue
        seen.add(field)
        fields.append({"field": field, "line": _line_number(source, open_abs + 1 + item_offset + match.start())})
    return fields


def _php_validation_rule_names(value: str) -> list[str]:
    rules: list[str] = []
    seen: set[str] = set()

    def add_rule(raw: str) -> None:
        name = raw.split(":", 1)[0].strip()
        if not name or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            return
        normalized = _snake_name(name).lower()
        if normalized not in seen:
            seen.add(normalized)
            rules.append(normalized)

    quoted_rule_source = re.sub(r"\bRule::[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)", "", value)
    for quoted in PHP_QUOTED_VALUE_RE.finditer(quoted_rule_source):
        for part in quoted.group("value").split("|"):
            add_rule(part)
    for rule_call in re.finditer(r"\bRule::(?P<rule>[A-Za-z_][A-Za-z0-9_]*)\s*\(", value):
        add_rule(rule_call.group("rule"))
    return rules


def _php_safe_db_identifier(value: str) -> str:
    identifier = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", identifier):
        return ""
    return identifier


def _php_validation_database_rule_refs(value: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    def add_ref(raw_rule: str, raw_table: str, raw_column: str = "") -> None:
        rule = _snake_name(raw_rule).lower()
        if rule not in {"exists", "unique"}:
            return
        table = _php_safe_db_identifier(raw_table)
        column = _php_safe_db_identifier(raw_column)
        if not table:
            return
        key = (rule, table, column)
        if key in seen:
            return
        seen.add(key)
        ref = {"rule": rule, "table": table}
        if column:
            ref["column"] = column
        refs.append(ref)

    quoted_rule_source = re.sub(r"\bRule::[A-Za-z_][A-Za-z0-9_]*\s*\([^)]*\)", "", value)
    for quoted in PHP_QUOTED_VALUE_RE.finditer(quoted_rule_source):
        for part in quoted.group("value").split("|"):
            rule, separator, params = part.partition(":")
            if not separator:
                continue
            param_parts = [param.strip() for param in params.split(",")]
            add_ref(rule, param_parts[0] if param_parts else "", param_parts[1] if len(param_parts) > 1 else "")
    for rule_call in re.finditer(r"\bRule::(?P<rule>exists|unique)\s*\((?P<args>[^)]*)\)", value, re.IGNORECASE):
        quoted_args = [match.group("value") for match in PHP_QUOTED_VALUE_RE.finditer(rule_call.group("args"))]
        if quoted_args:
            add_ref(rule_call.group("rule"), quoted_args[0], quoted_args[1] if len(quoted_args) > 1 else "")
    return refs


def _php_validation_database_rule_target(ref: dict[str, Any]) -> str:
    table = str(ref.get("table") or "")
    column = str(ref.get("column") or "")
    return f"table:{table}.{column}" if column else f"table:{table}"


def _php_array_validation_fields(source: str, body: str, base_offset: int) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    matches = list(PHP_ARRAY_FIELD_KEY_RE.finditer(body))
    for index, match in enumerate(matches):
        field = match.group("field")
        if field in seen:
            continue
        seen.add(field)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        value_segment = body[match.end() : end]
        fields.append(
            {
                "field": field,
                "line": _line_number(source, base_offset + match.start()),
                "rules": _php_validation_rule_names(value_segment),
                "database_rules": _php_validation_database_rule_refs(value_segment),
            }
        )
    return fields


def _php_rules_method_body(source: str) -> tuple[str, int] | None:
    match = re.search(r"\bfunction\s+rules\s*\([^)]*\)", source)
    if not match:
        return None
    next_method = re.search(r"\bfunction\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", source[match.end() :])
    end = match.end() + next_method.start() if next_method else len(source)
    return source[match.end() : end], match.end()


def _php_livewire_validation_fields(source: str, classes: list[dict[str, Any]], fqcn: str, rel: str) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_field(field_info: dict[str, Any]) -> None:
        field_name = str(field_info.get("field") or "")
        if not field_name or field_name in seen:
            return
        seen.add(field_name)
        fields.append(
            {
                "field": field_name,
                "rules": field_info.get("rules") or [],
                "database_rules": field_info.get("database_rules") or [],
                "path": rel,
                "line": field_info.get("line"),
            }
        )

    for property_match in PHP_LIVEWIRE_RULES_PROPERTY_RE.finditer(source):
        property_class = _class_context(classes, property_match.start())
        if not property_class or property_class.get("name") != fqcn:
            continue
        for field_info in _php_array_validation_fields(source, property_match.group("body"), property_match.start("body")):
            add_field(field_info)

    for method_match in PHP_METHOD_RE.finditer(source):
        method_class = _class_context(classes, method_match.start())
        if not method_class or method_class.get("name") != fqcn:
            continue
        if str(method_match.group("name") or "") != "rules":
            continue
        method_body, method_body_offset = _php_method_body_slice(source, method_match)
        return_match = PHP_RETURN_ARRAY_RE.search(method_body)
        if return_match:
            for field_info in _php_array_validation_fields(
                source,
                return_match.group("body"),
                method_body_offset + return_match.start("body"),
            ):
                add_field(field_info)
    return fields


def _php_livewire_validation_field_matches_model(field_name: str, model: str, root_property: str) -> bool:
    field_name = str(field_name or "")
    model = str(model or "")
    root_property = str(root_property or "")
    if not field_name or not model or not root_property:
        return False
    if field_name == model or field_name == root_property:
        return True
    field_parts = field_name.split(".")
    model_parts = model.split(".")
    if len(field_parts) != len(model_parts):
        return False
    return all(field_part == "*" or field_part == model_part for field_part, model_part in zip(field_parts, model_parts, strict=False))


def _php_schedule_cadence(chain: str) -> str:
    ignored = {"name", "timezone", "withoutOverlapping", "onOneServer", "runInBackground", "evenInMaintenanceMode"}
    for match in PHP_SCHEDULE_CADENCE_RE.finditer(chain or ""):
        cadence = match.group("cadence")
        if cadence not in ignored:
            return cadence
    return ""


def _php_command_name(signature: str) -> str:
    return str(signature or "").split(maxsplit=1)[0].strip()


def _blade_view_name(path: str) -> str:
    prefix = "resources/views/"
    suffix = ".blade.php"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return ""
    return path[len(prefix) : -len(suffix)].replace("/", ".")


def _blade_component_symbol(view_name: str) -> str:
    prefix = "components."
    if not view_name.startswith(prefix):
        return ""
    component = view_name[len(prefix) :].replace("::", ".").replace(":", ".")
    return f"component:{component}" if component else ""


def _blade_component_target(raw: str) -> str:
    component = (raw or "").strip().replace("::", ".").replace(":", ".")
    if component in {"dynamic-component", "slot"}:
        return ""
    if component.startswith("components."):
        component = component[len("components.") :]
    return f"component:{component}" if component else ""


def _append_blade_view_graph(
    source: str,
    rel: str,
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    route_by_name: dict[str, dict[str, Any]] | None = None,
    livewire_component_by_alias: dict[str, dict[str, Any]] | None = None,
    max_symbols: int,
    max_edges: int,
) -> bool:
    view_name = _blade_view_name(rel)
    if not view_name:
        return False

    truncated = False
    view_id = f"view:{view_name}"
    known_routes = route_by_name or {}
    known_livewire = livewire_component_by_alias or {}
    referenced_livewire_aliases: set[str] = set()
    if len(symbols) < max_symbols:
        symbols.append(
            {
                "kind": "blade_view",
                "name": view_id,
                "view": view_name,
                "role": "blade_view",
                "path": rel,
                "line": 1,
            }
        )
    else:
        truncated = True

    component_symbol = _blade_component_symbol(view_name)
    if component_symbol:
        if len(symbols) < max_symbols:
            symbols.append(
                {
                    "kind": "blade_component",
                    "name": component_symbol,
                    "component": component_symbol.removeprefix("component:"),
                    "role": "blade_component",
                    "path": rel,
                    "line": 1,
                }
            )
        else:
            truncated = True

    seen_edges: set[tuple[str, str, int]] = set()

    def append_edge(kind: str, target: str, offset: int) -> None:
        nonlocal truncated
        if not target:
            return
        line = _line_number(source, offset)
        key = (kind, target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": kind,
                "from": view_id,
                "to": target,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_route_ref(route_name: str, offset: int) -> None:
        nonlocal truncated
        if not route_name:
            return
        line = _line_number(source, offset)
        target = f"route:{route_name}"
        key = ("blade_route_ref", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_route_ref",
                "from": view_id,
                "to": target,
                "route_name": route_name,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_route_param(route_name: str, route_param: str, status: str, offset: int) -> None:
        nonlocal truncated
        route = known_routes.get(route_name)
        if not route:
            return
        route_param = str(route_param or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", route_param):
            return
        all_params = _php_route_params(route)
        required_params = _php_required_route_params(route)
        normalized_status = "missing" if status == "missing" else "provided"
        line = _line_number(source, offset)
        target = f"route_param:{route_name}.{route_param}"
        key = ("blade_route_param", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_route_param",
                "from": view_id,
                "to": target,
                "route_name": route_name,
                "route_param": route_param,
                "route_param_status": normalized_status,
                "route_param_required": route_param in required_params,
                "route_param_match": normalized_status == "provided" and route_param in all_params,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_blade_authorization(helper: str, ability: str, offset: int) -> None:
        nonlocal truncated
        ability = str(ability or "").strip()
        if not ability or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", ability):
            return
        normalized_helper = str(helper or "").lower()
        line = _line_number(source, offset)
        target = f"ability:{ability}"
        key = ("blade_authorization", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_authorization",
                "from": view_id,
                "to": target,
                "ability": ability,
                "authorization_helper": normalized_helper,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_blade_form_field(field: str, tag: str, offset: int) -> None:
        nonlocal truncated
        field = str(field or "").strip()
        if not field or not re.fullmatch(r"[A-Za-z0-9_.*:-]{1,128}", field):
            return
        normalized_tag = str(tag or "").lower()
        line = _line_number(source, offset)
        target = f"request_field:{field}"
        key = ("blade_form_field", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_form_field",
                "from": view_id,
                "to": target,
                "form_field": field,
                "form_field_tag": normalized_tag,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_blade_old_input(field: str, offset: int) -> None:
        nonlocal truncated
        field = str(field or "").strip()
        if not field or not re.fullmatch(r"[A-Za-z0-9_.*:-]{1,128}", field):
            return
        line = _line_number(source, offset)
        target = f"request_field:{field}"
        key = ("blade_old_input", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_old_input",
                "from": view_id,
                "to": target,
                "form_field": field,
                "input_helper": "old",
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_blade_validation_error(field: str, offset: int) -> None:
        nonlocal truncated
        field = str(field or "").strip()
        if not field or not re.fullmatch(r"[A-Za-z0-9_.*:-]{1,128}", field):
            return
        line = _line_number(source, offset)
        target = f"validation:{field}"
        key = ("blade_validation_error", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_validation_error",
                "from": view_id,
                "to": target,
                "form_field": field,
                "validation_helper": "error",
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_blade_wire_model(model: str, modifiers: str, offset: int) -> None:
        nonlocal truncated
        model = str(model or "").strip()
        if not model or not re.fullmatch(r"[A-Za-z0-9_.*:-]{1,128}", model):
            return
        modifier_tokens = [
            token
            for token in str(modifiers or "").lstrip(".").split(".")
            if token and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", token)
        ][:6]
        line = _line_number(source, offset)
        target = f"livewire_property:{model}"
        key = ("blade_wire_model", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_wire_model",
                "from": view_id,
                "to": target,
                "wire_model": model,
                "wire_modifiers": modifier_tokens,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_blade_wire_action(event: str, action: str, modifiers: str, offset: int) -> None:
        nonlocal truncated
        action = str(action or "").strip()
        if not action or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:-]{0,127}", action):
            return
        normalized_event = str(event or "").lower()
        if normalized_event not in {"click", "submit", "change", "keydown", "keyup", "blur", "focus"}:
            return
        modifier_tokens = [
            token
            for token in str(modifiers or "").lstrip(".").split(".")
            if token and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", token)
        ][:6]
        line = _line_number(source, offset)
        target = f"livewire_action:{action}"
        key = ("blade_wire_action", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_wire_action",
                "from": view_id,
                "to": target,
                "wire_action": action,
                "wire_event": normalized_event,
                "wire_modifiers": modifier_tokens,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_blade_alpine_model(model: str, modifiers: str, offset: int) -> None:
        nonlocal truncated
        model = str(model or "").strip()
        if not model or not re.fullmatch(r"\$?[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*){0,8}", model):
            return
        modifier_tokens = [
            token
            for token in str(modifiers or "").lstrip(".").split(".")
            if token and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", token)
        ][:6]
        line = _line_number(source, offset)
        target = f"alpine_state:{model}"
        key = ("blade_alpine_model", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_alpine_model",
                "from": view_id,
                "to": target,
                "alpine_model": model,
                "alpine_modifiers": modifier_tokens,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_livewire_component_class(alias: str, offset: int) -> None:
        nonlocal truncated
        component = known_livewire.get(alias)
        if not component:
            return
        component_class = str(component.get("class") or "")
        if not component_class:
            return
        line = _line_number(source, offset)
        target = component_class
        key = ("livewire_component_class", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "livewire_component_class",
                "from": f"livewire:{alias}",
                "to": target,
                "livewire_alias": alias,
                "livewire_class": component_class,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_livewire_action_methods(action: str, offset: int) -> None:
        nonlocal truncated
        action = str(action or "").strip()
        if not action or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_:-]{0,127}", action):
            return
        line = _line_number(source, offset)
        for alias in sorted(referenced_livewire_aliases):
            component = known_livewire.get(alias) or {}
            methods = component.get("methods") or {}
            if not isinstance(methods, dict):
                continue
            method_symbol = str(methods.get(action) or "")
            component_class = str(component.get("class") or "")
            if not method_symbol or not component_class:
                continue
            key = ("blade_wire_action_method", method_symbol, line)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "blade_wire_action_method",
                    "from": f"livewire_action:{action}",
                    "to": method_symbol,
                    "livewire_alias": alias,
                    "livewire_class": component_class,
                    "wire_action": action,
                    "path": rel,
                    "line": line,
                },
                max_edges=max_edges,
            ) or truncated

    def append_livewire_model_properties(model: str, offset: int) -> None:
        nonlocal truncated
        model = str(model or "").strip()
        if not model or not re.fullmatch(r"[A-Za-z0-9_.*:-]{1,128}", model):
            return
        # Livewire nested bindings like "order.status" resolve through the root
        # public property. Keep the full binding and the root property only.
        root_property = model.split(".", 1)[0].split(":", 1)[0].split("*", 1)[0]
        if not root_property or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", root_property):
            return
        line = _line_number(source, offset)
        for alias in sorted(referenced_livewire_aliases):
            component = known_livewire.get(alias) or {}
            properties = component.get("properties") or {}
            if not isinstance(properties, dict):
                continue
            property_info = properties.get(root_property) or {}
            component_class = str(component.get("class") or "")
            if not property_info or not component_class:
                continue
            key = ("blade_wire_model_property", f"{component_class}.{root_property}", line)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edge = {
                "kind": "blade_wire_model_property",
                "from": f"livewire_property:{model}",
                "to": f"livewire_property:{component_class}.{root_property}",
                "livewire_alias": alias,
                "livewire_class": component_class,
                "wire_model": model,
                "livewire_property": root_property,
                "path": rel,
                "line": line,
            }
            property_type = str(property_info.get("type") or "")
            if property_type:
                edge["livewire_property_type"] = property_type
            truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated

    def append_livewire_model_validations(model: str, offset: int) -> None:
        nonlocal truncated
        model = str(model or "").strip()
        if not model or not re.fullmatch(r"[A-Za-z0-9_.*:-]{1,128}", model):
            return
        root_property = model.split(".", 1)[0].split(":", 1)[0].split("*", 1)[0]
        if not root_property or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", root_property):
            return
        line = _line_number(source, offset)
        for alias in sorted(referenced_livewire_aliases):
            component = known_livewire.get(alias) or {}
            component_class = str(component.get("class") or "")
            if not component_class:
                continue
            for field_info in component.get("validation_fields") or []:
                field_name = str(field_info.get("field") or "")
                if not _php_livewire_validation_field_matches_model(field_name, model, root_property):
                    continue
                key = ("blade_wire_model_validation", f"{component_class}.{field_name}", line)
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "blade_wire_model_validation",
                        "from": f"livewire_property:{model}",
                        "to": f"validation:{field_name}",
                        "livewire_alias": alias,
                        "livewire_class": component_class,
                        "wire_model": model,
                        "livewire_property": root_property,
                        "field": field_name,
                        "validation_rules": field_info.get("rules") or [],
                        "validation_path": field_info.get("path"),
                        "validation_line": field_info.get("line"),
                        "path": rel,
                        "line": line,
                    },
                    max_edges=max_edges,
                ) or truncated

    def append_form_method(method: str, offset: int) -> None:
        nonlocal truncated
        normalized = str(method or "").upper()
        if normalized not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return
        line = _line_number(source, offset)
        target = f"http_method:{normalized}"
        key = ("blade_form_method", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_form_method",
                "from": view_id,
                "to": target,
                "form_method": normalized,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_csrf(offset: int) -> None:
        nonlocal truncated
        line = _line_number(source, offset)
        key = ("blade_csrf_token", "csrf:present", line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_csrf_token",
                "from": view_id,
                "to": "csrf:present",
                "csrf": "present",
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def append_form_route_method(route_name: str, form_method: str, offset: int) -> None:
        nonlocal truncated
        route = known_routes.get(route_name)
        if not route:
            return
        normalized_method = str(form_method or "").upper()
        if normalized_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return
        route_method = str(route.get("method") or "").upper()
        line = _line_number(source, offset)
        target = f"route:{route_name}"
        key = ("blade_form_route_method", target, line)
        if key in seen_edges:
            return
        seen_edges.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": "blade_form_route_method",
                "from": view_id,
                "to": target,
                "route_name": route_name,
                "form_method": normalized_method,
                "route_method": route_method,
                "route_method_match": normalized_method == route_method,
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated

    def route_call_has_argument(offset: int) -> bool:
        while offset < len(source) and source[offset].isspace():
            offset += 1
        return offset < len(source) and source[offset] == ","

    for match in BLADE_EXTENDS_RE.finditer(source):
        append_edge("blade_extends", f"view:{match.group('view')}", match.start())
    for match in BLADE_INCLUDE_RE.finditer(source):
        append_edge("blade_include", f"view:{match.group('view')}", match.start())
    for match in BLADE_CONDITIONAL_INCLUDE_RE.finditer(source):
        append_edge("blade_include", f"view:{match.group('view')}", match.start())
    for match in BLADE_COMPONENT_DIRECTIVE_RE.finditer(source):
        append_edge("blade_component", _blade_component_target(match.group("component")), match.start())
    for match in BLADE_ANONYMOUS_COMPONENT_RE.finditer(source):
        append_edge("blade_component", _blade_component_target(match.group("component")), match.start())
    for match in BLADE_LIVEWIRE_RE.finditer(source):
        livewire_name = match.group("directive") or match.group("tag") or ""
        if livewire_name:
            referenced_livewire_aliases.add(livewire_name)
        append_edge("livewire_component", f"livewire:{livewire_name}", match.start())
        append_livewire_component_class(livewire_name, match.start())
    for match in BLADE_AUTHORIZATION_RE.finditer(source):
        append_blade_authorization(match.group("helper"), match.group("ability"), match.start())
    for match in BLADE_FORM_FIELD_RE.finditer(source):
        append_blade_form_field(match.group("field"), match.group("tag"), match.start("field"))
    for match in BLADE_OLD_INPUT_RE.finditer(source):
        append_blade_old_input(match.group("field"), match.start("field"))
    for match in BLADE_ERROR_DIRECTIVE_RE.finditer(source):
        append_blade_validation_error(match.group("field"), match.start("field"))
    for match in BLADE_WIRE_MODEL_RE.finditer(source):
        append_blade_wire_model(match.group("model"), match.group("modifiers"), match.start("model"))
        append_livewire_model_properties(match.group("model"), match.start("model"))
        append_livewire_model_validations(match.group("model"), match.start("model"))
    for match in BLADE_ALPINE_MODEL_RE.finditer(source):
        append_blade_alpine_model(match.group("model"), match.group("modifiers"), match.start("model"))
    for match in BLADE_WIRE_ACTION_RE.finditer(source):
        append_blade_wire_action(
            match.group("event"),
            match.group("action"),
            match.group("modifiers"),
            match.start("action"),
        )
        append_livewire_action_methods(match.group("action"), match.start("action"))
    for match in BLADE_ROUTE_FUNCTION_RE.finditer(source):
        append_route_ref(match.group("route"), match.start())
        route = known_routes.get(match.group("route"))
        if route and not route_call_has_argument(match.end()):
            for missing_param in sorted(_php_required_route_params(route)):
                append_route_param(match.group("route"), missing_param, "missing", match.start())
    for match in BLADE_ROUTE_ARRAY_PARAMS_RE.finditer(source):
        route_name = match.group("route")
        route = known_routes.get(route_name)
        if not route:
            continue
        provided_params: set[str] = set()
        params_source = match.group("params") or ""
        for param_match in BLADE_ROUTE_PARAM_KEY_RE.finditer(params_source):
            param_name = param_match.group("param")
            provided_params.add(param_name)
            append_route_param(route_name, param_name, "provided", match.start("params") + param_match.start("param"))
        for missing_param in sorted(_php_required_route_params(route) - provided_params):
            append_route_param(route_name, missing_param, "missing", match.start("params"))
    for match in BLADE_FORM_METHOD_RE.finditer(source):
        append_form_method(match.group("method"), match.start())
    for match in BLADE_CSRF_RE.finditer(source):
        append_csrf(match.start())
    for form_match in BLADE_FORM_BLOCK_RE.finditer(source):
        form_attrs = form_match.group("attrs") or ""
        form_body = form_match.group("body") or ""
        route_match = BLADE_ROUTE_FUNCTION_RE.search(form_match.group(0) or "")
        spoof_method_match = BLADE_FORM_METHOD_RE.search(form_body)
        html_method_match = BLADE_FORM_HTML_METHOD_RE.search(form_attrs)
        if not route_match:
            continue
        if spoof_method_match:
            append_form_route_method(
                route_match.group("route"),
                spoof_method_match.group("method"),
                form_match.start("body") + spoof_method_match.start("method"),
            )
        elif html_method_match:
            append_form_route_method(
                route_match.group("route"),
                html_method_match.group("method"),
                form_match.start("attrs") + html_method_match.start("method"),
            )
        else:
            append_form_route_method(route_match.group("route"), "GET", form_match.start())

    return truncated


def _laravel_routes(root: Path, files: list[dict[str, Any]], *, max_routes: int = 500) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    for item in files:
        rel = str(item.get("path") or "")
        if not (rel.startswith("routes/") and rel.endswith(".php")):
            continue
        try:
            content, truncated, _digest = _read_text_bounded(root / rel, 256_000)
        except OSError:
            continue
        if truncated:
            continue
        for match in ROUTE_CALL_RE.finditer(content):
            chain = _route_chain(content, match.end())
            name = match.group("name")
            if not name:
                name_match = PHP_ROUTE_NAME_RE.search(chain)
                name = name_match.group("name") if name_match else None
            route = {
                "framework": "laravel",
                "method": match.group("method").upper(),
                "uri": match.group("uri"),
                "handler": _normalize_laravel_handler(match.group("handler")),
                "path": rel,
                "line": _line_number(content, match.start()),
            }
            if name:
                route["name"] = name
            middleware = _route_middleware_values(chain)
            if middleware:
                route["middleware"] = middleware
            routes.append(route)
            if len(routes) >= max_routes:
                return routes
        for match in ROUTE_RESOURCE_RE.finditer(content):
            chain = _route_chain(content, match.end())
            resource_routes = _laravel_resource_routes(
                resource=match.group("resource"),
                controller=match.group("controller"),
                api=str(match.group("kind") or "").lower() == "apiresource",
                rel=rel,
                line=_line_number(content, match.start()),
                chain=chain,
            )
            for route in resource_routes:
                routes.append(route)
                if len(routes) >= max_routes:
                    return routes
    return routes


def _database_summary(files: list[dict[str, Any]]) -> dict[str, Any]:
    migrations = sorted(
        str(item.get("path") or "")
        for item in files
        if str(item.get("path") or "").startswith("database/migrations/")
    )
    return {"migrations": migrations[:500], "migration_count": len(migrations)}


def _first_quoted_arg(args: str) -> str:
    match = PHP_QUOTED_VALUE_RE.search(args)
    return match.group("value") if match else ""


def _migration_column_name(call_type: str, args: str) -> str:
    quoted = _first_quoted_arg(args)
    if quoted:
        return quoted
    if call_type == "id":
        return "id"
    if call_type == "timestamps":
        return "created_at,updated_at"
    if call_type == "softDeletes":
        return "deleted_at"
    return ""


def _foreign_table_from_column(column: str) -> str:
    if column.endswith("_id") and len(column) > 3:
        stem = column[:-3]
        return stem + "ies" if stem.endswith("y") else stem + "s"
    return ""


def _migration_columns(source: str, rel: str, table: str, start: int, end: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    indexes: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    body = source[start:end]
    for match in PHP_TABLE_CALL_RE.finditer(body):
        call_type = match.group("type")
        args = match.group("args")
        chain = match.group("chain") or ""
        column = _migration_column_name(call_type, args)
        line = _line_number(source, start + match.start())
        if call_type in {"index", "unique", "primary", "foreign"}:
            target = column or _first_quoted_arg(args)
            if target:
                indexes.append({"table": table, "column": target, "kind": call_type, "path": rel, "line": line})
            continue
        if column:
            columns.append(
                {
                    "name": column,
                    "type": call_type,
                    "path": rel,
                    "line": line,
                    "nullable": "->nullable(" in chain,
                    "indexed": "->index(" in chain or "->unique(" in chain,
                }
            )
        if call_type == "foreignId" or "->constrained(" in chain:
            foreign_table = _first_quoted_arg(chain) or _foreign_table_from_column(column)
            if column and foreign_table:
                foreign_keys.append(
                    {
                        "table": table,
                        "column": column,
                        "references_table": foreign_table,
                        "path": rel,
                        "line": line,
                    }
                )
    return columns, indexes, foreign_keys


def _laravel_migration_tables(source: str, rel: str) -> list[dict[str, Any]]:
    matches = list(PHP_SCHEMA_ACTION_RE.finditer(source))
    tables: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        table = match.group("table")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        columns, indexes, foreign_keys = _migration_columns(source, rel, table, start, end)
        tables.append(
            {
                "table": table,
                "action": match.group("action"),
                "path": rel,
                "line": _line_number(source, match.start()),
                "columns": columns[:200],
                "indexes": indexes[:100],
                "foreign_keys": foreign_keys[:100],
            }
        )
    return tables


def _project_index_summary(index: dict[str, Any]) -> str:
    route_bits = [
        f"{route['method']} {route['uri']} -> {route.get('handler', '')}".strip()
        for route in index.get("routes", [])[:5]
    ]
    package_bits: list[str] = []
    for manifest in index.get("dependency_manifests", [])[:4]:
        packages = manifest.get("packages") or []
        if packages:
            package_bits.extend(str(pkg) for pkg in packages[:5])
    migration_count = int((index.get("database") or {}).get("migration_count") or 0)
    parts = [
        f"routes: {', '.join(route_bits) or 'none'}",
        f"dependencies: {', '.join(package_bits) or 'none'}",
        f"migrations: {migration_count}",
    ]
    return "Project index; " + "; ".join(parts)


def _build_project_index(root: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    index = {
        "schema": "hades.project_index.v1",
        "source_schema": "hades.git_tree.v1",
        "root": root.name,
        "language_counts": _language_counts(files),
        "routes": _laravel_routes(root, files),
        "dependency_manifests": _dependency_manifests(root, files),
        "database": _database_summary(files),
        "raw_source_included": False,
    }
    index["summary"] = _project_index_summary(index)
    return index


def _execute_read_files(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    paths = [_safe_relpath(p) for p in payload.get("paths") or []]
    max_bytes = int(payload.get("max_bytes") or 512_000)
    ignore_spec = _gitignore_spec(workspace_root)
    attachments: list[dict[str, Any]] = []
    omitted: list[dict[str, str]] = []
    for rel in paths[: int(payload.get("max_files") or 20)]:
        try:
            path = _resolve_inside(workspace_root, rel)
            if not path.is_file():
                omitted.append({"path": rel, "reason": "not_file"})
                continue
            skip_reason = _skip_file_reason(path, rel, ignore_spec)
            if skip_reason:
                omitted.append({"path": rel, "reason": skip_reason})
                continue
            text, truncated, digest = _read_text_bounded(path, max_bytes)
            redacted = redact_secret(text)
            attachments.append(
                {
                    "path": rel,
                    "sha256": digest,
                    "content": redacted,
                    "truncated": truncated,
                    "redactions": 1 if redacted != text else 0,
                }
            )
        except Exception as exc:
            omitted.append({"path": rel, "reason": _safe_exception_reason(exc)})
    return {
        "status": "completed",
        "summary": f"Read {len(attachments)} file(s); omitted {len(omitted)}.",
        "attachments": attachments,
        "omitted": omitted,
        "redactions": sum(1 for attachment in attachments if attachment["redactions"]) + len(omitted),
        "retention_class": "source_content",
    }


def _line_window(content: str, start_line: int, end_line: int) -> tuple[str, int, int]:
    lines = content.splitlines()
    if not lines:
        return "", 1, 1
    start = max(1, start_line)
    end = max(start, end_line)
    bounded_start = min(start, len(lines))
    bounded_end = min(end, len(lines))
    selected = lines[bounded_start - 1 : bounded_end]
    return "\n".join(selected), bounded_start, bounded_end


def _execute_read_source_slice(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    rel = _safe_relpath(str(payload.get("path") or ""))
    if not rel:
        return {
            "status": "failed",
            "summary": "Missing source slice path.",
            "omitted": [{"reason": "missing_path"}],
            "retention_class": "source_slice",
        }
    start_line = max(1, int(payload.get("start_line") or payload.get("line") or 1))
    end_line = max(start_line, int(payload.get("end_line") or start_line))
    if end_line - start_line + 1 > int(payload.get("max_lines") or 120):
        end_line = start_line + int(payload.get("max_lines") or 120) - 1
    max_file_bytes = int(payload.get("max_file_bytes") or 512_000)
    max_slice_bytes = int(payload.get("max_slice_bytes") or 64_000)
    ignore_spec = _gitignore_spec(workspace_root)

    try:
        path = _resolve_inside(workspace_root, rel)
        if not path.is_file():
            return {
                "status": "failed",
                "summary": f"Source slice path is not a file: {rel}",
                "omitted": [{"path": rel, "reason": "not_file"}],
                "retention_class": "source_slice",
            }
        skip_reason = _skip_file_reason(path, rel, ignore_spec)
        if skip_reason:
            return {
                "status": "failed",
                "summary": f"Source slice path omitted: {skip_reason}",
                "omitted": [{"path": rel, "reason": skip_reason}],
                "retention_class": "source_slice",
            }
        size = path.stat().st_size
        if size > max_file_bytes:
            return {
                "status": "failed",
                "summary": f"Source slice file exceeds max_file_bytes: {rel}",
                "omitted": [{"path": rel, "reason": "file_too_large"}],
                "retention_class": "source_slice",
            }
        source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
    except Exception as exc:
        return {
            "status": "failed",
            "summary": f"Failed to read source slice: {_safe_exception_reason(exc)}",
            "omitted": [{"path": rel, "reason": _safe_exception_reason(exc)}],
            "retention_class": "source_slice",
        }

    content, bounded_start, bounded_end = _line_window(source, start_line, end_line)
    redacted = redact_secret(content)
    redaction_count = 1 if redacted != content else 0
    encoded = redacted.encode("utf-8")
    truncated = was_truncated
    if len(encoded) > max_slice_bytes:
        redacted = encoded[:max_slice_bytes].decode("utf-8", errors="ignore")
        truncated = True
    source_slice = {
        "path": rel,
        "start_line": bounded_start,
        "end_line": bounded_end,
        "language": _language_for_path(rel),
        "symbol": str(payload.get("symbol") or "").strip(),
        "content_redacted": redacted,
        "sha256": _hash_bytes(redacted.encode("utf-8")),
        "redactions": redaction_count,
        "truncated": truncated,
        "retention_class": "source_slice",
        "policy": str(payload.get("policy") or "manual_review"),
        "raw_source_included": True,
    }
    return {
        "status": "completed",
        "summary": f"Read source slice {rel}:{bounded_start}-{bounded_end}; redactions {source_slice['redactions']}.",
        "source_slice": {key: value for key, value in source_slice.items() if value not in ("", None)},
        "redactions": source_slice["redactions"],
        "retention_class": "source_slice",
    }


def _iter_workspace_files(root: Path, *, max_files: int) -> tuple[list[Path], list[dict[str, str]], bool]:
    ignore_spec = _gitignore_spec(root)
    files: list[Path] = []
    omitted: list[dict[str, str]] = []
    for current, dirs, names in os.walk(root):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for dirname in sorted(dirs):
            dir_path = current_path / dirname
            rel = dir_path.relative_to(root).as_posix()
            if dirname in SKIP_DIRS:
                omitted.append({"path": rel, "reason": "generated_or_dependency_dir"})
                continue
            if dir_path.is_symlink():
                omitted.append({"path": rel, "reason": "symlink"})
                continue
            if ignore_spec is not None and (
                ignore_spec.match_file(rel) or ignore_spec.match_file(rel + "/")
            ):
                omitted.append({"path": rel, "reason": "gitignored"})
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for name in sorted(names):
            path = current_path / name
            rel = path.relative_to(root).as_posix()
            skip_reason = _skip_file_reason(path, rel, ignore_spec)
            if skip_reason:
                omitted.append({"path": rel, "reason": skip_reason})
                continue
            if path.is_file():
                files.append(path)
                if len(files) >= max_files:
                    return files, omitted, True
    return files, omitted, False


def _execute_sync_git_tree(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 10_000)
    max_bytes = int(payload.get("max_bytes") or 2_000_000)
    max_file_bytes = int(payload.get("max_file_bytes") or 1_000_000)
    files: list[dict[str, Any]] = []
    total_bytes = 0
    candidates, omitted, truncated = _iter_workspace_files(workspace_root, max_files=max_files)
    for path in candidates:
        rel = path.relative_to(workspace_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        if total_bytes + size > max_bytes:
            truncated = True
            omitted.append({"path": rel, "reason": "byte_budget_exceeded"})
            break
        try:
            digest = _hash_file(path)
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue
        total_bytes += size
        files.append(
            {
                "path": rel,
                "bytes": size,
                "sha256": digest,
            }
        )
    project_index = _build_project_index(workspace_root, files)
    return {
        "status": "completed",
        "summary": (
            f"Collected {len(files)} git tree entries; "
            f"indexed {len(project_index['routes'])} route(s), "
            f"{len(project_index['dependency_manifests'])} dependency manifest(s)."
        ),
        "artifact": {
            "schema": "hades.git_tree.v1",
            "root": workspace_root.name,
            "files": files,
            "project_index": project_index,
            "summary": project_index["summary"],
            "omitted": omitted,
            "truncated": truncated,
            "redactions": len(omitted),
            "retention_class": "source_metadata",
            "raw_source_included": False,
        },
    }


def _execute_project_inspection(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    result = _execute_sync_git_tree(job, workspace_root)
    artifact = result.get("artifact")
    if isinstance(artifact, dict):
        artifact["requested_capability"] = "project_inspection"
        artifact["inspection_mode"] = "metadata_tree"
    files = artifact.get("files", []) if isinstance(artifact, dict) else []
    result["summary"] = f"Collected {len(files)} project metadata entries; raw source not included."
    return result


def _php_graph_summary(
    routes: list[dict[str, Any]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    database: dict[str, Any] | None = None,
    tests: dict[str, Any] | None = None,
    logs: dict[str, Any] | None = None,
) -> str:
    role_counts: dict[str, int] = {}
    for symbol in symbols:
        role = str(symbol.get("role") or symbol.get("kind") or "symbol")
        role_counts[role] = role_counts.get(role, 0) + 1
    roles = ", ".join(f"{role}:{count}" for role, count in sorted(role_counts.items())[:8])
    table_count = len((database or {}).get("tables") or [])
    test_count = int((tests or {}).get("file_count") or 0)
    log_count = int((logs or {}).get("event_count") or 0)
    return f"PHP graph; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; tables:{table_count}; tests:{test_count}; logs:{log_count}; {roles or 'roles:none'}"


def _append_laravel_middleware_graph(
    catalog: dict[str, Any],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_symbols: int,
    max_edges: int,
) -> bool:
    truncated = False
    for alias in catalog.get("aliases") or []:
        name = str(alias.get("name") or "").strip()
        class_name = str(alias.get("class") or "").strip()
        if not name or not class_name:
            continue
        if len(symbols) < max_symbols:
            symbols.append(
                {
                    "kind": "middleware_alias",
                    "name": f"middleware:{name}",
                    "alias": name,
                    "class": class_name,
                    "role": "middleware_alias",
                    "path": alias.get("path"),
                    "line": alias.get("line"),
                }
            )
        else:
            truncated = True
        truncated = not _edge_append(
            edges,
            {
                "kind": "middleware_alias_class",
                "from": f"middleware:{name}",
                "to": class_name,
                "path": alias.get("path"),
                "line": alias.get("line"),
            },
            max_edges=max_edges,
        ) or truncated

    alias_map = catalog.get("_alias_map") if isinstance(catalog.get("_alias_map"), dict) else {}
    for group in catalog.get("groups") or []:
        name = str(group.get("name") or "").strip()
        members = [str(item).strip() for item in group.get("members") or [] if str(item).strip()]
        if not name or not members:
            continue
        if len(symbols) < max_symbols:
            symbols.append(
                {
                    "kind": "middleware_group",
                    "name": f"middleware_group:{name}",
                    "group": name,
                    "member_count": len(members),
                    "role": "middleware_group",
                    "path": group.get("path"),
                    "line": group.get("line"),
                }
            )
        else:
            truncated = True
        for member in members:
            target = alias_map.get(member) or (member if "\\" in member else f"middleware:{member}")
            truncated = not _edge_append(
                edges,
                {
                    "kind": "middleware_group_member",
                    "from": f"middleware_group:{name}",
                    "to": target,
                    "path": group.get("path"),
                    "line": group.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_route_edges(
    routes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
    middleware_catalog: dict[str, Any] | None = None,
    php_method_symbols: dict[tuple[str, str], str] | None = None,
) -> bool:
    truncated = False
    php_method_symbols = php_method_symbols or {}
    alias_map = middleware_catalog.get("_alias_map", {}) if isinstance(middleware_catalog, dict) else {}
    group_map = middleware_catalog.get("_group_map", {}) if isinstance(middleware_catalog, dict) else {}

    def append_middleware_method_edge(
        *,
        route_ref: str,
        route: dict[str, Any],
        middleware_name: str,
        middleware_class: str,
        via: str = "",
        middleware_params: list[str] | None = None,
    ) -> bool:
        handle_symbol = php_method_symbols.get((middleware_class, "handle"), "")
        if not handle_symbol:
            return True
        edge = {
            "kind": "route_middleware_method",
            "from": route_ref,
            "to": handle_symbol,
            "middleware": middleware_name,
            "middleware_class": middleware_class,
            "via": via,
            "method": route.get("method"),
            "uri": route.get("uri"),
            "path": route.get("path"),
            "line": route.get("line"),
        }
        if middleware_params:
            edge["middleware_params"] = middleware_params
        return _edge_append(
            edges,
            edge,
            max_edges=max_edges,
        )

    for route in routes:
        route_id = _php_route_id(route)
        route_ref = f"route:{route_id}"
        handler = route.get("handler", "")
        if "@" in handler:
            if not _edge_append(
                edges,
                {
                    "kind": "route_handler",
                    "from": route_ref,
                    "to": handler,
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                },
                max_edges=max_edges,
            ):
                truncated = True
                break
        for middleware in route.get("middleware") or []:
            base_middleware = _middleware_base_name(str(middleware))
            middleware_params = _middleware_parameters(str(middleware))
            route_middleware_edge = {
                "kind": "route_middleware",
                "from": route_ref,
                "to": f"middleware:{middleware}",
                "middleware": base_middleware,
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
                "line": route.get("line"),
            }
            if middleware_params:
                route_middleware_edge["middleware_params"] = middleware_params
            if not _edge_append(
                edges,
                route_middleware_edge,
                max_edges=max_edges,
            ):
                truncated = True
                break
            class_target = alias_map.get(base_middleware)
            if class_target:
                class_edge = {
                    "kind": "route_middleware_class",
                    "from": route_ref,
                    "to": class_target,
                    "middleware": base_middleware,
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                }
                if middleware_params:
                    class_edge["middleware_params"] = middleware_params
                truncated = not _edge_append(
                    edges,
                    class_edge,
                    max_edges=max_edges,
                ) or truncated
                truncated = not append_middleware_method_edge(
                    route_ref=route_ref,
                    route=route,
                    middleware_name=base_middleware,
                    middleware_class=class_target,
                    middleware_params=middleware_params,
                ) or truncated
            group_members = group_map.get(base_middleware) or []
            if group_members:
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "route_middleware_group",
                        "from": route_ref,
                        "to": f"middleware_group:{base_middleware}",
                        "method": route.get("method"),
                        "uri": route.get("uri"),
                        "path": route.get("path"),
                        "line": route.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
                for member in group_members:
                    member_base = _middleware_base_name(str(member))
                    target = alias_map.get(member_base) or (member if "\\" in str(member) else "")
                    if not target:
                        continue
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "route_middleware_class",
                            "from": route_ref,
                            "to": target,
                            "middleware": base_middleware,
                            "via": member,
                            "method": route.get("method"),
                            "uri": route.get("uri"),
                            "path": route.get("path"),
                            "line": route.get("line"),
                        },
                        max_edges=max_edges,
                    ) or truncated
                    truncated = not append_middleware_method_edge(
                        route_ref=route_ref,
                        route=route,
                        middleware_name=base_middleware,
                        middleware_class=target,
                        via=str(member),
                        middleware_params=_middleware_parameters(str(member)),
                    ) or truncated
    return truncated


def _append_php_log_events(
    source: str,
    rel: str,
    classes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    log_events: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    patterns: tuple[tuple[re.Pattern[str], str], ...] = (
        (PHP_LOG_STATIC_RE, ""),
        (PHP_LOG_INSTANCE_RE, ""),
        (PHP_LOGGER_CHAIN_RE, ""),
        (PHP_LOGGER_HELPER_RE, "info"),
    )
    for pattern, default_level in patterns:
        for match in pattern.finditer(source):
            if len(log_events) >= MAX_LOG_EVENTS:
                truncated = True
                break
            level = (match.groupdict().get("level") or default_level or "info").lower()
            if level == "warn":
                level = "warning"
            message = redact_secret(match.group("message") or "")
            logger = match.groupdict().get("logger") or "logger"
            context = _php_method_context_id(source, classes, match.start(), rel)
            payload = {
                "context": context,
                "logger": logger.strip("\\"),
                "level": level,
                "path": rel,
                "line": _line_number(source, match.start()),
                "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest() if message else "",
                "message_length": len(message) if message else 0,
            }
            log_event = {key: value for key, value in payload.items() if value not in ("", None, 0)}
            log_id_payload = json.dumps(log_event, sort_keys=True, separators=(",", ":")).encode("utf-8")
            log_id = hashlib.sha256(log_id_payload).hexdigest()[:16]
            log_event = {"id": f"log:{log_id}", **log_event}
            log_events.append(log_event)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "emits_log",
                    "from": context,
                    "to": log_event["id"],
                    "level": log_event.get("level"),
                    "logger": log_event.get("logger"),
                    "path": rel,
                    "line": log_event.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
        if truncated and len(log_events) >= MAX_LOG_EVENTS:
            break
    return truncated


def _append_php_method_context_edge(
    source: str,
    rel: str,
    classes: list[dict[str, Any]],
    offset: int,
    edges: list[dict[str, Any]],
    edge: dict[str, Any],
    *,
    max_edges: int,
) -> bool:
    class_context = _php_context_id(_class_context(classes, offset), rel)
    method_context = _php_method_context_id(source, classes, offset, rel)
    if method_context == class_context:
        return True
    method_edge = {
        **edge,
        "from": method_context,
        "class_context": class_context,
    }
    return _edge_append(edges, method_edge, max_edges=max_edges)


def _php_query_operation_access(method: str) -> str:
    normalized = method.lower()
    if normalized in PHP_QUERY_WRITE_TERMINALS:
        return "write"
    if normalized in PHP_QUERY_READ_TERMINALS:
        return "read"
    if normalized in PHP_QUERY_SCOPE_METHODS:
        return "scope"
    if normalized in PHP_QUERY_LOCK_METHODS:
        return "lock"
    if normalized in PHP_QUERY_TABLE_METHODS:
        return "join" if normalized.endswith("join") else "table"
    if normalized in PHP_QUERY_FILTER_METHODS:
        return "filter"
    if normalized in PHP_QUERY_SHAPE_METHODS:
        return "shape"
    return "query"


def _php_query_chain_operations(source: str, start: int, main_table: str) -> list[dict[str, Any]]:
    statement_end = source.find(";", start)
    if statement_end == -1:
        statement_end = min(len(source), start + 4000)
    statement = source[start:statement_end]
    operations: list[dict[str, Any]] = []
    for match in PHP_QUERY_CHAIN_CALL_RE.finditer(statement):
        method = match.group("method")
        normalized = method.lower()
        if normalized not in PHP_QUERY_TRACKED_METHODS:
            continue
        table = main_table
        if normalized in PHP_QUERY_TABLE_METHODS:
            args_end = statement.find(")", match.end())
            args = statement[match.end() : args_end if args_end != -1 else len(statement)]
            table_match = PHP_QUOTED_VALUE_RE.search(args)
            if table_match:
                table = table_match.group("value")
        operations.append(
            {
                "method": method,
                "table": table,
                "access": _php_query_operation_access(method),
                "offset": start + match.start(),
            }
        )
    return operations


def _append_php_query_builder_edges(
    source: str,
    rel: str,
    classes: list[dict[str, Any]],
    match: re.Match[str],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    class_info = _class_context(classes, match.start())
    context = _php_context_id(class_info, rel)
    main_table = match.group("table")
    for operation in _php_query_chain_operations(source, match.end(), main_table):
        operation_name = str(operation["method"])
        table = str(operation["table"])
        access = str(operation["access"])
        offset = int(operation["offset"])
        operation_edge = {
            "kind": "query_operation",
            "from": context,
            "to": f"query:{table}:{operation_name}",
            "table": table,
            "operation": operation_name,
            "access": access,
            "path": rel,
            "line": _line_number(source, offset),
        }
        truncated = not _edge_append(edges, operation_edge, max_edges=max_edges) or truncated
        truncated = not _append_php_method_context_edge(
            source,
            rel,
            classes,
            offset,
            edges,
            operation_edge,
            max_edges=max_edges,
        ) or truncated
        if access not in {"read", "write"}:
            continue
        access_edge = {
            "kind": f"query_{access}",
            "from": context,
            "to": f"table:{table}",
            "query_method": operation_name,
            "path": rel,
            "line": _line_number(source, offset),
        }
        truncated = not _edge_append(edges, access_edge, max_edges=max_edges) or truncated
        truncated = not _append_php_method_context_edge(
            source,
            rel,
            classes,
            offset,
            edges,
            access_edge,
            max_edges=max_edges,
        ) or truncated
    return truncated


def _php_laravel_model_table_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    max_file_bytes: int,
) -> dict[str, str]:
    model_tables: dict[str, str] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        model_classes: list[str] = []
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            if _php_role(rel, fqcn, _php_fqcn_resolved(namespace, extends, uses) if extends else "") == "model":
                model_classes.append(fqcn)
        if not model_classes:
            continue
        table_match = PHP_MODEL_TABLE_RE.search(source)
        explicit_table = table_match.group("table") if table_match else ""
        for fqcn in model_classes:
            model_tables[fqcn] = explicit_table or f"{_snake_name(_php_short_name(fqcn))}s"
    return model_tables


def _php_model_table_for_class(resolved_class: str, model_table_by_class: dict[str, str]) -> str:
    table = model_table_by_class.get(resolved_class)
    if table:
        return table
    if "\\Models\\" in resolved_class or resolved_class.startswith("App\\Models\\"):
        return f"{_snake_name(_php_short_name(resolved_class))}s"
    return ""


def _php_is_laravel_api_resource_class(resolved_class: str) -> bool:
    short = _php_short_name(resolved_class)
    return "\\Http\\Resources\\" in resolved_class or short.endswith(("Resource", "ResourceCollection"))


def _php_resource_model_for_class(resource_class: str, model_table_by_class: dict[str, str]) -> str:
    short = _php_short_name(resource_class)
    base = short
    for suffix in ("ResourceCollection", "Collection", "Resource"):
        if base.endswith(suffix) and len(base) > len(suffix):
            base = base[: -len(suffix)]
            break
    if not base or base == short:
        return ""
    for model_class in model_table_by_class:
        if _php_short_name(model_class) == base:
            return model_class
    if resource_class.startswith("App\\Http\\Resources\\") or "\\Http\\Resources\\" in resource_class:
        return f"App\\Models\\{base}"
    return ""


def _php_resource_table_for_class(resource_class: str, model_table_by_class: dict[str, str]) -> tuple[str, str]:
    model_class = _php_resource_model_for_class(resource_class, model_table_by_class)
    if not model_class:
        return "", ""
    return model_class, _php_model_table_for_class(model_class, model_table_by_class)


def _php_scope_name(method_name: str) -> str:
    if not method_name.startswith("scope") or len(method_name) <= len("scope"):
        return ""
    suffix = method_name[len("scope") :]
    if not suffix or not suffix[0].isupper():
        return ""
    return suffix[:1].lower() + suffix[1:]


def _php_model_scope_id(model_class: str, scope_name: str) -> str:
    return f"scope:{model_class}.{scope_name}"


def _php_model_attribute_edges_for_method(
    source: str,
    rel: str,
    class_info: dict[str, Any],
    method_name: str,
    method_match: re.Match[str],
    method_body: str,
    model_table_by_class: dict[str, str],
    fallback_model_table: str,
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if class_info.get("role") != "model":
        return False
    model_class = str(class_info["name"])
    table = _php_model_table_for_class(model_class, model_table_by_class) or fallback_model_table
    attributes: list[dict[str, str]] = []
    classic_match = re.fullmatch(r"(?P<direction>get|set)(?P<field>[A-Z][A-Za-z0-9_]*)Attribute", method_name)
    if classic_match:
        direction = "get" if classic_match.group("direction") == "get" else "set"
        attributes.append(
            {
                "kind": "model_accessor" if direction == "get" else "model_mutator",
                "field": _snake_name(classic_match.group("field")),
                "direction": direction,
                "attribute_style": "classic",
            }
        )

    brace = source.find("{", method_match.end())
    signature_tail = source[method_match.end() : brace if brace != -1 else min(len(source), method_match.end() + 160)]
    if (
        method_name not in {"casts", "boot", "booted"}
        and not method_name.startswith("scope")
        and ("Attribute" in signature_tail or "Attribute::" in method_body or "new Attribute" in method_body)
    ):
        field = _snake_name(method_name)
        if re.search(r"\bget\s*:", method_body) or "Attribute::get" in method_body:
            attributes.append(
                {
                    "kind": "model_accessor",
                    "field": field,
                    "direction": "get",
                    "attribute_style": "attribute_object",
                }
            )
        if re.search(r"\bset\s*:", method_body) or "Attribute::set" in method_body:
            attributes.append(
                {
                    "kind": "model_mutator",
                    "field": field,
                    "direction": "set",
                    "attribute_style": "attribute_object",
                }
            )

    truncated = False
    seen: set[tuple[str, str, str]] = set()
    for attribute in attributes:
        key = (attribute["kind"], attribute["field"], attribute["attribute_style"])
        if key in seen:
            continue
        seen.add(key)
        truncated = not _edge_append(
            edges,
            {
                "kind": attribute["kind"],
                "from": model_class,
                "to": _php_model_field_target(model_class, table, attribute["field"]),
                "field": attribute["field"],
                "direction": attribute["direction"],
                "attribute_style": attribute["attribute_style"],
                "attribute_method": method_name,
                "table": table,
                "path": rel,
                "line": _line_number(source, method_match.start()),
            },
            max_edges=max_edges,
        ) or truncated
    return truncated


def _php_laravel_model_scope_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    max_file_bytes: int,
) -> dict[str, set[str]]:
    model_scopes: dict[str, set[str]] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        classes: list[dict[str, Any]] = []
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            extends_fqcn = _php_fqcn_resolved(namespace, extends, uses) if extends else ""
            role = _php_role(rel, fqcn, extends_fqcn)
            classes.append({"name": fqcn, "role": role, "offset": match.start()})
        classes.sort(key=lambda item: int(item["offset"]))
        for match in PHP_METHOD_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            if class_info is None or class_info.get("role") != "model":
                continue
            scope_name = _php_scope_name(match.group("name"))
            if scope_name:
                model_scopes.setdefault(str(class_info["name"]), set()).add(scope_name)
    return model_scopes


def _php_array_string_values(source: str, body: str, base_offset: int) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in PHP_QUOTED_VALUE_RE.finditer(body):
        value = match.group("value").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append({"value": value, "line": _line_number(source, base_offset + match.start())})
    return values


def _php_array_string_pairs(source: str, body: str, base_offset: int) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in PHP_ARRAY_STRING_PAIR_RE.finditer(body):
        key = match.group("key").strip()
        value = match.group("value").strip()
        if not key or not value or (key, value) in seen:
            continue
        seen.add((key, value))
        pairs.append({"key": key, "value": value, "line": _line_number(source, base_offset + match.start())})
    return pairs


def _php_model_field_target(model_class: str, table: str, field: str) -> str:
    if table and field != "*":
        return f"table:{table}.{field}"
    return f"model_field:{model_class}.{field}"


def _php_model_list_property_target(model_class: str, table: str, property_name: str, field: str) -> str:
    if property_name == "appends":
        return f"model_attribute:{model_class}.{field}"
    return _php_model_field_target(model_class, table, field)


def _php_model_list_property_kind(property_name: str) -> str:
    if property_name == "appends":
        return "model_appended_attribute"
    return f"model_{property_name}"


def _php_model_metadata_classes(classes: list[dict[str, Any]], offset: int) -> list[dict[str, Any]]:
    class_info = _class_context(classes, offset)
    if class_info and class_info.get("role") == "model":
        return [class_info]
    return [class_info for class_info in classes if class_info.get("role") == "model"]


def _append_php_model_trait_edges(
    source: str,
    rel: str,
    classes: list[dict[str, Any]],
    namespace: str,
    uses: dict[str, str],
    fallback_model_table: str,
    model_table_by_class: dict[str, str],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    for match in PHP_CLASS_TRAIT_USE_RE.finditer(source):
        class_info = _class_context(classes, match.start())
        if not class_info or class_info.get("role") != "model":
            continue
        model_class = str(class_info["name"])
        table = _php_model_table_for_class(model_class, model_table_by_class) or fallback_model_table
        for raw_trait in match.group("traits").split(","):
            trait_ref = raw_trait.strip()
            if not trait_ref:
                continue
            trait_class = _php_fqcn_resolved(namespace, trait_ref, uses)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "model_trait",
                    "from": model_class,
                    "to": trait_class,
                    "trait_class": trait_class,
                    "trait_short_name": _php_short_name(trait_class),
                    "table": table,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _append_php_model_metadata_edges(
    source: str,
    rel: str,
    classes: list[dict[str, Any]],
    fallback_model_table: str,
    model_table_by_class: dict[str, str],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    for match in PHP_MODEL_LIST_PROPERTY_RE.finditer(source):
        property_name = match.group("property")
        for field_info in _php_array_string_values(source, match.group("body"), match.start("body")):
            field = str(field_info["value"])
            for class_info in _php_model_metadata_classes(classes, match.start()):
                model_class = str(class_info["name"])
                table = _php_model_table_for_class(model_class, model_table_by_class) or fallback_model_table
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": _php_model_list_property_kind(property_name),
                        "from": model_class,
                        "to": _php_model_list_property_target(model_class, table, property_name, field),
                        "field": field,
                        "property": property_name,
                        "table": table if field != "*" else "",
                        "path": rel,
                        "line": field_info["line"],
                    },
                    max_edges=max_edges,
                ) or truncated

    cast_bodies: list[tuple[re.Match[str], str, int]] = []
    for match in PHP_MODEL_CASTS_PROPERTY_RE.finditer(source):
        cast_bodies.append((match, match.group("body"), match.start("body")))
    for method_match in PHP_MODEL_CASTS_METHOD_RE.finditer(source):
        for return_match in PHP_RETURN_ARRAY_RE.finditer(method_match.group("body")):
            cast_bodies.append(
                (
                    method_match,
                    return_match.group("body"),
                    method_match.start("body") + return_match.start("body"),
                )
            )

    for match, body, base_offset in cast_bodies:
        for cast_info in _php_array_string_pairs(source, body, base_offset):
            field = str(cast_info["key"])
            for class_info in _php_model_metadata_classes(classes, match.start()):
                model_class = str(class_info["name"])
                table = _php_model_table_for_class(model_class, model_table_by_class) or fallback_model_table
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "model_cast",
                        "from": model_class,
                        "to": _php_model_field_target(model_class, table, field),
                        "field": field,
                        "cast_type": cast_info["value"],
                        "table": table,
                        "path": rel,
                        "line": cast_info["line"],
                    },
                    max_edges=max_edges,
                ) or truncated
    return truncated


def _append_php_eloquent_query_builder_edges(
    source: str,
    rel: str,
    classes: list[dict[str, Any]],
    match: re.Match[str],
    *,
    resolved_class: str,
    method_name: str,
    table: str,
    edges: list[dict[str, Any]],
    max_edges: int,
) -> bool:
    truncated = False
    class_info = _class_context(classes, match.start())
    context = _php_context_id(class_info, rel)
    operations: list[dict[str, Any]] = []
    normalized = method_name.lower()
    if normalized in PHP_QUERY_TRACKED_METHODS or method_name in PHP_ELOQUENT_QUERY_METHODS:
        operations.append(
            {
                "method": method_name,
                "table": table,
                "access": _php_query_operation_access(method_name),
                "offset": match.start("method"),
            }
        )
    operations.extend(_php_query_chain_operations(source, match.end(), table))
    for operation in operations:
        operation_name = str(operation["method"])
        operation_table = str(operation["table"])
        access = str(operation["access"])
        offset = int(operation["offset"])
        operation_edge = {
            "kind": "query_operation",
            "from": context,
            "to": f"query:{operation_table}:{operation_name}",
            "table": operation_table,
            "model": resolved_class,
            "operation": operation_name,
            "access": access,
            "path": rel,
            "line": _line_number(source, offset),
        }
        truncated = not _edge_append(edges, operation_edge, max_edges=max_edges) or truncated
        truncated = not _append_php_method_context_edge(
            source,
            rel,
            classes,
            offset,
            edges,
            operation_edge,
            max_edges=max_edges,
        ) or truncated
        if access not in {"read", "write"}:
            continue
        access_edge = {
            "kind": f"query_{access}",
            "from": context,
            "to": f"table:{operation_table}",
            "model": resolved_class,
            "query_method": operation_name,
            "path": rel,
            "line": _line_number(source, offset),
        }
        truncated = not _edge_append(edges, access_edge, max_edges=max_edges) or truncated
        truncated = not _append_php_method_context_edge(
            source,
            rel,
            classes,
            offset,
            edges,
            access_edge,
            max_edges=max_edges,
        ) or truncated
    return truncated


def _append_php_route_model_binding_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    param_name: str,
    param_class: str,
    model_table_by_class: dict[str, str],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    model_table = _php_model_table_for_class(param_class, model_table_by_class)
    if not model_table:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        if param_name not in _php_route_params(route):
            continue
        route_ref = f"route:{_php_route_id(route)}"
        binding_edge = {
            "kind": "route_model_binding",
            "from": route_ref,
            "to": param_class,
            "handler": method_symbol,
            "param": param_name,
            "table": model_table,
            "method": route.get("method"),
            "uri": route.get("uri"),
            "path": route.get("path"),
            "line": route.get("line"),
        }
        truncated = not _edge_append(edges, binding_edge, max_edges=max_edges) or truncated
        table_edge = {
            "kind": "route_model_table",
            "from": route_ref,
            "to": f"table:{model_table}",
            "handler": method_symbol,
            "param": param_name,
            "model": param_class,
            "method": route.get("method"),
            "uri": route.get("uri"),
            "path": route.get("path"),
            "line": route.get("line"),
        }
        truncated = not _edge_append(edges, table_edge, max_edges=max_edges) or truncated
    return truncated


def _php_form_request_validation_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    max_file_bytes: int,
) -> dict[str, list[dict[str, Any]]]:
    validation_fields: dict[str, list[dict[str, Any]]] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        rules_body = _php_rules_method_body(source)
        if rules_body is None:
            continue
        body, base_offset = rules_body
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            extends_fqcn = _php_fqcn_resolved(namespace, extends, uses) if extends else ""
            if _php_role(rel, fqcn, extends_fqcn) != "form_request":
                continue
            validation_fields[fqcn] = [
                {
                    "field": field_info["field"],
                    "rules": field_info.get("rules") or [],
                    "database_rules": field_info.get("database_rules") or [],
                    "path": rel,
                    "line": field_info["line"],
                }
                for field_info in _php_array_validation_fields(source, body, base_offset)
            ]
    return validation_fields


def _append_php_route_form_request_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    param_name: str,
    request_class: str,
    fields: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        route_payload = {
            "handler": method_symbol,
            "param": param_name,
            "method": route.get("method"),
            "uri": route.get("uri"),
            "path": route.get("path"),
            "line": route.get("line"),
        }
        truncated = not _edge_append(
            edges,
            {
                "kind": "route_uses_form_request",
                "from": route_ref,
                "to": request_class,
                **route_payload,
            },
            max_edges=max_edges,
        ) or truncated
        for field in fields:
            field_name = str(field.get("field") or "")
            if not field_name:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_request_validation",
                    "from": route_ref,
                    "to": f"validation:{field_name}",
                    "request_class": request_class,
                    "validation_rules": field.get("rules") or [],
                    "validation_path": field.get("path"),
                    "validation_line": field.get("line"),
                    **route_payload,
                },
                max_edges=max_edges,
            ) or truncated
            for database_rule in field.get("database_rules") or []:
                route_database_rule_edge = {
                    "kind": "route_validation_database_rule",
                    "from": route_ref,
                    "to": _php_validation_database_rule_target(database_rule),
                    "field": field_name,
                    "rule": database_rule.get("rule"),
                    "table": database_rule.get("table"),
                    "request_class": request_class,
                    "validation_path": field.get("path"),
                    "validation_line": field.get("line"),
                    **route_payload,
                }
                if database_rule.get("column"):
                    route_database_rule_edge["column"] = database_rule.get("column")
                truncated = not _edge_append(edges, route_database_rule_edge, max_edges=max_edges) or truncated
    return truncated


def _append_php_route_inline_validation_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    field_info: dict[str, Any],
    validation_path: str,
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    field_name = str(field_info.get("field") or "")
    if not field_name:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        truncated = not _edge_append(
            edges,
            {
                "kind": "route_request_validation",
                "from": route_ref,
                "to": f"validation:{field_name}",
                "handler": method_symbol,
                "source": "inline_validate",
                "validation_rules": field_info.get("rules") or [],
                "validation_path": validation_path,
                "validation_line": field_info.get("line"),
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
                "line": route.get("line"),
            },
            max_edges=max_edges,
        ) or truncated
        for database_rule in field_info.get("database_rules") or []:
            route_database_rule_edge = {
                "kind": "route_validation_database_rule",
                "from": route_ref,
                "to": _php_validation_database_rule_target(database_rule),
                "handler": method_symbol,
                "source": "inline_validate",
                "field": field_name,
                "rule": database_rule.get("rule"),
                "table": database_rule.get("table"),
                "validation_path": validation_path,
                "validation_line": field_info.get("line"),
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
                "line": route.get("line"),
            }
            if database_rule.get("column"):
                route_database_rule_edge["column"] = database_rule.get("column")
            truncated = not _edge_append(edges, route_database_rule_edge, max_edges=max_edges) or truncated
    return truncated


def _php_method_body_slice(source: str, method_match: re.Match[str]) -> tuple[str, int]:
    start = method_match.end()
    next_method = PHP_METHOD_RE.search(source, start)
    end = next_method.start() if next_method else len(source)
    return source[start:end], start


def _php_form_request_authorization_result(source: str, method_match: re.Match[str]) -> str:
    method_body, _offset = _php_method_body_slice(source, method_match)
    if re.search(r"\breturn\s+false\s*;", method_body):
        return "deny"
    if re.search(r"\breturn\s+true\s*;", method_body):
        return "allow"
    return "dynamic"


def _php_form_request_authorization_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    max_file_bytes: int,
) -> dict[str, dict[str, Any]]:
    authorizations: dict[str, dict[str, Any]] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        classes: list[dict[str, Any]] = []
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            extends_fqcn = _php_fqcn_resolved(namespace, extends, uses) if extends else ""
            role = _php_role(rel, fqcn, extends_fqcn)
            classes.append({"name": fqcn, "role": role, "offset": match.start()})
        for method_match in PHP_METHOD_RE.finditer(source):
            if method_match.group("name") != "authorize":
                continue
            class_info = _class_context(classes, method_match.start())
            if not class_info or class_info.get("role") != "form_request":
                continue
            authorizations[str(class_info["name"])] = {
                "authorization_result": _php_form_request_authorization_result(source, method_match),
                "path": rel,
                "line": _line_number(source, method_match.start()),
            }
    return authorizations


def _php_form_request_input_mutations_for_method(
    source: str,
    method_match: re.Match[str],
    *,
    mutation_stage: str,
) -> list[dict[str, Any]]:
    method_body, method_body_offset = _php_method_body_slice(source, method_match)
    mutations: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for mutation_match in PHP_THIS_INPUT_MUTATION_RE.finditer(method_body):
        operation = str(mutation_match.group("operation") or "")
        open_abs = method_body_offset + mutation_match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        args = source[open_abs + 1 : close_abs]
        for field_info in _php_top_level_array_field_keys(source, args, open_abs + 1):
            field = str(field_info.get("field") or "")
            key = (mutation_stage, operation, field)
            if not field or key in seen:
                continue
            seen.add(key)
            mutations.append(
                {
                    "field": field,
                    "operation": operation,
                    "mutation_stage": mutation_stage,
                    "line": field_info["line"],
                }
            )
    return mutations


def _php_form_request_input_mutation_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    max_file_bytes: int,
) -> dict[str, list[dict[str, Any]]]:
    mutations_by_class: dict[str, list[dict[str, Any]]] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        classes: list[dict[str, Any]] = []
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            extends_fqcn = _php_fqcn_resolved(namespace, extends, uses) if extends else ""
            role = _php_role(rel, fqcn, extends_fqcn)
            classes.append({"name": fqcn, "role": role, "offset": match.start()})
        for method_match in PHP_METHOD_RE.finditer(source):
            method_name = method_match.group("name")
            stage_by_method = {
                "prepareForValidation": "prepare_for_validation",
                "passedValidation": "passed_validation",
            }
            mutation_stage = stage_by_method.get(method_name)
            if not mutation_stage:
                continue
            class_info = _class_context(classes, method_match.start())
            if not class_info or class_info.get("role") != "form_request":
                continue
            mutations = _php_form_request_input_mutations_for_method(
                source,
                method_match,
                mutation_stage=mutation_stage,
            )
            for mutation in mutations:
                mutations_by_class.setdefault(str(class_info["name"]), []).append(
                    {
                        **mutation,
                        "path": rel,
                    }
                )
    return mutations_by_class


def _php_method_symbol_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    max_file_bytes: int,
) -> dict[tuple[str, str], str]:
    method_symbols: dict[tuple[str, str], str] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        classes: list[dict[str, Any]] = []
        for match in PHP_CLASS_RE.finditer(source):
            fqcn = _php_fqcn(namespace, match.group("name"))
            classes.append({"name": fqcn, "offset": match.start()})
        for method_match in PHP_METHOD_RE.finditer(source):
            class_info = _class_context(classes, method_match.start())
            if not class_info:
                continue
            fqcn = str(class_info["name"])
            method_name = str(method_match.group("name") or "")
            if method_name:
                method_symbols[(fqcn, method_name)] = f"{_php_short_name(fqcn)}@{method_name}"
    return method_symbols


def _livewire_alias_segment(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    return re.sub(r"[\s_]+", "-", _snake_name(value)).strip("-").lower()


def _livewire_alias_from_path(rel: str, class_name: str) -> str:
    suffix = ""
    for prefix in ("app/Livewire/", "app/Http/Livewire/"):
        if rel.startswith(prefix):
            suffix = rel[len(prefix) :]
            break
    if suffix:
        suffix = suffix.removesuffix(".php")
        parts = [part for part in suffix.split("/") if part]
    else:
        parts = [class_name]
    alias_parts = [_livewire_alias_segment(part) for part in parts]
    return ".".join(part for part in alias_parts if part)


def _php_livewire_component_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    php_method_symbols: dict[tuple[str, str], str],
    max_file_bytes: int,
) -> dict[str, dict[str, Any]]:
    components: dict[str, dict[str, Any]] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        classes: list[dict[str, Any]] = []
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            extends_fqcn = _php_fqcn_resolved(namespace, extends, uses) if extends else ""
            role = _php_role(rel, fqcn, extends_fqcn or extends)
            classes.append(
                {
                    "name": fqcn,
                    "short_name": class_name,
                    "role": role,
                    "offset": match.start(),
                    "line": _line_number(source, match.start()),
                }
            )
        classes.sort(key=lambda item: int(item["offset"]))
        for class_info in classes:
            if class_info.get("role") != "livewire_component":
                continue
            fqcn = str(class_info["name"])
            methods: dict[str, str] = {}
            properties: dict[str, dict[str, Any]] = {}
            for method_match in PHP_METHOD_RE.finditer(source):
                method_class = _class_context(classes, method_match.start())
                if not method_class or method_class.get("name") != fqcn:
                    continue
                if str(method_match.group("visibility") or "public") != "public":
                    continue
                method_name = str(method_match.group("name") or "")
                method_symbol = php_method_symbols.get((fqcn, method_name), "")
                if method_name and method_symbol:
                    methods[method_name] = method_symbol
            for property_match in PHP_PROPERTY_RE.finditer(source):
                property_class = _class_context(classes, property_match.start())
                if not property_class or property_class.get("name") != fqcn:
                    continue
                if str(property_match.group("visibility") or "") != "public":
                    continue
                property_name = str(property_match.group("name") or "")
                if not property_name:
                    continue
                property_type = str(property_match.group("type") or "").lstrip("?")
                properties[property_name] = {
                    "name": property_name,
                    "type": property_type,
                    "line": _line_number(source, property_match.start()),
                }
            validation_fields = _php_livewire_validation_fields(source, classes, fqcn, rel)
            alias = _livewire_alias_from_path(rel, str(class_info.get("short_name") or _php_short_name(fqcn)))
            if alias:
                components[alias] = {
                    "alias": alias,
                    "class": fqcn,
                    "methods": methods,
                    "properties": properties,
                    "validation_fields": validation_fields,
                    "path": rel,
                    "line": class_info.get("line"),
                }
    return components


def _php_policy_mappings(source: str, namespace: str, uses: dict[str, str]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for match in PHP_GATE_POLICY_RE.finditer(source):
        model_class = _php_fqcn_resolved(namespace, str(match.group("model") or ""), uses)
        policy_class = _php_fqcn_resolved(namespace, str(match.group("policy") or ""), uses)
        if model_class and policy_class:
            mappings.append(
                {
                    "model_class": model_class,
                    "policy_class": policy_class,
                    "source": "gate_policy",
                    "offset": match.start(),
                }
            )
    for property_match in PHP_POLICIES_PROPERTY_RE.finditer(source):
        body = str(property_match.group("body") or "")
        body_offset = property_match.start("body")
        for entry_match in PHP_POLICY_MAP_ENTRY_RE.finditer(body):
            model_class = _php_fqcn_resolved(namespace, str(entry_match.group("model") or ""), uses)
            policy_class = _php_fqcn_resolved(namespace, str(entry_match.group("policy") or ""), uses)
            if model_class and policy_class:
                mappings.append(
                    {
                        "model_class": model_class,
                        "policy_class": policy_class,
                        "source": "policies_property",
                        "offset": body_offset + entry_match.start(),
                    }
                )
    return mappings


def _php_policy_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    max_file_bytes: int,
) -> dict[str, str]:
    policy_by_model: dict[str, str] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        for mapping in _php_policy_mappings(source, namespace, uses):
            model_class = str(mapping.get("model_class") or "")
            policy_class = str(mapping.get("policy_class") or "")
            if model_class and policy_class:
                policy_by_model[model_class] = policy_class
    return policy_by_model


def _php_container_binding_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    max_file_bytes: int,
) -> dict[str, dict[str, Any]]:
    bindings: dict[str, dict[str, Any]] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        for match in PHP_CONTAINER_BIND_RE.finditer(source):
            abstract_class = _php_fqcn_resolved(namespace, match.group("abstract"), uses)
            concrete_class = _php_fqcn_resolved(namespace, match.group("concrete"), uses)
            if abstract_class and concrete_class:
                bindings[abstract_class] = {
                    "concrete_class": concrete_class,
                    "binding": match.group("method"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
    return bindings


def _php_command_method_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    php_method_symbols: dict[tuple[str, str], str],
    max_file_bytes: int,
) -> dict[str, dict[str, Any]]:
    commands: dict[str, dict[str, Any]] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        classes: list[dict[str, Any]] = []
        for match in PHP_CLASS_RE.finditer(source):
            fqcn = _php_fqcn(namespace, match.group("name"))
            extends = match.group("extends") or ""
            extends_fqcn = _php_fqcn_resolved(namespace, extends, uses) if extends else ""
            classes.append(
                {
                    "name": fqcn,
                    "role": _php_role(rel, fqcn, extends_fqcn),
                    "offset": match.start(),
                }
            )
        for match in PHP_COMMAND_SIGNATURE_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            command_name = _php_command_name(match.group("signature"))
            if not class_info or not command_name:
                continue
            command_class = str(class_info.get("name") or "")
            command_method = php_method_symbols.get((command_class, "handle"), "")
            if command_class and command_method:
                commands[command_name] = {
                    "command_class": command_class,
                    "command_method": command_method,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
    return commands


def _php_event_listener_method_index(
    workspace_root: Path,
    php_files: list[Path],
    *,
    php_method_symbols: dict[tuple[str, str], str],
    max_file_bytes: int,
) -> dict[str, list[dict[str, Any]]]:
    listener_methods_by_event: dict[str, list[dict[str, Any]]] = {}
    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
        except OSError:
            continue
        if was_truncated:
            continue
        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        for match in PHP_LISTEN_ARRAY_RE.finditer(source):
            event_class = _php_fqcn_resolved(namespace, match.group("event"), uses)
            if not event_class:
                continue
            line = _line_number(source, match.start())
            for listener_match in PHP_CLASS_CONST_RE.finditer(match.group("listeners")):
                listener_class = _php_fqcn_resolved(namespace, listener_match.group("class"), uses)
                listener_method_symbol = php_method_symbols.get((listener_class, "handle"), "")
                if not listener_method_symbol:
                    continue
                listener_methods_by_event.setdefault(event_class, []).append(
                    {
                        "listener_class": listener_class,
                        "listener_method": listener_method_symbol,
                        "listener_path": rel,
                        "listener_line": line,
                    }
                )
    return listener_methods_by_event


def _append_php_route_form_request_input_mutation_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    param_name: str,
    request_class: str,
    mutations: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not mutations:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        for mutation in mutations:
            field = str(mutation.get("field") or "")
            if not field:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_request_input_mutation",
                    "from": f"route:{_php_route_id(route)}",
                    "to": f"request_field:{field}",
                    "request_class": request_class,
                    "handler": method_symbol,
                    "param": param_name,
                    "field": field,
                    "operation": mutation.get("operation"),
                    "mutation_stage": mutation.get("mutation_stage"),
                    "mutation_path": mutation.get("path"),
                    "mutation_line": mutation.get("line"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _append_php_route_form_request_authorization_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    param_name: str,
    request_class: str,
    authorization: dict[str, Any] | None,
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not authorization:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        truncated = not _edge_append(
            edges,
            {
                "kind": "route_request_authorization",
                "from": f"route:{_php_route_id(route)}",
                "to": request_class,
                "handler": method_symbol,
                "param": param_name,
                "authorization_result": authorization.get("authorization_result"),
                "authorization_path": authorization.get("path"),
                "authorization_line": authorization.get("line"),
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
                "line": route.get("line"),
            },
            max_edges=max_edges,
        ) or truncated
    return truncated


def _php_http_aborts_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    aborts: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in PHP_ABORT_HELPER_RE.finditer(method_body):
        suffix = str(match.group("suffix") or "").lower()
        helper = f"abort{suffix}"
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        args = source[open_abs + 1 : close_abs]
        parts = _split_top_level_items(args)
        status_index = 0 if helper == "abort" else 1
        if len(parts) <= status_index:
            continue
        status_raw = parts[status_index][0].strip()
        status_match = re.fullmatch(r"[1-5][0-9]{2}", status_raw)
        if not status_match:
            continue
        status_code = int(status_match.group(0))
        line = _line_number(source, method_body_offset + match.start())
        key = (helper, status_code, line)
        if key in seen:
            continue
        seen.add(key)
        aborts.append(
            {
                "status_code": status_code,
                "abort_helper": helper,
                "line": line,
            }
        )
    return aborts


def _append_php_route_http_abort_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    aborts: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not aborts:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for http_abort in aborts:
            status_code = http_abort.get("status_code")
            if not status_code:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_http_abort",
                    "from": route_ref,
                    "to": f"http_status:{status_code}",
                    "handler": method_symbol,
                    "status_code": status_code,
                    "abort_helper": http_abort.get("abort_helper"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": http_abort.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_status_literal(raw: str) -> int | None:
    status_match = re.fullmatch(r"[1-5][0-9]{2}", raw.strip())
    return int(status_match.group(0)) if status_match else None


def _php_http_response_statuses_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()
    for match in PHP_RESPONSE_HELPER_RE.finditer(method_body):
        helper_method = str(match.group("method") or "")
        response_helper = f"response_{helper_method.lower()}"
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        args = source[open_abs + 1 : close_abs]
        parts = _split_top_level_items(args)
        status_index = 0 if helper_method.lower() == "nocontent" else 1
        if len(parts) <= status_index:
            continue
        status_code = _php_status_literal(parts[status_index][0])
        if status_code is None:
            continue
        line = _line_number(source, method_body_offset + match.start())
        key = (response_helper, status_code, line)
        if key in seen:
            continue
        seen.add(key)
        responses.append(
            {
                "status_code": status_code,
                "response_helper": response_helper,
                "line": line,
            }
        )
    return responses


def _append_php_route_http_response_status_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    responses: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not responses:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for response in responses:
            status_code = response.get("status_code")
            if not status_code:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_http_response_status",
                    "from": route_ref,
                    "to": f"http_status:{status_code}",
                    "handler": method_symbol,
                    "status_code": status_code,
                    "response_helper": response.get("response_helper"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": response.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_cookie_operation(method: str) -> str:
    normalized = method.lower()
    if normalized in {"forget", "withoutcookie"}:
        return "delete"
    return "set"


def _php_cookie_name_literal(raw: str) -> str:
    literal = _php_quoted_literal(raw)
    if not literal or len(literal) > 128:
        return ""
    if "$" in literal or "{" in literal or "}" in literal:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", literal):
        return ""
    return literal


def _php_cookie_access_items(
    *,
    source: str,
    method_body_offset: int,
    relative_call_start: int,
    cookie_method: str,
    args: str,
) -> list[dict[str, Any]]:
    parts = _split_top_level_items(args)
    cookie_name = _php_cookie_name_literal(parts[0][0]) if parts else ""
    if not cookie_name:
        return []
    method = cookie_method.lower().removeprefix("cookie_")
    return [
        {
            "cookie_name": cookie_name,
            "cookie_operation": _php_cookie_operation(method),
            "cookie_method": cookie_method,
            "line": _line_number(source, method_body_offset + relative_call_start),
        }
    ]


def _php_cookie_accesses_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    accesses: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()

    def append_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            key = (
                str(item.get("cookie_name") or ""),
                str(item.get("cookie_operation") or ""),
                str(item.get("cookie_method") or ""),
                int(item.get("line") or 0),
            )
            if not key[0] or key in seen:
                continue
            seen.add(key)
            accesses.append(item)

    for pattern, method_prefix in (
        (PHP_RESPONSE_COOKIE_CHAIN_RE, "cookie_response"),
        (PHP_COOKIE_FACADE_RE, "cookie"),
    ):
        for match in pattern.finditer(method_body):
            open_abs = method_body_offset + match.end() - 1
            close_abs = _balanced_end(source, open_abs, "(", ")")
            if close_abs == -1:
                continue
            method = str(match.group("method") or "").lower()
            append_items(
                _php_cookie_access_items(
                    source=source,
                    method_body_offset=method_body_offset,
                    relative_call_start=match.start(),
                    cookie_method=f"{method_prefix}_{method}",
                    args=source[open_abs + 1 : close_abs],
                )
            )

    for match in PHP_COOKIE_HELPER_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        append_items(
            _php_cookie_access_items(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                cookie_method="cookie_helper",
                args=source[open_abs + 1 : close_abs],
            )
        )
    return accesses


def _append_php_route_cookie_access_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    accesses: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not accesses:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for access in accesses:
            cookie_name = str(access.get("cookie_name") or "")
            if not cookie_name:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_cookie_access",
                    "from": route_ref,
                    "to": f"cookie:{cookie_name}",
                    "handler": method_symbol,
                    "cookie_name": cookie_name,
                    "cookie_operation": access.get("cookie_operation"),
                    "cookie_method": access.get("cookie_method"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": access.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_quoted_literal(raw: str) -> str:
    match = PHP_QUOTED_VALUE_RE.fullmatch(raw.strip())
    return str(match.group("value") or "") if match else ""


def _php_redirect_target_id(redirect_type: str, redirect_target: str) -> str:
    if redirect_type == "route" and redirect_target:
        return f"redirect_route:{redirect_target}"
    if redirect_type == "path" and redirect_target:
        return f"redirect_path:{redirect_target}"
    if redirect_type == "back":
        return "redirect:back"
    return "redirect:unknown"


def _php_redirect_from_call(
    *,
    source: str,
    method_body_offset: int,
    relative_call_start: int,
    helper: str,
    args: str,
) -> dict[str, Any] | None:
    parts = _split_top_level_items(args)
    redirect_type = "unknown"
    redirect_target = ""
    status_code: int | None = None
    helper = helper.lower()
    if helper == "back":
        redirect_type = "back"
        status_index = 0
    elif helper == "redirect_route":
        redirect_type = "route"
        redirect_target = _php_quoted_literal(parts[0][0]) if parts else ""
        status_index = 2
    elif helper in {"redirect_to", "redirect_away"}:
        redirect_type = "path"
        redirect_target = _php_quoted_literal(parts[0][0]) if parts else ""
        status_index = 1
    elif helper == "redirect_back":
        redirect_type = "back"
        status_index = 0
    else:
        redirect_target = _php_quoted_literal(parts[0][0]) if parts else ""
        redirect_type = "path" if redirect_target.startswith("/") else "unknown"
        status_index = 1
    if len(parts) > status_index:
        status_code = _php_status_literal(parts[status_index][0])
        if status_code is not None and not 300 <= status_code <= 399:
            status_code = None
    line = _line_number(source, method_body_offset + relative_call_start)
    return {
        "redirect_type": redirect_type,
        "redirect_target": redirect_target,
        "redirect_to": _php_redirect_target_id(redirect_type, redirect_target),
        "redirect_helper": helper,
        "redirect_status": status_code,
        "line": line,
    }


def _php_http_redirects_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    redirects: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int | None, int]] = set()

    for match in PHP_REDIRECT_CHAIN_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        helper = f"redirect_{str(match.group('method') or '').lower()}"
        redirect = _php_redirect_from_call(
            source=source,
            method_body_offset=method_body_offset,
            relative_call_start=match.start(),
            helper=helper,
            args=source[open_abs + 1 : close_abs],
        )
        if redirect is None:
            continue
        key = (
            str(redirect.get("redirect_type") or ""),
            str(redirect.get("redirect_target") or ""),
            str(redirect.get("redirect_helper") or ""),
            redirect.get("redirect_status"),
            int(redirect.get("line") or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        redirects.append(redirect)

    for match in PHP_REDIRECT_HELPER_RE.finditer(method_body):
        helper = str(match.group("helper") or "").lower()
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        chain_preview = source[close_abs + 1 : close_abs + 20]
        if helper == "redirect" and re.match(r"\s*->\s*(?:route|to|away|back)\s*\(", chain_preview, re.IGNORECASE):
            continue
        redirect = _php_redirect_from_call(
            source=source,
            method_body_offset=method_body_offset,
            relative_call_start=match.start(),
            helper=helper,
            args=source[open_abs + 1 : close_abs],
        )
        if redirect is None:
            continue
        key = (
            str(redirect.get("redirect_type") or ""),
            str(redirect.get("redirect_target") or ""),
            str(redirect.get("redirect_helper") or ""),
            redirect.get("redirect_status"),
            int(redirect.get("line") or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        redirects.append(redirect)
    return redirects


def _append_php_route_http_redirect_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    redirects: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not redirects:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for redirect in redirects:
            redirect_to = str(redirect.get("redirect_to") or "")
            if not redirect_to:
                continue
            edge = {
                "kind": "route_http_redirect",
                "from": route_ref,
                "to": redirect_to,
                "handler": method_symbol,
                "redirect_type": redirect.get("redirect_type"),
                "redirect_helper": redirect.get("redirect_helper"),
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
                "line": route.get("line"),
                "source_path": rel,
                "source_line": redirect.get("line"),
            }
            if redirect.get("redirect_target"):
                edge["redirect_target"] = redirect.get("redirect_target")
            if redirect.get("redirect_status"):
                edge["redirect_status"] = redirect.get("redirect_status")
            truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
    return truncated


def _php_session_operation(method: str) -> str:
    normalized = method.lower()
    if normalized in {"put"}:
        return "write"
    if normalized == "flash":
        return "flash"
    if normalized in {"forget", "remove"}:
        return "delete"
    if normalized == "pull":
        return "read_delete"
    if normalized == "has":
        return "check"
    return "read"


def _php_session_access_items(
    *,
    source: str,
    method_body_offset: int,
    relative_call_start: int,
    session_method: str,
    args: str,
) -> list[dict[str, Any]]:
    line = _line_number(source, method_body_offset + relative_call_start)
    parts = _split_top_level_items(args)
    method = session_method.lower()
    items: list[dict[str, Any]] = []
    if method == "session_helper":
        stripped = args.strip()
        if stripped.startswith("["):
            for field_match in PHP_ARRAY_FIELD_KEY_RE.finditer(stripped):
                session_key = str(field_match.group("field") or "")
                if not session_key:
                    continue
                items.append(
                    {
                        "session_key": session_key,
                        "session_operation": "write",
                        "session_method": session_method,
                        "line": line,
                    }
                )
            return items
        session_key = _php_quoted_literal(parts[0][0]) if parts else ""
        if session_key:
            items.append(
                {
                    "session_key": session_key,
                    "session_operation": "read",
                    "session_method": session_method,
                    "line": line,
                }
            )
        return items

    session_key = _php_quoted_literal(parts[0][0]) if parts else ""
    if not session_key:
        return []
    items.append(
        {
            "session_key": session_key,
            "session_operation": _php_session_operation(method.removeprefix("session_").removeprefix("request_session_")),
            "session_method": session_method,
            "line": line,
        }
    )
    return items


def _php_session_accesses_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    accesses: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()

    def append_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            key = (
                str(item.get("session_key") or ""),
                str(item.get("session_operation") or ""),
                str(item.get("session_method") or ""),
                int(item.get("line") or 0),
            )
            if not key[0] or key in seen:
                continue
            seen.add(key)
            accesses.append(item)

    for pattern, method_prefix in (
        (PHP_SESSION_CHAIN_RE, "session"),
        (PHP_REQUEST_SESSION_CHAIN_RE, "request_session"),
        (PHP_SESSION_FACADE_RE, "session"),
    ):
        for match in pattern.finditer(method_body):
            open_abs = method_body_offset + match.end() - 1
            close_abs = _balanced_end(source, open_abs, "(", ")")
            if close_abs == -1:
                continue
            method = str(match.group("method") or "").lower()
            append_items(
                _php_session_access_items(
                    source=source,
                    method_body_offset=method_body_offset,
                    relative_call_start=match.start(),
                    session_method=f"{method_prefix}_{method}",
                    args=source[open_abs + 1 : close_abs],
                )
            )

    for match in PHP_SESSION_HELPER_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        chain_preview = source[close_abs + 1 : close_abs + 20]
        if re.match(r"\s*->\s*(?:get|put|flash|forget|has|pull|remove)\s*\(", chain_preview, re.IGNORECASE):
            continue
        append_items(
            _php_session_access_items(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                session_method="session_helper",
                args=source[open_abs + 1 : close_abs],
            )
        )
    return accesses


def _append_php_route_session_access_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    accesses: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not accesses:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for access in accesses:
            session_key = str(access.get("session_key") or "")
            if not session_key:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_session_access",
                    "from": route_ref,
                    "to": f"session_key:{session_key}",
                    "handler": method_symbol,
                    "session_key": session_key,
                    "session_operation": access.get("session_operation"),
                    "session_method": access.get("session_method"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": access.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_cache_operation(method: str) -> str:
    normalized = method.lower()
    if normalized in {"put", "add", "forever", "increment", "decrement"}:
        return "write"
    if normalized in {"remember", "rememberforever"}:
        return "read_write"
    if normalized == "forget":
        return "delete"
    if normalized == "pull":
        return "read_delete"
    if normalized == "has":
        return "check"
    return "read"


def _php_cache_ttl_present(method: str, parts: list[tuple[str, int]]) -> bool:
    normalized = method.lower()
    if normalized in {"put", "add", "remember"}:
        return len(parts) > 1
    return False


def _php_cache_access_items(
    *,
    source: str,
    method_body_offset: int,
    relative_call_start: int,
    cache_method: str,
    args: str,
) -> list[dict[str, Any]]:
    line = _line_number(source, method_body_offset + relative_call_start)
    parts = _split_top_level_items(args)
    method = cache_method.lower()
    items: list[dict[str, Any]] = []
    if method == "cache_helper":
        stripped = args.strip()
        if stripped.startswith("["):
            for field_match in PHP_ARRAY_FIELD_KEY_RE.finditer(stripped):
                cache_key = str(field_match.group("field") or "")
                if not cache_key:
                    continue
                items.append(
                    {
                        "cache_key": cache_key,
                        "cache_operation": "write",
                        "cache_method": cache_method,
                        "cache_ttl_present": False,
                        "line": line,
                    }
                )
            return items
        cache_key = _php_quoted_literal(parts[0][0]) if parts else ""
        if cache_key:
            items.append(
                {
                    "cache_key": cache_key,
                    "cache_operation": "read",
                    "cache_method": cache_method,
                    "cache_ttl_present": False,
                    "line": line,
                }
            )
        return items

    cache_key = _php_quoted_literal(parts[0][0]) if parts else ""
    if not cache_key:
        return []
    base_method = method.removeprefix("cache_")
    items.append(
        {
            "cache_key": cache_key,
            "cache_operation": _php_cache_operation(base_method),
            "cache_method": cache_method,
            "cache_ttl_present": _php_cache_ttl_present(base_method, parts),
            "line": line,
        }
    )
    return items


def _php_cache_accesses_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    accesses: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, bool, int]] = set()

    def append_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            key = (
                str(item.get("cache_key") or ""),
                str(item.get("cache_operation") or ""),
                str(item.get("cache_method") or ""),
                bool(item.get("cache_ttl_present")),
                int(item.get("line") or 0),
            )
            if not key[0] or key in seen:
                continue
            seen.add(key)
            accesses.append(item)

    for pattern in (PHP_CACHE_CHAIN_RE, PHP_CACHE_FACADE_RE):
        for match in pattern.finditer(method_body):
            open_abs = method_body_offset + match.end() - 1
            close_abs = _balanced_end(source, open_abs, "(", ")")
            if close_abs == -1:
                continue
            method = str(match.group("method") or "").lower()
            append_items(
                _php_cache_access_items(
                    source=source,
                    method_body_offset=method_body_offset,
                    relative_call_start=match.start(),
                    cache_method=f"cache_{method}",
                    args=source[open_abs + 1 : close_abs],
                )
            )

    for match in PHP_CACHE_HELPER_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        chain_preview = source[close_abs + 1 : close_abs + 24]
        if re.match(
            r"\s*->\s*(?:get|put|add|forever|remember|rememberForever|forget|has|pull|increment|decrement)\s*\(",
            chain_preview,
            re.IGNORECASE,
        ):
            continue
        append_items(
            _php_cache_access_items(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                cache_method="cache_helper",
                args=source[open_abs + 1 : close_abs],
            )
        )
    return accesses


def _append_php_route_cache_access_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    accesses: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not accesses:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for access in accesses:
            cache_key = str(access.get("cache_key") or "")
            if not cache_key:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_cache_access",
                    "from": route_ref,
                    "to": f"cache_key:{cache_key}",
                    "handler": method_symbol,
                    "cache_key": cache_key,
                    "cache_operation": access.get("cache_operation"),
                    "cache_method": access.get("cache_method"),
                    "cache_ttl_present": bool(access.get("cache_ttl_present")),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": access.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_db_transaction_operation(method: str) -> str:
    normalized = method.lower()
    if normalized == "begintransaction":
        return "begin"
    if normalized == "rollback":
        return "rollback"
    return normalized


def _php_db_transactions_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    transactions: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in PHP_DB_TRANSACTION_RE.finditer(method_body):
        method = str(match.group("method") or "")
        operation = _php_db_transaction_operation(method)
        line = _line_number(source, method_body_offset + match.start())
        key = (operation, line)
        if key in seen:
            continue
        seen.add(key)
        transactions.append(
            {
                "transaction_operation": operation,
                "transaction_method": f"DB::{method}",
                "line": line,
            }
        )
    return transactions


def _append_php_route_db_transaction_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    transactions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not transactions:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for transaction in transactions:
            operation = str(transaction.get("transaction_operation") or "")
            if not operation:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_db_transaction",
                    "from": route_ref,
                    "to": f"db_transaction:{operation}",
                    "handler": method_symbol,
                    "transaction_operation": operation,
                    "transaction_method": transaction.get("transaction_method"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": transaction.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_http_target_from_url(raw: str) -> dict[str, Any] | None:
    literal = _php_quoted_literal(raw)
    if not literal:
        return None
    parsed = urlsplit(literal)
    try:
        parsed_host = parsed.hostname
        parsed_port = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed_host:
        return None
    host = parsed_host.lower()
    if parsed_port:
        host = f"{host}:{parsed_port}"
    path = parsed.path or ""
    target = f"http_endpoint:{host}{path}" if path and path != "/" else f"http_host:{host}"
    return {
        "http_target": target,
        "http_scheme": parsed.scheme.lower(),
        "http_host": host,
        "http_path": path,
    }


def _php_outbound_http_call_from_args(
    *,
    source: str,
    method_body_offset: int,
    relative_call_start: int,
    http_method_name: str,
    http_call_method: str,
    args: str,
) -> dict[str, Any] | None:
    parts = _split_top_level_items(args)
    normalized_method = http_method_name.lower()
    http_method = normalized_method.upper()
    url_index = 0
    if normalized_method == "send":
        if len(parts) < 2:
            return None
        literal_method = _php_quoted_literal(parts[0][0])
        if literal_method and re.fullmatch(r"[A-Za-z]+", literal_method):
            http_method = literal_method.upper()
        url_index = 1
    if len(parts) <= url_index:
        return None
    target = _php_http_target_from_url(parts[url_index][0])
    if target is None:
        return None
    return {
        **target,
        "http_client": "laravel_http",
        "http_method": http_method,
        "http_call_method": http_call_method,
        "line": _line_number(source, method_body_offset + relative_call_start),
    }


def _php_outbound_http_calls_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()

    def append_call(call: dict[str, Any] | None) -> None:
        if call is None:
            return
        key = (
            str(call.get("http_method") or ""),
            str(call.get("http_target") or ""),
            str(call.get("http_call_method") or ""),
            int(call.get("line") or 0),
        )
        if not key[0] or not key[1] or key in seen:
            return
        seen.add(key)
        calls.append(call)

    for match in PHP_HTTP_FACADE_CHAIN_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        method = str(match.group("method") or "").lower()
        prefix = str(match.group("prefix") or "")
        append_call(
            _php_outbound_http_call_from_args(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                http_method_name=method,
                http_call_method=f"Http::{prefix}->{method}",
                args=source[open_abs + 1 : close_abs],
            )
        )

    for match in PHP_HTTP_FACADE_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        method = str(match.group("method") or "").lower()
        append_call(
            _php_outbound_http_call_from_args(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                http_method_name=method,
                http_call_method=f"Http::{method}",
                args=source[open_abs + 1 : close_abs],
            )
        )
    return calls


def _append_php_route_outbound_http_call_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    calls: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not calls:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for call in calls:
            http_target = str(call.get("http_target") or "")
            if not http_target:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_outbound_http_call",
                    "from": route_ref,
                    "to": http_target,
                    "handler": method_symbol,
                    "http_client": call.get("http_client"),
                    "http_method": call.get("http_method"),
                    "http_scheme": call.get("http_scheme"),
                    "http_host": call.get("http_host"),
                    "http_path": call.get("http_path"),
                    "http_call_method": call.get("http_call_method"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": call.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_storage_operation(method: str) -> str:
    normalized = method.lower()
    if normalized in {"put", "prepend", "append", "makedirectory"}:
        return "write"
    if normalized in {"delete", "deletedirectory"}:
        return "delete"
    if normalized in {"exists", "missing"}:
        return "check"
    if normalized in {"files", "allfiles", "directories"}:
        return "list"
    if normalized in {"url", "temporaryurl", "path"}:
        return "resolve"
    return "read"


def _php_storage_path_literal(raw: str) -> str:
    literal = _php_quoted_literal(raw)
    if not literal or len(literal) > 256:
        return ""
    if "$" in literal or "{" in literal or "}" in literal:
        return ""
    return literal.strip("/")


def _php_storage_access_from_args(
    *,
    source: str,
    method_body_offset: int,
    relative_call_start: int,
    storage_method: str,
    storage_disk: str,
    args: str,
) -> dict[str, Any] | None:
    parts = _split_top_level_items(args)
    storage_path = _php_storage_path_literal(parts[0][0]) if parts else ""
    if not storage_path:
        return None
    method = storage_method.lower()
    return {
        "storage_disk": storage_disk or "default",
        "storage_path": storage_path,
        "storage_operation": _php_storage_operation(method.removeprefix("storage_")),
        "storage_method": storage_method,
        "line": _line_number(source, method_body_offset + relative_call_start),
    }


def _php_storage_accesses_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    accesses: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, int]] = set()

    def append_access(access: dict[str, Any] | None) -> None:
        if access is None:
            return
        key = (
            str(access.get("storage_disk") or ""),
            str(access.get("storage_path") or ""),
            str(access.get("storage_operation") or ""),
            str(access.get("storage_method") or ""),
            int(access.get("line") or 0),
        )
        if not key[0] or not key[1] or key in seen:
            return
        seen.add(key)
        accesses.append(access)

    for match in PHP_STORAGE_DISK_CHAIN_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        selector = str(match.group("selector") or "").lower()
        selector_args = str(match.group("selector_args") or "")
        storage_disk = _php_quoted_literal(selector_args) if selector == "disk" else "cloud"
        method = str(match.group("method") or "").lower()
        append_access(
            _php_storage_access_from_args(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                storage_method=f"storage_{method}",
                storage_disk=storage_disk or "dynamic",
                args=source[open_abs + 1 : close_abs],
            )
        )

    for match in PHP_STORAGE_FACADE_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        method = str(match.group("method") or "").lower()
        append_access(
            _php_storage_access_from_args(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                storage_method=f"storage_{method}",
                storage_disk="default",
                args=source[open_abs + 1 : close_abs],
            )
        )
    return accesses


def _append_php_route_storage_access_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    accesses: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not accesses:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for access in accesses:
            storage_path = str(access.get("storage_path") or "")
            storage_disk = str(access.get("storage_disk") or "default")
            if not storage_path:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_storage_access",
                    "from": route_ref,
                    "to": f"storage_path:{storage_disk}:{storage_path}",
                    "handler": method_symbol,
                    "storage_disk": storage_disk,
                    "storage_path": storage_path,
                    "storage_operation": access.get("storage_operation"),
                    "storage_method": access.get("storage_method"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": access.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_request_input_source(method: str) -> str:
    normalized = method.lower()
    if normalized == "query":
        return "query"
    if normalized == "header":
        return "header"
    if normalized == "cookie":
        return "cookie"
    if normalized == "route":
        return "route_param"
    return "input"


def _php_request_input_field_literal(raw: str) -> str:
    literal = _php_quoted_literal(raw)
    if not literal or len(literal) > 128:
        return ""
    if "$" in literal or "{" in literal or "}" in literal:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.*:-]+", literal):
        return ""
    return literal


def _php_request_input_access_items(
    *,
    source: str,
    method_body_offset: int,
    relative_call_start: int,
    input_method: str,
    args: str,
) -> list[dict[str, Any]]:
    parts = _split_top_level_items(args)
    field = _php_request_input_field_literal(parts[0][0]) if parts else ""
    if not field:
        return []
    method = input_method.lower()
    return [
        {
            "field": field,
            "input_source": _php_request_input_source(method.removeprefix("request_")),
            "input_method": input_method,
            "line": _line_number(source, method_body_offset + relative_call_start),
        }
    ]


def _php_request_input_accesses_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    accesses: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()

    def append_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            key = (
                str(item.get("field") or ""),
                str(item.get("input_source") or ""),
                str(item.get("input_method") or ""),
                int(item.get("line") or 0),
            )
            if not key[0] or key in seen:
                continue
            seen.add(key)
            accesses.append(item)

    for match in PHP_REQUEST_INPUT_CHAIN_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        method = str(match.group("method") or "").lower()
        append_items(
            _php_request_input_access_items(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                input_method=f"request_{method}",
                args=source[open_abs + 1 : close_abs],
            )
        )

    for match in PHP_REQUEST_HELPER_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        chain_preview = source[close_abs + 1 : close_abs + 20]
        if re.match(r"\s*->\s*(?:input|get|query|header|cookie|route)\s*\(", chain_preview, re.IGNORECASE):
            continue
        append_items(
            _php_request_input_access_items(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                input_method="request_helper",
                args=source[open_abs + 1 : close_abs],
            )
        )
    return accesses


def _append_php_route_request_input_access_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    accesses: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not accesses:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for access in accesses:
            field = str(access.get("field") or "")
            if not field:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_request_input_access",
                    "from": route_ref,
                    "to": f"request_field:{field}",
                    "handler": method_symbol,
                    "field": field,
                    "input_source": access.get("input_source"),
                    "input_method": access.get("input_method"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": access.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_request_file_operation(method: str) -> str:
    return "check" if method.lower() == "hasfile" else "read"


def _php_request_file_access_items(
    *,
    source: str,
    method_body_offset: int,
    relative_call_start: int,
    file_method: str,
    args: str,
) -> list[dict[str, Any]]:
    parts = _split_top_level_items(args)
    field = _php_request_input_field_literal(parts[0][0]) if parts else ""
    if not field:
        return []
    method = file_method.lower().removeprefix("request_")
    return [
        {
            "file_field": field,
            "file_operation": _php_request_file_operation(method),
            "file_method": file_method,
            "line": _line_number(source, method_body_offset + relative_call_start),
        }
    ]


def _php_request_file_accesses_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
) -> list[dict[str, Any]]:
    accesses: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int]] = set()

    def append_items(items: list[dict[str, Any]]) -> None:
        for item in items:
            key = (
                str(item.get("file_field") or ""),
                str(item.get("file_operation") or ""),
                str(item.get("file_method") or ""),
                int(item.get("line") or 0),
            )
            if not key[0] or key in seen:
                continue
            seen.add(key)
            accesses.append(item)

    for match in PHP_REQUEST_FILE_CHAIN_RE.finditer(method_body):
        open_abs = method_body_offset + match.end() - 1
        close_abs = _balanced_end(source, open_abs, "(", ")")
        if close_abs == -1:
            continue
        method = str(match.group("method") or "").lower()
        append_items(
            _php_request_file_access_items(
                source=source,
                method_body_offset=method_body_offset,
                relative_call_start=match.start(),
                file_method=f"request_{method}",
                args=source[open_abs + 1 : close_abs],
            )
        )
    return accesses


def _append_php_route_request_file_access_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    accesses: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not accesses:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for access in accesses:
            file_field = str(access.get("file_field") or "")
            if not file_field:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_request_file_access",
                    "from": route_ref,
                    "to": f"request_file:{file_field}",
                    "handler": method_symbol,
                    "file_field": file_field,
                    "file_operation": access.get("file_operation"),
                    "file_method": access.get("file_method"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": access.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_class_property_type_map(
    source: str,
    classes: list[dict[str, Any]],
    namespace: str,
    uses: dict[str, str],
) -> dict[str, dict[str, str]]:
    property_types: dict[str, dict[str, str]] = {}
    for property_match in PHP_PROPERTY_RE.finditer(source):
        class_info = _class_context(classes, property_match.start())
        if not class_info:
            continue
        target_class = _php_resolved_simple_type(namespace, str(property_match.group("type") or ""), uses)
        if not target_class:
            continue
        property_types.setdefault(str(class_info["name"]), {})[str(property_match.group("name") or "")] = target_class

    for method_match in PHP_METHOD_RE.finditer(source):
        if method_match.group("name") != "__construct":
            continue
        class_info = _class_context(classes, method_match.start())
        if not class_info:
            continue
        class_name = str(class_info["name"])
        constructor_param_types: dict[str, str] = {}
        params = method_match.group("params") or ""
        for param_match in PHP_TYPED_PARAM_RE.finditer(params):
            param_name = str(param_match.group("name") or "")
            target_class = _php_resolved_simple_type(namespace, str(param_match.group("class") or ""), uses)
            if param_name and target_class:
                constructor_param_types[param_name] = target_class
        for promoted_match in PHP_PROMOTED_PROPERTY_PARAM_RE.finditer(params):
            property_name = str(promoted_match.group("name") or "")
            target_class = _php_resolved_simple_type(namespace, str(promoted_match.group("class") or ""), uses)
            if property_name and target_class:
                property_types.setdefault(class_name, {})[property_name] = target_class
        if not constructor_param_types:
            continue
        method_body, _ = _php_method_body_slice(source, method_match)
        for assign_match in PHP_THIS_PROPERTY_ASSIGN_RE.finditer(method_body):
            property_name = str(assign_match.group("property") or "")
            param_name = str(assign_match.group("param") or "")
            target_class = constructor_param_types.get(param_name, "")
            if property_name and target_class:
                property_types.setdefault(class_name, {})[property_name] = target_class
    return property_types


def _append_php_instance_method_call_edges(
    source: str,
    rel: str,
    classes: list[dict[str, Any]],
    method_body: str,
    method_body_offset: int,
    method_symbol: str,
    class_property_types: dict[str, str],
    typed_params: dict[str, str],
    php_method_symbols: dict[tuple[str, str], str],
    container_bindings: dict[str, dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not typed_params:
        return False
    truncated = False
    for match in PHP_INSTANCE_METHOD_CALL_RE.finditer(method_body):
        receiver = str(match.group("receiver") or "")
        call_type = "instance"
        if receiver.startswith("$this->"):
            receiver_name = receiver.split("->", 1)[1]
            target_class = class_property_types.get(receiver_name, "")
            call_type = "property"
        elif receiver.startswith("$"):
            receiver_name = receiver[1:]
            target_class = typed_params.get(receiver_name, "")
        else:
            continue
        if not target_class:
            continue
        target_method = str(match.group("method") or "")
        target_method_symbol = php_method_symbols.get((target_class, target_method))
        binding_info: dict[str, Any] = {}
        abstract_class = ""
        if not target_method_symbol:
            binding_info = container_bindings.get(target_class, {})
            concrete_class = str(binding_info.get("concrete_class") or "")
            if concrete_class:
                concrete_method_symbol = php_method_symbols.get((concrete_class, target_method))
                if concrete_method_symbol:
                    abstract_class = target_class
                    target_class = concrete_class
                    target_method_symbol = concrete_method_symbol
        if not target_method_symbol:
            continue
        call_edge = {
            "kind": "calls_method",
            "from": method_symbol,
            "to": target_method_symbol,
            "target_class": target_class,
            "call_type": call_type,
            "receiver": receiver_name,
            "target_method": target_method,
            "path": rel,
            "line": _line_number(source, method_body_offset + match.start()),
        }
        if abstract_class:
            call_edge["abstract_class"] = abstract_class
            call_edge["binding"] = binding_info.get("binding")
        truncated = not _edge_append(edges, call_edge, max_edges=max_edges) or truncated
    return truncated


def _append_php_model_instance_operation_edges(
    source: str,
    rel: str,
    method_body: str,
    method_body_offset: int,
    method_symbol: str,
    typed_params: dict[str, str],
    routes_by_handler: dict[str, list[dict[str, Any]]],
    model_table_by_class: dict[str, str],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not typed_params:
        return False
    truncated = False
    for match in PHP_INSTANCE_METHOD_CALL_RE.finditer(method_body):
        receiver = str(match.group("receiver") or "")
        if not receiver.startswith("$") or receiver.startswith("$this->"):
            continue
        receiver_name = receiver[1:]
        model_class = typed_params.get(receiver_name, "")
        model_table = _php_model_table_for_class(model_class, model_table_by_class)
        if not model_table:
            continue
        operation = str(match.group("method") or "")
        access = PHP_MODEL_INSTANCE_OPERATION_ACCESS.get(operation)
        if not access:
            continue
        source_line = _line_number(source, method_body_offset + match.start())
        target = f"model_operation:{model_table}:{operation}"
        operation_payload = {
            "model": model_class,
            "table": model_table,
            "operation": operation,
            "access": access,
            "receiver": receiver_name,
            "source_path": rel,
            "source_line": source_line,
        }
        truncated = not _edge_append(
            edges,
            {
                "kind": "model_instance_operation",
                "from": method_symbol,
                "to": target,
                **operation_payload,
                "path": rel,
                "line": source_line,
            },
            max_edges=max_edges,
        ) or truncated
        for route in routes_by_handler.get(method_symbol) or []:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_model_instance_operation",
                    "from": f"route:{_php_route_id(route)}",
                    "to": target,
                    **operation_payload,
                    "handler": method_symbol,
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _php_throw_exceptions_for_method(
    source: str,
    method_body: str,
    method_body_offset: int,
    namespace: str,
    uses: dict[str, str],
) -> list[dict[str, Any]]:
    exceptions: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in PHP_THROW_NEW_RE.finditer(method_body):
        exception_class = _php_fqcn_resolved(namespace, str(match.group("class") or ""), uses)
        if not exception_class:
            continue
        line = _line_number(source, method_body_offset + match.start())
        key = (exception_class, line)
        if key in seen:
            continue
        seen.add(key)
        exceptions.append(
            {
                "exception_class": exception_class,
                "exception_short_name": _php_short_name(exception_class),
                "line": line,
            }
        )
    return exceptions


def _append_php_route_throw_exception_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    exceptions: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    if not exceptions:
        return False
    truncated = False
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        for exception in exceptions:
            exception_class = str(exception.get("exception_class") or "")
            if not exception_class:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_throws_exception",
                    "from": route_ref,
                    "to": exception_class,
                    "handler": method_symbol,
                    "exception_class": exception_class,
                    "exception_short_name": exception.get("exception_short_name"),
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": exception.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _append_php_emitted_event_listener_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    source_line: int,
    *,
    event_class: str,
    listener_methods: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    max_edges: int,
) -> bool:
    if not method_symbol or not event_class or not listener_methods:
        return False
    truncated = False
    for listener in listener_methods:
        listener_method = str(listener.get("listener_method") or "")
        listener_class = str(listener.get("listener_class") or "")
        if not listener_method:
            continue
        payload = {
            "event_class": event_class,
            "listener_class": listener_class,
            "listener_path": listener.get("listener_path"),
            "listener_line": listener.get("listener_line"),
            "path": rel,
            "line": source_line,
        }
        truncated = not _edge_append(
            edges,
            {
                "kind": "emits_event_listener",
                "from": method_symbol,
                "to": listener_method,
                **payload,
            },
            max_edges=max_edges,
        ) or truncated
        for route in routes_by_handler.get(method_symbol) or []:
            route_ref = f"route:{_php_route_id(route)}"
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_emits_event_listener",
                    "from": route_ref,
                    "to": listener_method,
                    "handler": method_symbol,
                    "event_class": event_class,
                    "listener_class": listener_class,
                    "method": route.get("method"),
                    "uri": route.get("uri"),
                    "path": route.get("path"),
                    "line": route.get("line"),
                    "source_path": rel,
                    "source_line": source_line,
                    "listener_path": listener.get("listener_path"),
                    "listener_line": listener.get("listener_line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _append_php_dispatched_job_method_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    source_line: int,
    *,
    job_class: str,
    job_method_symbol: str,
    dispatch_method: str,
    edges: list[dict[str, Any]],
    max_edges: int,
) -> bool:
    if not method_symbol or not job_class or not job_method_symbol:
        return False
    truncated = False
    payload = {
        "job_class": job_class,
        "job_method": "handle",
        "dispatch_method": dispatch_method,
        "path": rel,
        "line": source_line,
    }
    truncated = not _edge_append(
        edges,
        {
            "kind": "dispatches_job_method",
            "from": method_symbol,
            "to": job_method_symbol,
            **payload,
        },
        max_edges=max_edges,
    ) or truncated
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        truncated = not _edge_append(
            edges,
            {
                "kind": "route_dispatches_job_method",
                "from": route_ref,
                "to": job_method_symbol,
                "handler": method_symbol,
                "job_class": job_class,
                "job_method": "handle",
                "dispatch_method": dispatch_method,
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
                "line": route.get("line"),
                "source_path": rel,
                "source_line": source_line,
            },
            max_edges=max_edges,
            ) or truncated
    return truncated


def _php_first_method_symbol(
    php_method_symbols: dict[tuple[str, str], str],
    class_name: str,
    method_names: tuple[str, ...],
) -> tuple[str, str]:
    for method_name in method_names:
        method_symbol = php_method_symbols.get((class_name, method_name), "")
        if method_symbol:
            return method_symbol, method_name
    return "", ""


def _append_php_mail_method_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    source_line: int,
    *,
    mailable_class: str,
    mailable_method_symbol: str,
    mailable_method: str,
    mail_method: str,
    edges: list[dict[str, Any]],
    max_edges: int,
) -> bool:
    if not method_symbol or not mailable_class or not mailable_method_symbol:
        return False
    truncated = False
    payload = {
        "mailable_class": mailable_class,
        "mailable_method": mailable_method,
        "mail_method": mail_method,
        "path": rel,
        "line": source_line,
    }
    truncated = not _edge_append(
        edges,
        {
            "kind": "sends_mail_method",
            "from": method_symbol,
            "to": mailable_method_symbol,
            **payload,
        },
        max_edges=max_edges,
    ) or truncated
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        truncated = not _edge_append(
            edges,
            {
                "kind": "route_sends_mail_method",
                "from": route_ref,
                "to": mailable_method_symbol,
                "handler": method_symbol,
                "mailable_class": mailable_class,
                "mailable_method": mailable_method,
                "mail_method": mail_method,
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
                "line": route.get("line"),
                "source_path": rel,
                "source_line": source_line,
            },
            max_edges=max_edges,
        ) or truncated
    return truncated


def _append_php_notification_method_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    source_line: int,
    *,
    notification_class: str,
    notification_method_symbol: str,
    notification_method: str,
    notification_source: str,
    edges: list[dict[str, Any]],
    max_edges: int,
) -> bool:
    if not method_symbol or not notification_class or not notification_method_symbol:
        return False
    truncated = False
    payload = {
        "notification_class": notification_class,
        "notification_method": notification_method,
        "notification_source": notification_source,
        "path": rel,
        "line": source_line,
    }
    truncated = not _edge_append(
        edges,
        {
            "kind": "sends_notification_method",
            "from": method_symbol,
            "to": notification_method_symbol,
            **payload,
        },
        max_edges=max_edges,
    ) or truncated
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        truncated = not _edge_append(
            edges,
            {
                "kind": "route_sends_notification_method",
                "from": route_ref,
                "to": notification_method_symbol,
                "handler": method_symbol,
                "notification_class": notification_class,
                "notification_method": notification_method,
                "notification_source": notification_source,
                "method": route.get("method"),
                "uri": route.get("uri"),
                "path": route.get("path"),
                "line": route.get("line"),
                "source_path": rel,
                "source_line": source_line,
            },
            max_edges=max_edges,
        ) or truncated
    return truncated


def _append_php_authorization_edges(
    routes_by_handler: dict[str, list[dict[str, Any]]],
    method_symbol: str,
    rel: str,
    source_line: int,
    *,
    ability: str,
    source: str,
    target_param: str,
    target_class: str,
    model_table: str,
    policy_by_model: dict[str, str],
    php_method_symbols: dict[tuple[str, str], str],
    edges: list[dict[str, Any]],
    max_edges: int,
) -> bool:
    ability = ability.strip()
    if not ability:
        return False
    ability_ref = f"ability:{ability}"
    truncated = False
    base_payload = {
        "ability": ability,
        "source": source,
        "target_param": target_param,
        "target_model": target_class,
        "table": model_table,
        "path": rel,
        "line": source_line,
    }
    truncated = not _edge_append(
        edges,
        {
            "kind": "authorization_check",
            "from": method_symbol,
            "to": ability_ref,
            **base_payload,
        },
        max_edges=max_edges,
    ) or truncated
    if target_class:
        truncated = not _edge_append(
            edges,
            {
                "kind": "authorization_model",
                "from": method_symbol,
                "to": target_class,
                **base_payload,
            },
            max_edges=max_edges,
        ) or truncated
    if model_table:
        truncated = not _edge_append(
            edges,
            {
                "kind": "authorization_table",
                "from": method_symbol,
                "to": f"table:{model_table}",
                **base_payload,
            },
            max_edges=max_edges,
        ) or truncated
    policy_class = policy_by_model.get(target_class, "") if target_class else ""
    policy_method_symbol = php_method_symbols.get((policy_class, ability), "") if policy_class else ""
    if policy_method_symbol:
        truncated = not _edge_append(
            edges,
            {
                "kind": "authorization_policy_method",
                "from": method_symbol,
                "to": policy_method_symbol,
                "ability": ability,
                "policy_class": policy_class,
                "target_model": target_class,
                "table": model_table,
                "path": rel,
                "line": source_line,
            },
            max_edges=max_edges,
        ) or truncated
    for route in routes_by_handler.get(method_symbol) or []:
        route_ref = f"route:{_php_route_id(route)}"
        route_payload = {
            "handler": method_symbol,
            "ability": ability,
            "source": source,
            "target_param": target_param,
            "target_model": target_class,
            "table": model_table,
            "method": route.get("method"),
            "uri": route.get("uri"),
            "path": route.get("path"),
            "line": route.get("line"),
            "source_path": rel,
            "source_line": source_line,
        }
        truncated = not _edge_append(
            edges,
            {
                "kind": "route_authorization",
                "from": route_ref,
                "to": ability_ref,
                **route_payload,
            },
            max_edges=max_edges,
        ) or truncated
        if target_class:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_authorization_model",
                    "from": route_ref,
                    "to": target_class,
                    **route_payload,
                },
                max_edges=max_edges,
            ) or truncated
        if model_table:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_authorization_table",
                    "from": route_ref,
                    "to": f"table:{model_table}",
                    **route_payload,
                },
                max_edges=max_edges,
            ) or truncated
        if policy_method_symbol:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_authorization_policy_method",
                    "from": route_ref,
                    "to": policy_method_symbol,
                    "policy_class": policy_class,
                    **route_payload,
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _build_php_graph(
    workspace_root: Path,
    candidates: list[Path],
    omitted: list[dict[str, str]],
    *,
    truncated: bool,
    max_symbols: int,
    max_edges: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    php_files = [path for path in candidates if path.suffix.lower() == ".php"]
    file_refs = [{"path": path.relative_to(workspace_root).as_posix()} for path in php_files]
    model_table_by_class = _php_laravel_model_table_index(
        workspace_root,
        php_files,
        max_file_bytes=max_file_bytes,
    )
    model_scope_by_class = _php_laravel_model_scope_index(
        workspace_root,
        php_files,
        max_file_bytes=max_file_bytes,
    )
    form_request_validation_by_class = _php_form_request_validation_index(
        workspace_root,
        php_files,
        max_file_bytes=max_file_bytes,
    )
    form_request_authorization_by_class = _php_form_request_authorization_index(
        workspace_root,
        php_files,
        max_file_bytes=max_file_bytes,
    )
    form_request_input_mutations_by_class = _php_form_request_input_mutation_index(
        workspace_root,
        php_files,
        max_file_bytes=max_file_bytes,
    )
    php_method_symbols = _php_method_symbol_index(
        workspace_root,
        php_files,
        max_file_bytes=max_file_bytes,
    )
    livewire_component_by_alias = _php_livewire_component_index(
        workspace_root,
        php_files,
        php_method_symbols=php_method_symbols,
        max_file_bytes=max_file_bytes,
    )
    container_bindings_by_abstract = _php_container_binding_index(
        workspace_root,
        php_files,
        max_file_bytes=max_file_bytes,
    )
    command_methods_by_name = _php_command_method_index(
        workspace_root,
        php_files,
        php_method_symbols=php_method_symbols,
        max_file_bytes=max_file_bytes,
    )
    event_listener_methods_by_event = _php_event_listener_method_index(
        workspace_root,
        php_files,
        php_method_symbols=php_method_symbols,
        max_file_bytes=max_file_bytes,
    )
    policy_by_model = _php_policy_index(
        workspace_root,
        php_files,
        max_file_bytes=max_file_bytes,
    )
    routes = _laravel_routes(workspace_root, file_refs)
    routes_by_handler: dict[str, list[dict[str, Any]]] = {}
    route_by_name: dict[str, dict[str, Any]] = {}
    for route in routes:
        handler = str(route.get("handler") or "")
        if handler:
            routes_by_handler.setdefault(handler, []).append(route)
        route_name = str(route.get("name") or "")
        if route_name:
            route_by_name.setdefault(route_name, route)
    middleware_catalog = _laravel_middleware_catalog(workspace_root, file_refs)
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    database = _database_summary(file_refs)
    database["tables"] = []
    log_events: list[dict[str, Any]] = []
    truncated = _append_laravel_middleware_graph(
        middleware_catalog,
        symbols,
        edges,
        max_symbols=max_symbols,
        max_edges=max_edges,
    ) or truncated
    truncated = _php_route_edges(
        routes,
        edges,
        max_edges=max_edges,
        middleware_catalog=middleware_catalog,
        php_method_symbols=php_method_symbols,
    ) or truncated

    for path in php_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue

        truncated = _append_blade_view_graph(
            source,
            rel,
            symbols,
            edges,
            route_by_name=route_by_name,
            livewire_component_by_alias=livewire_component_by_alias,
            max_symbols=max_symbols,
            max_edges=max_edges,
        ) or truncated

        namespace = _php_namespace(source)
        uses = _php_use_map(source)
        classes: list[dict[str, Any]] = []
        symfony_class_routes: dict[str, list[dict[str, Any]]] = {}
        doctrine_tables: dict[str, dict[str, Any]] = {}
        for match in PHP_CLASS_RE.finditer(source):
            class_name = match.group("name")
            extends = match.group("extends") or ""
            fqcn = _php_fqcn(namespace, class_name)
            extends_fqcn = _php_fqcn_resolved(namespace, extends, uses) if extends else None
            role = _php_role(rel, fqcn, extends_fqcn or extends)
            doctrine_meta = _php_doctrine_entity_meta(source, match.start(), class_name)
            if doctrine_meta is not None:
                role = "doctrine_entity"
                doctrine_tables[fqcn] = {
                    "table": doctrine_meta["table"],
                    "model": fqcn,
                    "orm": "doctrine",
                    "path": rel,
                    "line": doctrine_meta["line"],
                    "columns": [],
                    "foreign_keys": [],
                }
            class_symbol = {
                "kind": match.group("kind"),
                "name": fqcn,
                "short_name": class_name,
                "role": role,
                "path": rel,
                "line": _line_number(source, match.start()),
                "extends": extends_fqcn,
                "offset": match.start(),
            }
            classes.append(class_symbol)
            route_metadata = _php_route_metadata_before(source, match.start())
            if route_metadata:
                symfony_class_routes[fqcn] = route_metadata
            if len(symbols) < max_symbols:
                symbols.append({key: value for key, value in class_symbol.items() if key != "offset" and value not in ("", None)})
            else:
                truncated = True
            if extends:
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "extends",
                        "from": fqcn,
                        "to": extends_fqcn,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated
            if role == "api_resource":
                resource_model, resource_table = _php_resource_table_for_class(fqcn, model_table_by_class)
                if resource_model:
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "api_resource_model",
                            "from": fqcn,
                            "to": resource_model,
                            "table": resource_table,
                            "path": rel,
                            "line": _line_number(source, match.start()),
                        },
                        max_edges=max_edges,
                    ) or truncated
                if resource_table:
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "api_resource_table",
                            "from": fqcn,
                            "to": f"table:{resource_table}",
                            "model": resource_model,
                            "path": rel,
                            "line": _line_number(source, match.start()),
                        },
                        max_edges=max_edges,
                    ) or truncated
            if role == "form_request":
                authorization = form_request_authorization_by_class.get(fqcn)
                if authorization:
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "request_authorization",
                            "from": fqcn,
                            "to": "authorization:form_request",
                            "authorization_result": authorization.get("authorization_result"),
                            "authorization_path": authorization.get("path"),
                            "authorization_line": authorization.get("line"),
                            "path": rel,
                            "line": authorization.get("line"),
                        },
                        max_edges=max_edges,
                    ) or truncated
                for mutation in form_request_input_mutations_by_class.get(fqcn) or []:
                    field = str(mutation.get("field") or "")
                    if not field:
                        continue
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "request_input_mutation",
                            "from": fqcn,
                            "to": f"request_field:{field}",
                            "field": field,
                            "operation": mutation.get("operation"),
                            "mutation_stage": mutation.get("mutation_stage"),
                            "mutation_path": mutation.get("path"),
                            "mutation_line": mutation.get("line"),
                            "path": rel,
                            "line": mutation.get("line"),
                        },
                        max_edges=max_edges,
                    ) or truncated
            if role == "livewire_component":
                livewire_component = next(
                    (
                        component
                        for component in livewire_component_by_alias.values()
                        if str(component.get("class") or "") == fqcn
                    ),
                    None,
                )
                if livewire_component:
                    livewire_alias = str(livewire_component.get("alias") or "")
                    for field_info in livewire_component.get("validation_fields") or []:
                        field_name = str(field_info.get("field") or "")
                        if not field_name:
                            continue
                        truncated = not _edge_append(
                            edges,
                            {
                                "kind": "livewire_validation",
                                "from": fqcn,
                                "to": f"validation:{field_name}",
                                "livewire_alias": livewire_alias,
                                "livewire_class": fqcn,
                                "field": field_name,
                                "validation_rules": field_info.get("rules") or [],
                                "validation_path": field_info.get("path"),
                                "validation_line": field_info.get("line"),
                                "path": rel,
                                "line": field_info.get("line"),
                            },
                            max_edges=max_edges,
                        ) or truncated

        classes.sort(key=lambda item: int(item["offset"]))
        truncated = _append_php_log_events(source, rel, classes, edges, log_events, max_edges=max_edges) or truncated

        doctrine_table_by_class = {class_name: info["table"] for class_name, info in doctrine_tables.items()}
        for class_info in classes:
            table_info = doctrine_tables.get(str(class_info["name"]))
            if not table_info:
                continue
            database["tables"].append(table_info)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "model_table",
                    "from": class_info["name"],
                    "to": f"table:{table_info['table']}",
                    "framework": "doctrine",
                    "path": rel,
                    "line": table_info["line"],
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_PROPERTY_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            if class_info is None:
                continue
            table_info = doctrine_tables.get(str(class_info["name"]))
            if not table_info:
                continue
            attrs = _php_attributes_before(source, match.start())
            prop_name = match.group("name")
            line = _line_number(source, match.start())
            column = _php_doctrine_column(attrs, prop_name, rel, line)
            if column is not None:
                table_info["columns"].append(column)
            target_class = _php_doctrine_relation_target(attrs, match.group("type") or "", namespace, uses)
            join_column = _php_doctrine_join_column(attrs, prop_name, rel, line)
            if target_class and join_column is not None:
                table_info["columns"].append(
                    {
                        "name": join_column["column"],
                        "field": prop_name,
                        "type": "relation",
                        "relation_model": target_class,
                        "path": rel,
                        "line": join_column["line"],
                        **({"nullable": join_column["nullable"]} if join_column.get("nullable") is not None else {}),
                    }
                )
                references_table = doctrine_table_by_class.get(target_class) or _snake_name(_php_short_name(target_class)) + "s"
                foreign_key = {
                    "table": table_info["table"],
                    "column": join_column["column"],
                    "references_table": references_table,
                    "references_column": join_column["references_column"],
                    "path": rel,
                    "line": join_column["line"],
                }
                if join_column.get("nullable") is not None:
                    foreign_key["nullable"] = join_column["nullable"]
                table_info["foreign_keys"].append(foreign_key)
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "foreign_key",
                        "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                        "to": f"table:{foreign_key['references_table']}",
                        "framework": "doctrine",
                        "path": rel,
                        "line": foreign_key["line"],
                    },
                    max_edges=max_edges,
                ) or truncated

        model_table = ""
        model_table_match = PHP_MODEL_TABLE_RE.search(source)
        if model_table_match:
            model_table = model_table_match.group("table")
        elif classes and classes[0].get("role") == "model":
            short = _php_short_name(str(classes[0]["name"]))
            model_table = re.sub(r"(?<!^)([A-Z])", r"_\1", short).lower() + "s"
        if model_table and classes:
            for class_info in classes:
                if class_info.get("role") != "model":
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "model_table",
                        "from": class_info["name"],
                        "to": f"table:{model_table}",
                        "path": rel,
                        "line": _line_number(source, model_table_match.start()) if model_table_match else class_info.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated

        truncated = _append_php_model_trait_edges(
            source,
            rel,
            classes,
            namespace,
            uses,
            model_table,
            model_table_by_class,
            edges,
            max_edges=max_edges,
        ) or truncated

        truncated = _append_php_model_metadata_edges(
            source,
            rel,
            classes,
            model_table,
            model_table_by_class,
            edges,
            max_edges=max_edges,
        ) or truncated

        property_types_by_class = _php_class_property_type_map(source, classes, namespace, uses)

        for match in PHP_METHOD_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            if class_info is None:
                continue
            method_name = match.group("name")
            fqcn = str(class_info["name"])
            method_symbol = f"{_php_short_name(fqcn)}@{method_name}"
            if len(symbols) < max_symbols:
                symbols.append(
                    {
                        "kind": "method",
                        "name": method_symbol,
                        "class": fqcn,
                        "method": method_name,
                        "visibility": match.group("visibility"),
                        "role": class_info.get("role"),
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    }
                )
            else:
                truncated = True
            scope_name = _php_scope_name(method_name)
            if class_info.get("role") == "model" and scope_name:
                scope_id = _php_model_scope_id(fqcn, scope_name)
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "model_scope",
                        "from": fqcn,
                        "to": scope_id,
                        "scope": scope_name,
                        "method": method_name,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "scope_method",
                        "from": scope_id,
                        "to": method_symbol,
                        "scope": scope_name,
                        "model": fqcn,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated
            method_body, method_body_offset = _php_method_body_slice(source, match)
            http_aborts = _php_http_aborts_for_method(source, method_body, method_body_offset)
            for http_abort in http_aborts:
                status_code = http_abort.get("status_code")
                if not status_code:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "http_abort",
                        "from": method_symbol,
                        "to": f"http_status:{status_code}",
                        "status_code": status_code,
                        "abort_helper": http_abort.get("abort_helper"),
                        "path": rel,
                        "line": http_abort.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_http_abort_edges(
                routes_by_handler,
                method_symbol,
                rel,
                http_aborts,
                edges,
                max_edges=max_edges,
            ) or truncated
            http_responses = _php_http_response_statuses_for_method(source, method_body, method_body_offset)
            for response_status in http_responses:
                status_code = response_status.get("status_code")
                if not status_code:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "http_response_status",
                        "from": method_symbol,
                        "to": f"http_status:{status_code}",
                        "status_code": status_code,
                        "response_helper": response_status.get("response_helper"),
                        "path": rel,
                        "line": response_status.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_http_response_status_edges(
                routes_by_handler,
                method_symbol,
                rel,
                http_responses,
                edges,
                max_edges=max_edges,
            ) or truncated
            cookie_accesses = _php_cookie_accesses_for_method(source, method_body, method_body_offset)
            for access in cookie_accesses:
                cookie_name = str(access.get("cookie_name") or "")
                if not cookie_name:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "cookie_access",
                        "from": method_symbol,
                        "to": f"cookie:{cookie_name}",
                        "cookie_name": cookie_name,
                        "cookie_operation": access.get("cookie_operation"),
                        "cookie_method": access.get("cookie_method"),
                        "path": rel,
                        "line": access.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_cookie_access_edges(
                routes_by_handler,
                method_symbol,
                rel,
                cookie_accesses,
                edges,
                max_edges=max_edges,
            ) or truncated
            http_redirects = _php_http_redirects_for_method(source, method_body, method_body_offset)
            for redirect in http_redirects:
                redirect_to = str(redirect.get("redirect_to") or "")
                if not redirect_to:
                    continue
                redirect_edge = {
                    "kind": "http_redirect",
                    "from": method_symbol,
                    "to": redirect_to,
                    "redirect_type": redirect.get("redirect_type"),
                    "redirect_helper": redirect.get("redirect_helper"),
                    "path": rel,
                    "line": redirect.get("line"),
                }
                if redirect.get("redirect_target"):
                    redirect_edge["redirect_target"] = redirect.get("redirect_target")
                if redirect.get("redirect_status"):
                    redirect_edge["redirect_status"] = redirect.get("redirect_status")
                truncated = not _edge_append(edges, redirect_edge, max_edges=max_edges) or truncated
            truncated = _append_php_route_http_redirect_edges(
                routes_by_handler,
                method_symbol,
                rel,
                http_redirects,
                edges,
                max_edges=max_edges,
            ) or truncated
            session_accesses = _php_session_accesses_for_method(source, method_body, method_body_offset)
            for access in session_accesses:
                session_key = str(access.get("session_key") or "")
                if not session_key:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "session_access",
                        "from": method_symbol,
                        "to": f"session_key:{session_key}",
                        "session_key": session_key,
                        "session_operation": access.get("session_operation"),
                        "session_method": access.get("session_method"),
                        "path": rel,
                        "line": access.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_session_access_edges(
                routes_by_handler,
                method_symbol,
                rel,
                session_accesses,
                edges,
                max_edges=max_edges,
            ) or truncated
            cache_accesses = _php_cache_accesses_for_method(source, method_body, method_body_offset)
            for access in cache_accesses:
                cache_key = str(access.get("cache_key") or "")
                if not cache_key:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "cache_access",
                        "from": method_symbol,
                        "to": f"cache_key:{cache_key}",
                        "cache_key": cache_key,
                        "cache_operation": access.get("cache_operation"),
                        "cache_method": access.get("cache_method"),
                        "cache_ttl_present": bool(access.get("cache_ttl_present")),
                        "path": rel,
                        "line": access.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_cache_access_edges(
                routes_by_handler,
                method_symbol,
                rel,
                cache_accesses,
                edges,
                max_edges=max_edges,
            ) or truncated
            db_transactions = _php_db_transactions_for_method(source, method_body, method_body_offset)
            for transaction in db_transactions:
                operation = str(transaction.get("transaction_operation") or "")
                if not operation:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "db_transaction",
                        "from": method_symbol,
                        "to": f"db_transaction:{operation}",
                        "transaction_operation": operation,
                        "transaction_method": transaction.get("transaction_method"),
                        "path": rel,
                        "line": transaction.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_db_transaction_edges(
                routes_by_handler,
                method_symbol,
                rel,
                db_transactions,
                edges,
                max_edges=max_edges,
            ) or truncated
            outbound_http_calls = _php_outbound_http_calls_for_method(source, method_body, method_body_offset)
            for call in outbound_http_calls:
                http_target = str(call.get("http_target") or "")
                if not http_target:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "outbound_http_call",
                        "from": method_symbol,
                        "to": http_target,
                        "http_client": call.get("http_client"),
                        "http_method": call.get("http_method"),
                        "http_scheme": call.get("http_scheme"),
                        "http_host": call.get("http_host"),
                        "http_path": call.get("http_path"),
                        "http_call_method": call.get("http_call_method"),
                        "path": rel,
                        "line": call.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_outbound_http_call_edges(
                routes_by_handler,
                method_symbol,
                rel,
                outbound_http_calls,
                edges,
                max_edges=max_edges,
            ) or truncated
            storage_accesses = _php_storage_accesses_for_method(source, method_body, method_body_offset)
            for access in storage_accesses:
                storage_path = str(access.get("storage_path") or "")
                storage_disk = str(access.get("storage_disk") or "default")
                if not storage_path:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "storage_access",
                        "from": method_symbol,
                        "to": f"storage_path:{storage_disk}:{storage_path}",
                        "storage_disk": storage_disk,
                        "storage_path": storage_path,
                        "storage_operation": access.get("storage_operation"),
                        "storage_method": access.get("storage_method"),
                        "path": rel,
                        "line": access.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_storage_access_edges(
                routes_by_handler,
                method_symbol,
                rel,
                storage_accesses,
                edges,
                max_edges=max_edges,
            ) or truncated
            request_input_accesses = _php_request_input_accesses_for_method(source, method_body, method_body_offset)
            for access in request_input_accesses:
                field = str(access.get("field") or "")
                if not field:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "request_input_access",
                        "from": method_symbol,
                        "to": f"request_field:{field}",
                        "field": field,
                        "input_source": access.get("input_source"),
                        "input_method": access.get("input_method"),
                        "path": rel,
                        "line": access.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_request_input_access_edges(
                routes_by_handler,
                method_symbol,
                rel,
                request_input_accesses,
                edges,
                max_edges=max_edges,
            ) or truncated
            request_file_accesses = _php_request_file_accesses_for_method(source, method_body, method_body_offset)
            for access in request_file_accesses:
                file_field = str(access.get("file_field") or "")
                if not file_field:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "request_file_access",
                        "from": method_symbol,
                        "to": f"request_file:{file_field}",
                        "file_field": file_field,
                        "file_operation": access.get("file_operation"),
                        "file_method": access.get("file_method"),
                        "path": rel,
                        "line": access.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_request_file_access_edges(
                routes_by_handler,
                method_symbol,
                rel,
                request_file_accesses,
                edges,
                max_edges=max_edges,
            ) or truncated
            thrown_exceptions = _php_throw_exceptions_for_method(
                source,
                method_body,
                method_body_offset,
                namespace,
                uses,
            )
            for exception in thrown_exceptions:
                exception_class = str(exception.get("exception_class") or "")
                if not exception_class:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "throws_exception",
                        "from": method_symbol,
                        "to": exception_class,
                        "exception_class": exception_class,
                        "exception_short_name": exception.get("exception_short_name"),
                        "path": rel,
                        "line": exception.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
            truncated = _append_php_route_throw_exception_edges(
                routes_by_handler,
                method_symbol,
                rel,
                thrown_exceptions,
                edges,
                max_edges=max_edges,
            ) or truncated
            if class_info.get("role") == "api_resource" and method_name == "toArray":
                resource_model, resource_table = _php_resource_table_for_class(fqcn, model_table_by_class)
                for field_info in _php_array_field_keys(source, method_body, method_body_offset):
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "api_resource_field",
                            "from": fqcn,
                            "to": f"response_field:{field_info['field']}",
                            "field": field_info["field"],
                            "model": resource_model,
                            "table": resource_table,
                            "path": rel,
                            "line": field_info["line"],
                        },
                        max_edges=max_edges,
                    ) or truncated
            truncated = _php_model_attribute_edges_for_method(
                source,
                rel,
                class_info,
                method_name,
                match,
                method_body,
                model_table_by_class,
                model_table,
                edges,
                max_edges=max_edges,
            ) or truncated
            typed_params: dict[str, str] = {}
            for param_match in PHP_TYPED_PARAM_RE.finditer(match.group("params") or ""):
                param_name = str(param_match.group("name") or "")
                param_class = _php_fqcn_resolved(namespace, param_match.group("class"), uses)
                if param_name:
                    typed_params[param_name] = param_class
                param_short = _php_short_name(param_class)
                if param_short.endswith("Request"):
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "uses_form_request",
                            "from": method_symbol,
                            "to": param_class,
                            "path": rel,
                            "line": _line_number(source, match.start()),
                        },
                        max_edges=max_edges,
                    ) or truncated
                    truncated = _append_php_route_form_request_edges(
                        routes_by_handler,
                        method_symbol,
                        str(param_match.group("name") or ""),
                        param_class,
                        form_request_validation_by_class.get(param_class) or [],
                        edges,
                        max_edges=max_edges,
                    ) or truncated
                    truncated = _append_php_route_form_request_authorization_edges(
                        routes_by_handler,
                        method_symbol,
                        str(param_match.group("name") or ""),
                        param_class,
                        form_request_authorization_by_class.get(param_class),
                        edges,
                        max_edges=max_edges,
                    ) or truncated
                    truncated = _append_php_route_form_request_input_mutation_edges(
                        routes_by_handler,
                        method_symbol,
                        str(param_match.group("name") or ""),
                        param_class,
                        form_request_input_mutations_by_class.get(param_class) or [],
                        edges,
                        max_edges=max_edges,
                    ) or truncated
                else:
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "uses_dependency",
                            "from": method_symbol,
                            "to": param_class,
                            "path": rel,
                            "line": _line_number(source, match.start()),
                        },
                        max_edges=max_edges,
                    ) or truncated
                    truncated = _append_php_route_model_binding_edges(
                        routes_by_handler,
                        method_symbol,
                        str(param_match.group("name") or ""),
                        param_class,
                        model_table_by_class,
                        edges,
                        max_edges=max_edges,
                    ) or truncated

            truncated = _append_php_instance_method_call_edges(
                source,
                rel,
                classes,
                method_body,
                method_body_offset,
                method_symbol,
                property_types_by_class.get(fqcn, {}),
                typed_params,
                php_method_symbols,
                container_bindings_by_abstract,
                edges,
                max_edges=max_edges,
            ) or truncated

            truncated = _append_php_model_instance_operation_edges(
                source,
                rel,
                method_body,
                method_body_offset,
                method_symbol,
                typed_params,
                routes_by_handler,
                model_table_by_class,
                edges,
                max_edges=max_edges,
            ) or truncated

            for auth_pattern, auth_source in (
                (PHP_THIS_AUTHORIZE_RE, "this_authorize"),
                (PHP_GATE_AUTHORIZATION_RE, "gate_authorize"),
            ):
                for auth_match in auth_pattern.finditer(method_body):
                    target_param = str(auth_match.group("var") or "")
                    target_class = typed_params.get(target_param, "")
                    model_table = _php_model_table_for_class(target_class, model_table_by_class) if target_class else ""
                    truncated = _append_php_authorization_edges(
                        routes_by_handler,
                        method_symbol,
                        rel,
                        _line_number(source, method_body_offset + auth_match.start()),
                        ability=str(auth_match.group("ability") or ""),
                        source=auth_source,
                        target_param=target_param,
                        target_class=target_class,
                        model_table=model_table,
                        policy_by_model=policy_by_model,
                        php_method_symbols=php_method_symbols,
                        edges=edges,
                        max_edges=max_edges,
                    ) or truncated

            method_routes = _php_route_metadata_before(source, match.start())
            if not method_routes and method_name == "__invoke":
                method_routes = [{}] if symfony_class_routes.get(fqcn) else []
            for class_route in symfony_class_routes.get(fqcn) or [{}]:
                for method_route in method_routes:
                    route = _php_symfony_route(
                        class_route,
                        method_route,
                        handler=method_symbol,
                        controller=fqcn,
                        rel=rel,
                        fallback_line=_line_number(source, match.start()),
                    )
                    routes.append(route)
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "route_handler",
                            "from": f"route:{_php_route_id(route)}",
                            "to": method_symbol,
                            "framework": "symfony",
                            "method": route.get("method"),
                            "uri": route.get("uri"),
                            "path": rel,
                            "line": route.get("line"),
                        },
                        max_edges=max_edges,
                    ) or truncated

        rules_body = _php_rules_method_body(source)
        if rules_body is not None:
            body, base_offset = rules_body
            for class_info in classes:
                if class_info.get("role") != "form_request":
                    continue
                for field_info in _php_array_validation_fields(source, body, base_offset):
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "request_validation",
                            "from": class_info["name"],
                            "to": f"validation:{field_info['field']}",
                            "validation_rules": field_info.get("rules") or [],
                            "path": rel,
                            "line": field_info["line"],
                        },
                        max_edges=max_edges,
                    ) or truncated
                    for database_rule in field_info.get("database_rules") or []:
                        database_rule_edge = {
                            "kind": "validation_database_rule",
                            "from": class_info["name"],
                            "to": _php_validation_database_rule_target(database_rule),
                            "field": field_info["field"],
                            "rule": database_rule.get("rule"),
                            "table": database_rule.get("table"),
                            "path": rel,
                            "line": field_info["line"],
                        }
                        if database_rule.get("column"):
                            database_rule_edge["column"] = database_rule.get("column")
                        truncated = not _edge_append(edges, database_rule_edge, max_edges=max_edges) or truncated

        for match in PHP_VALIDATE_ARRAY_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            method_context = _php_method_context_id(source, classes, match.start(), rel)
            for field_info in _php_array_validation_fields(source, match.group("body"), match.start()):
                edge = {
                    "kind": "request_validation",
                    "from": _php_context_id(class_info, rel),
                    "to": f"validation:{field_info['field']}",
                    "validation_rules": field_info.get("rules") or [],
                    "path": rel,
                    "line": field_info["line"],
                }
                truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    edge,
                    max_edges=max_edges,
                ) or truncated
                for database_rule in field_info.get("database_rules") or []:
                    database_rule_edge = {
                        "kind": "validation_database_rule",
                        "from": _php_context_id(class_info, rel),
                        "to": _php_validation_database_rule_target(database_rule),
                        "field": field_info["field"],
                        "rule": database_rule.get("rule"),
                        "table": database_rule.get("table"),
                        "path": rel,
                        "line": field_info["line"],
                    }
                    if database_rule.get("column"):
                        database_rule_edge["column"] = database_rule.get("column")
                    truncated = not _edge_append(edges, database_rule_edge, max_edges=max_edges) or truncated
                    truncated = not _append_php_method_context_edge(
                        source,
                        rel,
                        classes,
                        match.start(),
                        edges,
                        database_rule_edge,
                        max_edges=max_edges,
                    ) or truncated
                truncated = _append_php_route_inline_validation_edges(
                    routes_by_handler,
                    method_context,
                    field_info,
                    rel,
                    edges,
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_ELOQUENT_RELATION_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            if class_info is None:
                continue
            truncated = not _edge_append(
                edges,
                {
                    "kind": "eloquent_relation",
                    "from": class_info["name"],
                    "to": _php_fqcn_resolved(namespace, match.group("target"), uses),
                    "relation": match.group("relation"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_STATIC_CALL_RE.finditer(source):
            class_name = match.group("class")
            method_name = match.group("method")
            if _php_short_name(class_name) in {
                "self",
                "static",
                "parent",
                "Route",
                "Schema",
                "Gate",
                "DB",
                "Attribute",
                "Broadcast",
                "View",
                "Inertia",
            }:
                continue
            if method_name == "observe":
                continue
            class_info = _class_context(classes, match.start())
            resolved_class = _php_fqcn_resolved(namespace, class_name, uses)
            edge = {
                "kind": "static_call",
                "from": class_info["name"] if class_info else rel,
                "to": f"{resolved_class}::{method_name}",
                "path": rel,
                "line": _line_number(source, match.start()),
            }
            truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
            truncated = not _append_php_method_context_edge(
                source,
                rel,
                classes,
                match.start(),
                edges,
                edge,
                max_edges=max_edges,
            ) or truncated
            target_method_symbol = php_method_symbols.get((resolved_class, method_name))
            if target_method_symbol:
                call_edge = {
                    "kind": "calls_method",
                    "from": class_info["name"] if class_info else rel,
                    "to": target_method_symbol,
                    "target_class": resolved_class,
                    "call_type": "static",
                    "target_method": method_name,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                truncated = not _edge_append(edges, call_edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    call_edge,
                    max_edges=max_edges,
                ) or truncated
            if _php_is_laravel_api_resource_class(resolved_class) and method_name in {"make", "collection"}:
                resource_model, resource_table = _php_resource_table_for_class(resolved_class, model_table_by_class)
                resource_edge = {
                    "kind": "api_resource_ref",
                    "from": class_info["name"] if class_info else rel,
                    "to": resolved_class,
                    "resource_method": method_name,
                    "model": resource_model,
                    "table": resource_table,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                truncated = not _edge_append(edges, resource_edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    resource_edge,
                    max_edges=max_edges,
                ) or truncated
            model_scopes = model_scope_by_class.get(resolved_class) or set()
            if method_name in model_scopes:
                model_table = _php_model_table_for_class(resolved_class, model_table_by_class)
                scope_edge = {
                    "kind": "eloquent_scope_call",
                    "from": class_info["name"] if class_info else rel,
                    "to": _php_model_scope_id(resolved_class, method_name),
                    "scope": method_name,
                    "model": resolved_class,
                    "table": model_table,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                truncated = not _edge_append(edges, scope_edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    scope_edge,
                    max_edges=max_edges,
                ) or truncated
                if model_table:
                    truncated = _append_php_eloquent_query_builder_edges(
                        source,
                        rel,
                        classes,
                        match,
                        resolved_class=resolved_class,
                        method_name=method_name,
                        table=model_table,
                        edges=edges,
                        max_edges=max_edges,
                    ) or truncated
            if method_name in PHP_ELOQUENT_QUERY_METHODS and _php_short_name(resolved_class) != "DB":
                query_edge = {
                    "kind": "eloquent_query",
                    "from": class_info["name"] if class_info else rel,
                    "to": f"{resolved_class}::{method_name}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                truncated = not _edge_append(edges, query_edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    query_edge,
                    max_edges=max_edges,
                ) or truncated
                model_table = _php_model_table_for_class(resolved_class, model_table_by_class)
                if model_table:
                    truncated = _append_php_eloquent_query_builder_edges(
                        source,
                        rel,
                        classes,
                        match,
                        resolved_class=resolved_class,
                        method_name=method_name,
                        table=model_table,
                        edges=edges,
                        max_edges=max_edges,
                    ) or truncated

        for mapping in _php_policy_mappings(source, namespace, uses):
            truncated = not _edge_append(
                edges,
                {
                    "kind": "policy_for",
                    "from": mapping.get("model_class"),
                    "to": mapping.get("policy_class"),
                    "source": mapping.get("source"),
                    "path": rel,
                    "line": _line_number(source, int(mapping.get("offset") or 0)),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_CONTAINER_BIND_RE.finditer(source):
            truncated = not _edge_append(
                edges,
                {
                    "kind": "container_binding",
                    "from": _php_fqcn_resolved(namespace, match.group("abstract"), uses),
                    "to": _php_fqcn_resolved(namespace, match.group("concrete"), uses),
                    "binding": match.group("method"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for match in PHP_OBSERVER_RE.finditer(source):
            model_class = _php_fqcn_resolved(namespace, match.group("model"), uses)
            observer_class = _php_fqcn_resolved(namespace, match.group("observer"), uses)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "observed_by",
                    "from": model_class,
                    "to": observer_class,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated
            model_table = _php_model_table_for_class(model_class, model_table_by_class)
            for observer_method in sorted(PHP_OBSERVER_LIFECYCLE_METHODS):
                observer_method_symbol = php_method_symbols.get((observer_class, observer_method), "")
                if not observer_method_symbol:
                    continue
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "observed_by_method",
                        "from": model_class,
                        "to": observer_method_symbol,
                        "observer_class": observer_class,
                        "observer_method": observer_method,
                        "lifecycle_event": observer_method,
                        "table": model_table,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_LISTEN_ARRAY_RE.finditer(source):
            event_class = _php_fqcn_resolved(namespace, match.group("event"), uses)
            for listener_match in PHP_CLASS_CONST_RE.finditer(match.group("listeners")):
                listener_class = _php_fqcn_resolved(namespace, listener_match.group("class"), uses)
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "event_listener",
                        "from": event_class,
                        "to": listener_class,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated
                listener_method_symbol = php_method_symbols.get((listener_class, "handle"), "")
                if listener_method_symbol:
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "event_listener_method",
                            "from": event_class,
                            "to": listener_method_symbol,
                            "listener_class": listener_class,
                            "listener_method": "handle",
                            "path": rel,
                            "line": _line_number(source, match.start()),
                        },
                        max_edges=max_edges,
                    ) or truncated

        for match in PHP_DISPATCH_JOB_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            job_class = _php_fqcn_resolved(namespace, match.group("class"), uses)
            source_line = _line_number(source, match.start())
            edge = {
                "kind": "dispatches_job",
                "from": _php_context_id(class_info, rel),
                "to": job_class,
                "path": rel,
                "line": source_line,
            }
            truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
            truncated = not _append_php_method_context_edge(
                source,
                rel,
                classes,
                match.start(),
                edges,
                edge,
                max_edges=max_edges,
            ) or truncated
            method_context = _php_method_context_id(source, classes, match.start(), rel)
            job_method_symbol = php_method_symbols.get((job_class, "handle"), "")
            truncated = _append_php_dispatched_job_method_edges(
                routes_by_handler,
                method_context,
                rel,
                source_line,
                job_class=job_class,
                job_method_symbol=job_method_symbol,
                dispatch_method=match.group("method"),
                edges=edges,
                max_edges=max_edges,
            ) or truncated

        for event_pattern in (PHP_EVENT_FUNCTION_RE, PHP_EVENT_DISPATCH_RE):
            for match in event_pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                event_class = _php_fqcn_resolved(namespace, match.group("class"), uses)
                edge = {
                    "kind": "emits_event",
                    "from": _php_context_id(class_info, rel),
                    "to": event_class,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    edge,
                    max_edges=max_edges,
                ) or truncated
                method_context = _php_method_context_id(source, classes, match.start(), rel)
                truncated = _append_php_emitted_event_listener_edges(
                    routes_by_handler,
                    method_context,
                    rel,
                    _line_number(source, match.start()),
                    event_class=event_class,
                    listener_methods=event_listener_methods_by_event.get(event_class, []),
                    edges=edges,
                    max_edges=max_edges,
                ) or truncated

        for mail_pattern in (PHP_MAIL_CHAIN_RE, PHP_MAIL_DIRECT_RE):
            for match in mail_pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                mailable_class = _php_fqcn_resolved(namespace, match.group("class"), uses)
                source_line = _line_number(source, match.start())
                mail_method = str(match.group("method") or "")
                edge = {
                    "kind": "sends_mail",
                    "from": _php_context_id(class_info, rel),
                    "to": mailable_class,
                    "mail_method": mail_method,
                    "path": rel,
                    "line": source_line,
                }
                truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    edge,
                    max_edges=max_edges,
                ) or truncated
                method_context = _php_method_context_id(source, classes, match.start(), rel)
                mailable_method_symbol, mailable_method = _php_first_method_symbol(
                    php_method_symbols,
                    mailable_class,
                    ("build", "content", "envelope"),
                )
                truncated = _append_php_mail_method_edges(
                    routes_by_handler,
                    method_context,
                    rel,
                    source_line,
                    mailable_class=mailable_class,
                    mailable_method_symbol=mailable_method_symbol,
                    mailable_method=mailable_method,
                    mail_method=mail_method,
                    edges=edges,
                    max_edges=max_edges,
                ) or truncated

        for notification_pattern, notification_source in (
            (PHP_NOTIFY_NEW_RE, "notifiable_notify"),
            (PHP_NOTIFICATION_SEND_RE, "notification_facade"),
        ):
            for match in notification_pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                notification_class = _php_fqcn_resolved(namespace, match.group("class"), uses)
                source_line = _line_number(source, match.start())
                edge = {
                    "kind": "sends_notification",
                    "from": _php_context_id(class_info, rel),
                    "to": notification_class,
                    "notification_source": notification_source,
                    "path": rel,
                    "line": source_line,
                }
                truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    edge,
                    max_edges=max_edges,
                ) or truncated
                method_context = _php_method_context_id(source, classes, match.start(), rel)
                notification_method_symbol, notification_method = _php_first_method_symbol(
                    php_method_symbols,
                    notification_class,
                    ("toMail", "toArray", "toDatabase", "toBroadcast", "via"),
                )
                truncated = _append_php_notification_method_edges(
                    routes_by_handler,
                    method_context,
                    rel,
                    source_line,
                    notification_class=notification_class,
                    notification_method_symbol=notification_method_symbol,
                    notification_method=notification_method,
                    notification_source=notification_source,
                    edges=edges,
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_COMMAND_SIGNATURE_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            command_name = _php_command_name(match.group("signature"))
            if class_info is None or not command_name:
                continue
            command_class = str(class_info["name"])
            command_method_symbol = php_method_symbols.get((command_class, "handle"), "")
            truncated = not _edge_append(
                edges,
                {
                    "kind": "artisan_command",
                    "from": command_class,
                    "to": f"command:{command_name}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated
            if command_method_symbol:
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "artisan_command_method",
                        "from": f"command:{command_name}",
                        "to": command_method_symbol,
                        "command_class": command_class,
                        "command_method": "handle",
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_SCHEDULE_COMMAND_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            command_name = _php_command_name(match.group("command"))
            cadence = _php_schedule_cadence(match.group("chain"))
            truncated = not _edge_append(
                edges,
                {
                    "kind": "scheduled_command",
                    "from": _php_context_id(class_info, rel),
                    "to": f"command:{command_name}",
                    "cadence": cadence,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated
            command_info = command_methods_by_name.get(command_name, {})
            command_method_symbol = str(command_info.get("command_method") or "")
            if command_method_symbol:
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "scheduled_command_method",
                        "from": _php_context_id(class_info, rel),
                        "to": command_method_symbol,
                        "command": f"command:{command_name}",
                        "command_class": command_info.get("command_class"),
                        "command_method": "handle",
                        "cadence": cadence,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_SCHEDULE_JOB_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            job_class = _php_fqcn_resolved(namespace, match.group("class"), uses)
            cadence = _php_schedule_cadence(match.group("chain"))
            truncated = not _edge_append(
                edges,
                {
                    "kind": "scheduled_job",
                    "from": _php_context_id(class_info, rel),
                    "to": job_class,
                    "cadence": cadence,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated
            job_method_symbol = php_method_symbols.get((job_class, "handle"), "")
            if job_method_symbol:
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "scheduled_job_method",
                        "from": _php_context_id(class_info, rel),
                        "to": job_method_symbol,
                        "job_class": job_class,
                        "job_method": "handle",
                        "cadence": cadence,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                    },
                    max_edges=max_edges,
                ) or truncated

        for table_pattern in (PHP_DB_TABLE_RE, PHP_QUERY_FROM_RE, PHP_QUERY_JOIN_RE):
            for match in table_pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                edge = {
                    "kind": "query_table",
                    "from": _php_context_id(class_info, rel),
                    "to": f"table:{match.group('table')}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                if "method" in match.groupdict():
                    edge["query_method"] = match.group("method")
                truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    edge,
                    max_edges=max_edges,
                ) or truncated
                if table_pattern is PHP_DB_TABLE_RE:
                    truncated = _append_php_query_builder_edges(
                        source,
                        rel,
                        classes,
                        match,
                        edges,
                        max_edges=max_edges,
                    ) or truncated

        for kind, pattern, prefix in (
            ("view_ref", PHP_VIEW_FUNCTION_RE, "view"),
            ("view_ref", PHP_VIEW_MAKE_RE, "view"),
            ("inertia_view", PHP_INERTIA_RENDER_RE, "inertia"),
        ):
            for match in pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                edge = {
                    "kind": kind,
                    "from": _php_context_id(class_info, rel),
                    "to": f"{prefix}:{match.group('view')}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    edge,
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_BROADCAST_CHANNEL_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            handler_match = PHP_CLASS_CONST_RE.search(match.group("handler") or "")
            edge = {
                "kind": "broadcast_channel",
                "from": _php_context_id(class_info, rel),
                "to": f"broadcast:{match.group('channel')}",
                "path": rel,
                "line": _line_number(source, match.start()),
            }
            if handler_match:
                edge["handler"] = _php_fqcn_resolved(namespace, handler_match.group("class"), uses)
            truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated

        for kind, pattern, prefix in (
            ("config_ref", PHP_CONFIG_RE, "config"),
            ("env_ref", PHP_ENV_RE, "env"),
        ):
            for match in pattern.finditer(source):
                class_info = _class_context(classes, match.start())
                edge = {
                    "kind": kind,
                    "from": _php_context_id(class_info, rel),
                    "to": f"{prefix}:{match.group('key')}",
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    edge,
                    max_edges=max_edges,
                ) or truncated

        for match in PHP_NEW_RE.finditer(source):
            class_info = _class_context(classes, match.start())
            resolved_class = _php_fqcn_resolved(namespace, match.group("class"), uses)
            edge = {
                "kind": "instantiates",
                "from": class_info["name"] if class_info else rel,
                "to": resolved_class,
                "path": rel,
                "line": _line_number(source, match.start()),
            }
            truncated = not _edge_append(edges, edge, max_edges=max_edges) or truncated
            truncated = not _append_php_method_context_edge(
                source,
                rel,
                classes,
                match.start(),
                edges,
                edge,
                max_edges=max_edges,
            ) or truncated
            if _php_is_laravel_api_resource_class(resolved_class):
                resource_model, resource_table = _php_resource_table_for_class(resolved_class, model_table_by_class)
                resource_edge = {
                    "kind": "api_resource_ref",
                    "from": class_info["name"] if class_info else rel,
                    "to": resolved_class,
                    "resource_method": "new",
                    "model": resource_model,
                    "table": resource_table,
                    "path": rel,
                    "line": _line_number(source, match.start()),
                }
                truncated = not _edge_append(edges, resource_edge, max_edges=max_edges) or truncated
                truncated = not _append_php_method_context_edge(
                    source,
                    rel,
                    classes,
                    match.start(),
                    edges,
                    resource_edge,
                    max_edges=max_edges,
                ) or truncated

        if rel.startswith("database/migrations/"):
            for table_info in _laravel_migration_tables(source, rel):
                database["tables"].append(table_info)
                table_name = str(table_info["table"])
                if len(symbols) < max_symbols:
                    symbols.append(
                        {
                            "kind": "table",
                            "name": f"table:{table_name}",
                            "table": table_name,
                            "role": "database_table",
                            "path": rel,
                            "line": table_info.get("line"),
                        }
                    )
                else:
                    truncated = True
                truncated = not _edge_append(
                    edges,
                    {
                        "kind": "migration_table",
                        "from": rel,
                        "to": f"table:{table_name}",
                        "action": table_info.get("action"),
                        "path": rel,
                        "line": table_info.get("line"),
                    },
                    max_edges=max_edges,
                ) or truncated
                for foreign_key in table_info.get("foreign_keys") or []:
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "foreign_key",
                            "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                            "to": f"table:{foreign_key['references_table']}",
                            "path": rel,
                            "line": foreign_key.get("line"),
                        },
                        max_edges=max_edges,
                    ) or truncated

    route_frameworks = {str(route.get("framework")) for route in routes if route.get("framework")}
    has_laravel = "laravel" in route_frameworks or (workspace_root / "artisan").exists()
    has_symfony = "symfony" in route_frameworks or (workspace_root / "bin" / "console").exists()
    has_doctrine = any(str(table.get("orm") or "") == "doctrine" for table in database.get("tables", []))
    if sum(1 for item in (has_laravel, has_symfony, has_doctrine) if item) > 1:
        framework = "php_web"
    elif has_laravel:
        framework = "laravel"
    elif has_symfony:
        framework = "symfony"
    elif has_doctrine:
        framework = "doctrine"
    else:
        framework = "php"

    tests, tests_truncated = _build_test_map(
        workspace_root,
        candidates,
        routes,
        symbols,
        edges,
        max_edges=max_edges,
        max_file_bytes=max_file_bytes,
    )
    truncated = truncated or tests_truncated
    logs = {
        "schema": "hades.log_map.v1",
        "event_count": len(log_events),
        "events": log_events[:MAX_LOG_EVENTS],
        "truncated": len(log_events) > MAX_LOG_EVENTS,
        "raw_source_included": False,
    }
    graph = {
        "schema": "hades.php_graph.v1",
        "language": "php",
        "framework": framework,
        "root": workspace_root.name,
        "routes": routes,
        "symbols": symbols,
        "edges": edges,
        "database": database,
        "middleware": {key: value for key, value in middleware_catalog.items() if not key.startswith("_")},
        "tests": tests,
        "logs": logs,
        "summary": "",
        "omitted": omitted,
        "truncated": truncated or len(symbols) >= max_symbols or len(edges) >= max_edges,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }
    graph["summary"] = _php_graph_summary(routes, symbols, edges, database, tests, logs)
    return graph


def _ts_graph_summary(
    routes: list[dict[str, str]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    framework: str,
    database: dict[str, Any] | None = None,
    tests: dict[str, Any] | None = None,
    logs: dict[str, Any] | None = None,
) -> str:
    kind_counts: dict[str, int] = {}
    for symbol in symbols:
        kind = str(symbol.get("kind") or "symbol")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    kinds = ", ".join(f"{kind}:{count}" for kind, count in sorted(kind_counts.items())[:8])
    table_count = len((database or {}).get("tables") or [])
    test_count = int((tests or {}).get("file_count") or 0)
    log_count = int((logs or {}).get("event_count") or 0)
    return f"Code graph; framework:{framework}; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; tables:{table_count}; tests:{test_count}; logs:{log_count}; {kinds or 'symbols:none'}"


def _py_graph_summary(
    routes: list[dict[str, str]],
    symbols: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    framework: str,
    database: dict[str, Any] | None = None,
    tests: dict[str, Any] | None = None,
    logs: dict[str, Any] | None = None,
) -> str:
    kind_counts: dict[str, int] = {}
    for symbol in symbols:
        kind = str(symbol.get("kind") or "symbol")
        kind_counts[kind] = kind_counts.get(kind, 0) + 1
    kinds = ", ".join(f"{kind}:{count}" for kind, count in sorted(kind_counts.items())[:8])
    table_count = len((database or {}).get("tables") or [])
    test_count = int((tests or {}).get("file_count") or 0)
    log_count = int((logs or {}).get("event_count") or 0)
    return f"Code graph; framework:{framework}; routes:{len(routes)}; symbols:{len(symbols)}; edges:{len(edges)}; tables:{table_count}; tests:{test_count}; logs:{log_count}; {kinds or 'symbols:none'}"


def _py_dotted_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _py_dotted_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    if isinstance(node, ast.Call):
        return _py_dotted_name(node.func)
    return ""


def _py_string(node: ast.AST | None) -> str:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""


def _py_keyword_string(node: ast.Call, name: str) -> str:
    for keyword in node.keywords:
        if keyword.arg == name:
            return _py_string(keyword.value)
    return ""


def _py_keyword_bool(node: ast.Call, name: str) -> bool | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, bool):
            return keyword.value.value
    return None


def _py_keyword_int(node: ast.Call, name: str) -> int | None:
    for keyword in node.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, int):
            return keyword.value.value
    return None


def _join_url_path(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    if not path:
        return prefix
    return f"/{prefix.strip('/')}/{path.strip('/')}".replace("//", "/")


def _py_route_id(route: dict[str, Any]) -> str:
    return str(route.get("name") or f"{route.get('method', '')} {route.get('path', '')}".strip())


def _py_import_target(module: str, name: str, level: int = 0) -> str:
    prefix = "." * max(0, level)
    if module and name:
        return f"{prefix}{module}.{name}"
    return f"{prefix}{module or name}".strip(".") or prefix


def _py_import_aliases_and_edges(
    tree: ast.AST,
    rel: str,
    edges: list[dict[str, Any]],
    *,
    max_edges: int,
) -> tuple[dict[str, str], bool]:
    aliases: dict[str, str] = {}
    truncated = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                local = alias.asname or alias.name.split(".", 1)[0]
                aliases[local] = target
                truncated = not _edge_append(
                    edges,
                    {"kind": "imports", "from": rel, "to": target, "path": rel, "line": node.lineno},
                    max_edges=max_edges,
                ) or truncated
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                target = _py_import_target(module, alias.name, node.level)
                local = alias.asname or alias.name
                aliases[local] = target
                truncated = not _edge_append(
                    edges,
                    {"kind": "imports", "from": rel, "to": target, "path": rel, "line": node.lineno},
                    max_edges=max_edges,
                ) or truncated
    return aliases, truncated


def _py_resolve_call_name(call_name: str, imports: dict[str, str]) -> str:
    if not call_name:
        return ""
    head, separator, tail = call_name.partition(".")
    target = imports.get(head)
    if not target:
        return call_name
    return f"{target}{separator}{tail}" if separator else target


def _py_log_event_from_call(
    call: ast.Call,
    *,
    call_name: str,
    context: str,
    rel: str,
) -> dict[str, Any] | None:
    parts = call_name.split(".")
    level = parts[-1].lower() if parts else ""
    if level not in PY_LOG_LEVELS:
        return None
    logger = ".".join(parts[:-1]) or "logger"
    if not (
        logger == "logging"
        or logger.endswith(".logging")
        or logger.endswith(".logger")
        or logger in {"logger", "log", "self.logger"}
    ):
        return None
    message = ""
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        message = redact_secret(call.args[0].value)
    payload = {
        "context": context,
        "logger": logger,
        "level": "warning" if level == "warn" else level,
        "path": rel,
        "line": getattr(call, "lineno", 0),
        "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest() if message else "",
        "message_length": len(message) if message else 0,
    }
    return {key: value for key, value in payload.items() if value not in ("", None, 0)}


def _py_callable_contexts(tree: ast.AST) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    contexts: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    body = getattr(tree, "body", [])
    for item in body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            contexts.append((item.name, item))
        elif isinstance(item, ast.ClassDef):
            for child in item.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    contexts.append((f"{item.name}.{child.name}", child))
    return contexts


def _append_python_call_edges(
    tree: ast.AST,
    rel: str,
    imports: dict[str, str],
    edges: list[dict[str, Any]],
    log_events: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    for context, node in _py_callable_contexts(tree):
        seen_calls: set[tuple[str, int]] = set()
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            call_name = _py_resolve_call_name(_py_dotted_name(child.func), imports)
            if not call_name or call_name in {context, "super"}:
                continue
            line = getattr(child, "lineno", getattr(node, "lineno", 0))
            key = (call_name, line)
            if key in seen_calls:
                continue
            seen_calls.add(key)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "calls",
                    "from": context,
                    "to": call_name,
                    "path": rel,
                    "line": line,
                },
                max_edges=max_edges,
            ) or truncated
            log_event = _py_log_event_from_call(child, call_name=call_name, context=context, rel=rel)
            if log_event is None:
                continue
            if len(log_events) >= MAX_LOG_EVENTS:
                truncated = True
                continue
            log_id_payload = json.dumps(log_event, sort_keys=True, separators=(",", ":")).encode("utf-8")
            log_id = hashlib.sha256(log_id_payload).hexdigest()[:16]
            log_event = {"id": f"log:{log_id}", **log_event}
            log_events.append(log_event)
            truncated = not _edge_append(
                edges,
                {
                    "kind": "emits_log",
                    "from": context,
                    "to": log_event["id"],
                    "level": log_event.get("level"),
                    "logger": log_event.get("logger"),
                    "path": rel,
                    "line": log_event.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return truncated


def _snake_name(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def _py_app_label(rel: str) -> str:
    parts = rel.split("/")
    if "models" in parts:
        index = parts.index("models")
        if index > 0:
            return parts[index - 1]
    if rel.endswith("/models.py") and len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "app"


def _py_django_model_base(node: ast.ClassDef) -> bool:
    for base in node.bases:
        base_name = _py_dotted_name(base)
        if base_name == "Model" or base_name.endswith(".Model"):
            return True
    return False


def _py_django_meta_table(node: ast.ClassDef) -> str:
    for item in node.body:
        if not isinstance(item, ast.ClassDef) or item.name != "Meta":
            continue
        for meta_item in item.body:
            if not isinstance(meta_item, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) and target.id == "db_table" for target in meta_item.targets):
                continue
            table = _py_string(meta_item.value)
            if table:
                return table
    return ""


def _py_django_relation_target(call: ast.Call) -> str:
    if not call.args:
        return ""
    target = call.args[0]
    if isinstance(target, ast.Constant) and isinstance(target.value, str):
        return target.value
    return _py_dotted_name(target)


def _py_django_target_table(target: str, app_label: str, current_table: str, model_tables: dict[str, str]) -> str:
    if not target:
        return ""
    if target == "self":
        return current_table
    clean = target.strip("'\"")
    model_name = clean.rsplit(".", 1)[-1]
    if model_name in model_tables:
        return model_tables[model_name]
    if "." in clean and not clean.startswith("settings."):
        app, model = clean.rsplit(".", 1)
        return f"{app}_{_snake_name(model)}"
    if clean.startswith("settings."):
        return f"setting:{clean}"
    return f"{app_label}_{_snake_name(clean.split('.')[-1])}"


def _py_django_model_table(node: ast.ClassDef, rel: str) -> tuple[str, str]:
    app_label = _py_app_label(rel)
    return _py_django_meta_table(node) or f"{app_label}_{_snake_name(node.name)}", app_label


def _py_django_model_fields(
    node: ast.ClassDef,
    table: str,
    app_label: str,
    rel: str,
    model_tables: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    for item in node.body:
        value: ast.AST | None = None
        field_name = ""
        if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
            field_name = item.targets[0].id
            value = item.value
        elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            field_name = item.target.id
            value = item.value
        if not field_name or not isinstance(value, ast.Call):
            continue
        field_type = _py_dotted_name(value.func).split(".")[-1]
        if not (field_type.endswith("Field") or field_type in PY_DJANGO_RELATION_FIELDS):
            continue
        relation = field_type in PY_DJANGO_RELATION_FIELDS
        column_name = f"{field_name}_id" if relation and field_type != "ManyToManyField" else field_name
        column = {
            "name": column_name,
            "field": field_name,
            "type": field_type,
            "path": rel,
            "line": getattr(item, "lineno", getattr(value, "lineno", 0)),
        }
        for keyword in ("null", "blank", "unique", "db_index", "primary_key"):
            keyword_value = _py_keyword_bool(value, keyword)
            if keyword_value is not None:
                column[keyword] = keyword_value
        max_length = _py_keyword_int(value, "max_length")
        if max_length is not None:
            column["max_length"] = max_length
        target = _py_django_relation_target(value) if relation else ""
        if target:
            column["relation_model"] = target
        columns.append(column)
        references_table = _py_django_target_table(target, app_label, table, model_tables)
        if references_table and field_type != "ManyToManyField":
            foreign_keys.append(
                {
                    "table": table,
                    "column": column_name,
                    "references_table": references_table,
                    "path": rel,
                    "line": column["line"],
                }
            )
    return columns, foreign_keys


def _py_assign_name(item: ast.AST) -> str:
    if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
        return item.targets[0].id
    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
        return item.target.id
    return ""


def _py_assign_value(item: ast.AST) -> ast.AST | None:
    if isinstance(item, ast.Assign):
        return item.value
    if isinstance(item, ast.AnnAssign):
        return item.value
    return None


def _py_sqlalchemy_table_name(node: ast.ClassDef) -> str:
    for item in node.body:
        if not isinstance(item, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__tablename__" for target in item.targets):
            continue
        table = _py_string(item.value)
        if table:
            return table
    return ""


def _py_sqlalchemy_column_type(arg: ast.AST | None) -> str:
    if arg is None:
        return ""
    if isinstance(arg, ast.Call):
        return _py_dotted_name(arg.func).split(".")[-1]
    return _py_dotted_name(arg).split(".")[-1]


def _py_sqlalchemy_foreign_key(call: ast.Call) -> tuple[str, str]:
    for arg in call.args:
        if not isinstance(arg, ast.Call) or _py_dotted_name(arg.func).split(".")[-1] != "ForeignKey" or not arg.args:
            continue
        target = _py_string(arg.args[0])
        if not target:
            continue
        if "." in target:
            table, column = target.split(".", 1)
            return table, column
        return target, ""
    return "", ""


def _py_sqlalchemy_column(field_name: str, value: ast.AST | None, rel: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if not field_name or not isinstance(value, ast.Call):
        return None, None
    call_name = _py_dotted_name(value.func).split(".")[-1]
    if call_name not in PY_SQLALCHEMY_COLUMN_CALLS:
        return None, None

    args = list(value.args)
    column_name = field_name
    type_arg: ast.AST | None = None
    if args and isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
        column_name = args[0].value
        type_arg = args[1] if len(args) > 1 else None
    elif args:
        type_arg = args[0]

    column = {
        "name": column_name,
        "field": field_name,
        "type": _py_sqlalchemy_column_type(type_arg),
        "path": rel,
        "line": getattr(value, "lineno", 0),
    }
    for keyword in ("nullable", "unique", "index", "primary_key"):
        keyword_value = _py_keyword_bool(value, keyword)
        if keyword_value is not None:
            column[keyword] = keyword_value

    ref_table, ref_column = _py_sqlalchemy_foreign_key(value)
    foreign_key = None
    if ref_table:
        foreign_key = {
            "column": column_name,
            "references_table": ref_table,
            "references_column": ref_column,
            "path": rel,
            "line": column["line"],
        }
    return column, foreign_key


def _py_sqlalchemy_model_fields(node: ast.ClassDef, table: str, rel: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    columns: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, Any]] = []
    for item in node.body:
        field_name = _py_assign_name(item)
        column, foreign_key = _py_sqlalchemy_column(field_name, _py_assign_value(item), rel)
        if column is None:
            continue
        columns.append(column)
        if foreign_key is not None:
            foreign_key["table"] = table
            foreign_keys.append(foreign_key)
    return columns, foreign_keys


def _prisma_table_name(model_name: str, body: str) -> str:
    match = PRISMA_MAP_RE.search(body)
    return match.group("table") if match else model_name


def _balanced_end(source: str, start: int, open_char: str, close_char: str) -> int:
    depth = 0
    quote = ""
    escape = False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _split_top_level_items(body: str) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    start = 0
    parens = 0
    braces = 0
    brackets = 0
    quote = ""
    escape = False
    for index, char in enumerate(body):
        if quote:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "(":
            parens += 1
        elif char == ")" and parens > 0:
            parens -= 1
        elif char == "{":
            braces += 1
        elif char == "}" and braces > 0:
            braces -= 1
        elif char == "[":
            brackets += 1
        elif char == "]" and brackets > 0:
            brackets -= 1
        elif char == "," and parens == 0 and braces == 0 and brackets == 0:
            raw = body[start:index]
            stripped = raw.strip()
            if stripped:
                items.append((stripped, start + len(raw) - len(raw.lstrip())))
            start = index + 1
    raw_tail = body[start:]
    tail = raw_tail.strip()
    if tail:
        items.append((tail, start + len(raw_tail) - len(raw_tail.lstrip())))
    return items


def _drizzle_table_declarations(source: str, rel: str) -> list[dict[str, Any]]:
    declarations: list[dict[str, Any]] = []
    for match in DRIZZLE_TABLE_RE.finditer(source):
        object_start = match.end() - 1
        object_end = _balanced_end(source, object_start, "{", "}")
        if object_end == -1:
            continue
        body = source[object_start + 1 : object_end]
        fields: list[dict[str, Any]] = []
        for item, item_offset in _split_top_level_items(body):
            field_match = DRIZZLE_FIELD_RE.match(item)
            if not field_match:
                continue
            expr = field_match.group("expr").strip()
            column_match = DRIZZLE_COLUMN_RE.search(expr)
            if not column_match:
                continue
            field = {
                "field": field_match.group("field"),
                "name": column_match.group("column"),
                "type": column_match.group("type"),
                "path": rel,
                "line": _line_number(source, object_start + 1 + item_offset),
                "primary_key": ".primaryKey(" in expr,
                "nullable": False if ".notNull(" in expr else None,
                "unique": True if ".unique(" in expr else None,
                "has_default": True if ".default(" in expr else None,
            }
            reference_match = DRIZZLE_REFERENCES_RE.search(expr)
            if reference_match:
                field["references_table_var"] = reference_match.group("table")
                field["references_field"] = reference_match.group("column")
            fields.append({key: value for key, value in field.items() if value is not None})
        declarations.append(
            {
                "var": match.group("var"),
                "table": match.group("table"),
                "factory": match.group("fn"),
                "path": rel,
                "line": _line_number(source, match.start()),
                "fields": fields,
            }
        )
    return declarations


def _drizzle_schema_graph(
    source: str,
    rel: str,
    *,
    max_symbols: int,
    max_edges: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    declarations = _drizzle_table_declarations(source, rel)
    table_by_var = {item["var"]: item["table"] for item in declarations}
    column_by_var_field = {
        (item["var"], field["field"]): field["name"]
        for item in declarations
        for field in item.get("fields", [])
    }
    tables: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    truncated = False
    for declaration in declarations:
        table_name = str(declaration["table"])
        foreign_keys: list[dict[str, Any]] = []
        if len(symbols) < max_symbols:
            symbols.append(
                {
                    "kind": "model",
                    "name": str(declaration["var"]),
                    "role": "drizzle_table",
                    "path": rel,
                    "line": declaration["line"],
                }
            )
        else:
            truncated = True
        for field in declaration.get("fields", []):
            target_var = field.get("references_table_var")
            target_field = field.get("references_field")
            if not target_var or not target_field:
                continue
            references_table = table_by_var.get(str(target_var), str(target_var))
            references_column = column_by_var_field.get((str(target_var), str(target_field)), str(target_field))
            foreign_keys.append(
                {
                    "table": table_name,
                    "column": field["name"],
                    "references_table": references_table,
                    "references_column": references_column,
                    "path": rel,
                    "line": field["line"],
                }
            )
        tables.append(
            {
                "table": table_name,
                "model": str(declaration["var"]),
                "orm": "drizzle",
                "factory": str(declaration["factory"]),
                "path": rel,
                "line": declaration["line"],
                "columns": [
                    {
                        key: value
                        for key, value in field.items()
                        if key not in {"references_table_var", "references_field"}
                    }
                    for field in declaration.get("fields", [])
                ][:200],
                "foreign_keys": foreign_keys[:100],
            }
        )
        truncated = not _edge_append(
            edges,
            {
                "kind": "model_table",
                "from": str(declaration["var"]),
                "to": f"table:{table_name}",
                "framework": "drizzle",
                "path": rel,
                "line": declaration["line"],
            },
            max_edges=max_edges,
        ) or truncated
        for foreign_key in foreign_keys:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "foreign_key",
                    "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                    "to": f"table:{foreign_key['references_table']}",
                    "framework": "drizzle",
                    "path": rel,
                    "line": foreign_key.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return tables, symbols, edges, truncated


def _prisma_list_arg(relation_body: str, name: str) -> list[str]:
    for match in PRISMA_LIST_ARG_RE.finditer(relation_body or ""):
        if match.group("name") != name:
            continue
        return [item.strip() for item in match.group("values").split(",") if item.strip()]
    return []


def _prisma_model_graph(
    source: str,
    rel: str,
    *,
    max_symbols: int,
    max_edges: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    model_matches = list(PRISMA_MODEL_RE.finditer(source))
    table_by_model = {match.group("name"): _prisma_table_name(match.group("name"), match.group("body")) for match in model_matches}
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    truncated = False

    for model_match in model_matches:
        model_name = model_match.group("name")
        body = model_match.group("body")
        table_name = table_by_model[model_name]
        columns: list[dict[str, Any]] = []
        foreign_keys: list[dict[str, Any]] = []
        line = _line_number(source, model_match.start())

        if len(symbols) < max_symbols:
            symbols.append(
                {
                    "kind": "model",
                    "name": model_name,
                    "role": "prisma_model",
                    "path": rel,
                    "line": line,
                }
            )
        else:
            truncated = True

        for field_match in PRISMA_FIELD_RE.finditer(body):
            field_name = field_match.group("name")
            raw_type = field_match.group("type")
            base_type = raw_type.rstrip("?").removesuffix("[]")
            attrs = field_match.group("attrs") or ""
            field_line = _line_number(source, model_match.start("body") + field_match.start())
            if base_type in PRISMA_SCALAR_TYPES:
                column = {
                    "name": field_name,
                    "field": field_name,
                    "type": base_type,
                    "path": rel,
                    "line": field_line,
                    "optional": raw_type.endswith("?"),
                    "list": raw_type.endswith("[]") or raw_type.endswith("[]?"),
                }
                if "@id" in attrs:
                    column["primary_key"] = True
                if "@unique" in attrs:
                    column["unique"] = True
                if "@default" in attrs:
                    column["has_default"] = True
                columns.append(column)
                continue

            relation_match = PRISMA_RELATION_RE.search(attrs)
            if not relation_match:
                continue
            fields = _prisma_list_arg(relation_match.group("body"), "fields")
            references = _prisma_list_arg(relation_match.group("body"), "references")
            target_table = table_by_model.get(base_type, base_type)
            for index, field in enumerate(fields):
                foreign_keys.append(
                    {
                        "table": table_name,
                        "column": field,
                        "references_table": target_table,
                        "references_column": references[index] if index < len(references) else "",
                        "path": rel,
                        "line": field_line,
                    }
                )

        tables.append(
            {
                "table": table_name,
                "model": model_name,
                "orm": "prisma",
                "path": rel,
                "line": line,
                "columns": columns[:200],
                "foreign_keys": foreign_keys[:100],
            }
        )
        truncated = not _edge_append(
            edges,
            {
                "kind": "model_table",
                "from": model_name,
                "to": f"table:{table_name}",
                "framework": "prisma",
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated
        for foreign_key in foreign_keys:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "foreign_key",
                    "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                    "to": f"table:{foreign_key['references_table']}",
                    "framework": "prisma",
                    "path": rel,
                    "line": foreign_key.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return tables, symbols, edges, truncated


def _sql_identifier(raw: str) -> str:
    return str(raw or "").strip().strip("`\"")


def _sql_split_items(body: str) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    start = 0
    depth = 0
    quote = ""
    for index, char in enumerate(body):
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"', "`"}:
            quote = char
            continue
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            raw = body[start:index]
            items.append((raw.strip(), start + len(raw) - len(raw.lstrip())))
            start = index + 1
    raw_tail = body[start:]
    tail = raw_tail.strip()
    if tail:
        items.append((tail, start + len(raw_tail) - len(raw_tail.lstrip())))
    return items


def _sql_schema_graph(
    source: str,
    rel: str,
    *,
    max_symbols: int,
    max_edges: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool]:
    tables: list[dict[str, Any]] = []
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    truncated = False
    for match in SQL_CREATE_TABLE_RE.finditer(source):
        table_name = _sql_identifier(match.group("table").split(".")[-1])
        body = match.group("body")
        columns: list[dict[str, Any]] = []
        foreign_keys: list[dict[str, Any]] = []
        line = _line_number(source, match.start())
        if len(symbols) < max_symbols:
            symbols.append({"kind": "table", "name": f"table:{table_name}", "table": table_name, "path": rel, "line": line})
        else:
            truncated = True

        for item, body_offset in _sql_split_items(body):
            upper = item.upper()
            item_line = _line_number(source, match.start("body") + body_offset)
            table_fk = SQL_TABLE_FOREIGN_KEY_RE.search(item)
            if table_fk:
                foreign_keys.append(
                    {
                        "table": table_name,
                        "column": _sql_identifier(table_fk.group("column")),
                        "references_table": _sql_identifier(table_fk.group("table").split(".")[-1]),
                        "references_column": _sql_identifier(table_fk.group("ref_column")),
                        "path": rel,
                        "line": item_line,
                    }
                )
                continue
            if upper.startswith(("CONSTRAINT ", "PRIMARY KEY", "UNIQUE ", "KEY ", "INDEX ", "CHECK ")):
                continue
            tokens = item.split()
            if len(tokens) < 2:
                continue
            column_name = _sql_identifier(tokens[0])
            column_type = tokens[1].strip(",")
            column = {
                "name": column_name,
                "type": column_type,
                "path": rel,
                "line": item_line,
            }
            if "PRIMARY KEY" in upper:
                column["primary_key"] = True
            if "NOT NULL" in upper:
                column["nullable"] = False
            if " UNIQUE" in f" {upper}":
                column["unique"] = True
            columns.append(column)
            inline_fk = SQL_INLINE_REFERENCE_RE.search(item)
            if inline_fk:
                foreign_keys.append(
                    {
                        "table": table_name,
                        "column": column_name,
                        "references_table": _sql_identifier(inline_fk.group("table").split(".")[-1]),
                        "references_column": _sql_identifier(inline_fk.group("column")),
                        "path": rel,
                        "line": item_line,
                    }
                )
        tables.append(
            {
                "table": table_name,
                "source": "sql",
                "path": rel,
                "line": line,
                "columns": columns[:200],
                "foreign_keys": foreign_keys[:100],
            }
        )
        truncated = not _edge_append(
            edges,
            {
                "kind": "schema_table",
                "from": rel,
                "to": f"table:{table_name}",
                "framework": "sql",
                "path": rel,
                "line": line,
            },
            max_edges=max_edges,
        ) or truncated
        for foreign_key in foreign_keys:
            truncated = not _edge_append(
                edges,
                {
                    "kind": "foreign_key",
                    "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                    "to": f"table:{foreign_key['references_table']}",
                    "framework": "sql",
                    "path": rel,
                    "line": foreign_key.get("line"),
                },
                max_edges=max_edges,
            ) or truncated
    return tables, symbols, edges, truncated


def _build_sql_graph(
    workspace_root: Path,
    candidates: list[Path],
    omitted: list[dict[str, str]],
    *,
    truncated: bool,
    max_symbols: int,
    max_edges: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    sql_files = [path for path in candidates if path.suffix.lower() == ".sql"]
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    database: dict[str, Any] = {"tables": []}
    for path in sql_files:
        rel = path.relative_to(workspace_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue
        tables, sql_symbols, sql_edges, sql_truncated = _sql_schema_graph(
            source,
            rel,
            max_symbols=max(0, max_symbols - len(symbols)),
            max_edges=max(0, max_edges - len(edges)),
        )
        database["tables"].extend(tables)
        symbols.extend(sql_symbols)
        edges.extend(sql_edges)
        truncated = truncated or sql_truncated
    graph_database = {**database, "tables": database["tables"][:500]}
    graph = {
        "schema": "hades.code_graph.v1",
        "language": "sql",
        "framework": "sql",
        "root": workspace_root.name,
        "routes": [],
        "symbols": symbols,
        "edges": edges,
        "database": graph_database,
        "summary": "",
        "omitted": omitted,
        "truncated": truncated or len(symbols) >= max_symbols or len(edges) >= max_edges or len(database["tables"]) > 500,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }
    graph["summary"] = _ts_graph_summary([], symbols, edges, framework="sql", database=graph_database)
    return graph


def _ts_framework(root: Path, files: list[Path], dependency_manifests: list[dict[str, Any]]) -> str:
    packages = {
        str(package)
        for manifest in dependency_manifests
        for package in (manifest.get("packages") or [])
    }
    if "next" in packages or any(NEXT_ROUTE_FILE_RE.search(path.relative_to(root).as_posix()) for path in files):
        return "nextjs"
    if "react" in packages or any(path.suffix.lower() in {".tsx", ".jsx"} for path in files):
        return "react"
    if "express" in packages:
        return "express"
    return "node"


def _route_from_next_path(rel: str) -> str:
    for pattern in (NEXT_ROUTE_FILE_RE, NEXT_PAGE_FILE_RE):
        match = pattern.search(rel)
        if not match:
            continue
        route = match.group("route")
        clean = "/" + route.replace("/(group)", "").replace("index", "").strip("/")
        return clean if clean != "/" else "/"
    return ""


def _append_ts_symbol(
    symbols: list[dict[str, Any]],
    symbol: dict[str, Any],
    *,
    max_symbols: int,
) -> bool:
    if len(symbols) >= max_symbols:
        return False
    symbols.append({key: value for key, value in symbol.items() if value not in ("", None)})
    return True


def _append_ts_log_events(
    source: str,
    rel: str,
    edges: list[dict[str, Any]],
    log_events: list[dict[str, Any]],
    *,
    max_edges: int,
) -> bool:
    truncated = False
    for match in TS_LOG_CALL_RE.finditer(source):
        if len(log_events) >= MAX_LOG_EVENTS:
            truncated = True
            break
        level = match.group("level").lower()
        if level in {"warn", "log"}:
            level = "warning" if level == "warn" else "info"
        message = redact_secret(match.group("message") or "")
        payload = {
            "context": rel,
            "logger": match.group("logger"),
            "level": level,
            "path": rel,
            "line": _line_number(source, match.start()),
            "message_sha256": hashlib.sha256(message.encode("utf-8")).hexdigest() if message else "",
            "message_length": len(message) if message else 0,
        }
        log_event = {key: value for key, value in payload.items() if value not in ("", None, 0)}
        log_id_payload = json.dumps(log_event, sort_keys=True, separators=(",", ":")).encode("utf-8")
        log_id = hashlib.sha256(log_id_payload).hexdigest()[:16]
        log_event = {"id": f"log:{log_id}", **log_event}
        log_events.append(log_event)
        truncated = not _edge_append(
            edges,
            {
                "kind": "emits_log",
                "from": rel,
                "to": log_event["id"],
                "level": log_event.get("level"),
                "logger": log_event.get("logger"),
                "path": rel,
                "line": log_event.get("line"),
            },
            max_edges=max_edges,
        ) or truncated
    return truncated


def _build_ts_graph(
    workspace_root: Path,
    candidates: list[Path],
    omitted: list[dict[str, str]],
    *,
    truncated: bool,
    max_symbols: int,
    max_edges: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    ts_files = [path for path in candidates if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}]
    prisma_files = [path for path in candidates if path.suffix.lower() == ".prisma"]
    file_refs = [{"path": path.relative_to(workspace_root).as_posix(), "bytes": path.stat().st_size} for path in candidates if path.is_file()]
    dependency_manifests = _dependency_manifests(workspace_root, file_refs)
    framework = _ts_framework(workspace_root, ts_files, dependency_manifests)
    routes: list[dict[str, str]] = []
    symbols: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    database: dict[str, Any] = {"tables": []}
    log_events: list[dict[str, Any]] = []

    for path in ts_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue

        route_path = _route_from_next_path(rel)
        if route_path:
            for match in NEXT_HTTP_EXPORT_RE.finditer(source):
                routes.append(
                    {
                        "framework": "nextjs",
                        "method": match.group("method"),
                        "path": route_path,
                        "handler": f"{rel}:{match.group('method')}",
                        "source_path": rel,
                    }
                )
            if rel.endswith(("/page.tsx", "/page.jsx", "/page.ts", "/page.js")):
                routes.append(
                    {
                        "framework": "nextjs",
                        "method": "PAGE",
                        "path": route_path,
                        "handler": rel,
                        "source_path": rel,
                    }
                )

        for match in EXPRESS_ROUTE_RE.finditer(source):
            routes.append(
                {
                    "framework": "express",
                    "method": match.group("method").upper(),
                    "path": match.group("path"),
                    "handler": match.group("handler") or "",
                    "source_path": rel,
                }
            )

        truncated = _append_ts_log_events(source, rel, edges, log_events, max_edges=max_edges) or truncated
        for match in TS_IMPORT_RE.finditer(source):
            truncated = not _edge_append(
                edges,
                {
                    "kind": "imports",
                    "from": rel,
                    "to": match.group("target"),
                    "path": rel,
                    "line": _line_number(source, match.start()),
                },
                max_edges=max_edges,
            ) or truncated

        for kind, pattern in (
            ("export", TS_EXPORT_DECL_RE),
            ("function", TS_FUNCTION_RE),
            ("component", TS_ARROW_COMPONENT_RE),
            ("class", TS_CLASS_RE),
        ):
            for match in pattern.finditer(source):
                name = match.group("name")
                symbol_kind = "component" if kind == "component" or (path.suffix.lower() in {".tsx", ".jsx"} and name[:1].isupper()) else kind
                truncated = not _append_ts_symbol(
                    symbols,
                    {
                        "kind": symbol_kind,
                        "name": name,
                        "path": rel,
                        "line": _line_number(source, match.start()),
                        "framework": framework,
                    },
                    max_symbols=max_symbols,
                ) or truncated
                if len(symbols) >= max_symbols:
                    break
            if len(symbols) >= max_symbols:
                break

        drizzle_tables, drizzle_symbols, drizzle_edges, drizzle_truncated = _drizzle_schema_graph(
            source,
            rel,
            max_symbols=max(0, max_symbols - len(symbols)),
            max_edges=max(0, max_edges - len(edges)),
        )
        database["tables"].extend(drizzle_tables)
        symbols.extend(drizzle_symbols)
        edges.extend(drizzle_edges)
        truncated = truncated or drizzle_truncated

    for path in prisma_files:
        rel = path.relative_to(workspace_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue
        prisma_tables, prisma_symbols, prisma_edges, prisma_truncated = _prisma_model_graph(
            source,
            rel,
            max_symbols=max(0, max_symbols - len(symbols)),
            max_edges=max(0, max_edges - len(edges)),
        )
        database["tables"].extend(prisma_tables)
        symbols.extend(prisma_symbols)
        edges.extend(prisma_edges)
        truncated = truncated or prisma_truncated

    if database["tables"] and framework == "node" and not routes:
        table_orms = {str(item.get("orm") or item.get("source") or "") for item in database["tables"]}
        if table_orms == {"drizzle"}:
            framework = "drizzle"
        elif table_orms == {"prisma"}:
            framework = "prisma"
        elif table_orms == {"sql"}:
            framework = "sql"
    if prisma_files and not ts_files:
        framework = "prisma"
    tests, tests_truncated = _build_test_map(
        workspace_root,
        candidates,
        routes,
        symbols,
        edges,
        max_edges=max_edges,
        max_file_bytes=max_file_bytes,
    )
    truncated = truncated or tests_truncated
    logs = {
        "schema": "hades.log_map.v1",
        "event_count": len(log_events),
        "events": log_events[:MAX_LOG_EVENTS],
        "truncated": len(log_events) > MAX_LOG_EVENTS,
        "raw_source_included": False,
    }
    graph_database = {**database, "tables": database["tables"][:500]}
    language = "prisma"
    if ts_files:
        language = "typescript" if any(path.suffix.lower() in {".ts", ".tsx"} for path in ts_files) else "javascript"
    graph = {
        "schema": "hades.code_graph.v1",
        "language": language,
        "framework": framework,
        "root": workspace_root.name,
        "routes": routes[:500],
        "symbols": symbols,
        "edges": edges,
        "database": graph_database,
        "tests": tests,
        "logs": logs,
        "dependency_manifests": dependency_manifests,
        "summary": "",
        "omitted": omitted,
        "truncated": truncated
        or len(symbols) >= max_symbols
        or len(edges) >= max_edges
        or len(routes) > 500
        or len(database["tables"]) > 500,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }
    graph["summary"] = _ts_graph_summary(
        graph["routes"],
        symbols,
        edges,
        framework=framework,
        database=graph_database,
        tests=tests,
        logs=logs,
    )
    return graph


def _build_python_artifact(
    workspace_root: Path,
    candidates: list[Path],
    omitted: list[dict[str, str]],
    *,
    truncated: bool,
    max_symbols: int,
    max_edges: int,
    max_file_bytes: int,
) -> dict[str, Any]:
    symbols: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    database: dict[str, Any] = {"tables": []}
    log_events: list[dict[str, Any]] = []
    frameworks: set[str] = set()
    python_files = [path for path in candidates if path.suffix == ".py"]

    for path in python_files:
        rel = path.relative_to(workspace_root).as_posix()
        if _is_test_path(rel):
            continue
        try:
            size = path.stat().st_size
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("stat_error", exc)})
            continue
        if size > max_file_bytes:
            omitted.append({"path": rel, "reason": "file_too_large"})
            truncated = True
            continue
        try:
            source, was_truncated, _digest = _read_text_bounded(path, max_file_bytes)
            if was_truncated:
                omitted.append({"path": rel, "reason": "file_too_large"})
                truncated = True
                continue
            tree = ast.parse(source)
        except SyntaxError as exc:
            omitted.append({"path": rel, "reason": f"syntax_error:{exc.lineno}"})
            continue
        except OSError as exc:
            omitted.append({"path": rel, "reason": _io_error_reason("read_error", exc)})
            continue

        py_imports, import_truncated = _py_import_aliases_and_edges(tree, rel, edges, max_edges=max_edges)
        truncated = truncated or import_truncated
        router_prefixes: dict[str, str] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            if _py_dotted_name(node.value.func).split(".")[-1] != "APIRouter":
                continue
            prefix = _py_keyword_string(node.value, "prefix")
            for target in node.targets:
                if isinstance(target, ast.Name):
                    router_prefixes[target.id] = prefix

        django_model_tables: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and _py_django_model_base(node):
                table, _app_label = _py_django_model_table(node, rel)
                django_model_tables[node.name] = table

        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                symbol = {"kind": "class", "name": node.name, "path": rel, "line": node.lineno}
                if _py_django_model_base(node):
                    table, app_label = _py_django_model_table(node, rel)
                    columns, foreign_keys = _py_django_model_fields(node, table, app_label, rel, django_model_tables)
                    if columns or foreign_keys:
                        symbol["role"] = "django_model"
                        database["tables"].append(
                            {
                                "table": table,
                                "model": node.name,
                                "app_label": app_label,
                                "path": rel,
                                "line": node.lineno,
                                "columns": columns[:200],
                                "foreign_keys": foreign_keys[:100],
                            }
                        )
                        frameworks.add("django")
                        truncated = not _edge_append(
                            edges,
                            {
                                "kind": "model_table",
                                "from": node.name,
                                "to": f"table:{table}",
                                "framework": "django",
                                "path": rel,
                                "line": node.lineno,
                            },
                            max_edges=max_edges,
                        ) or truncated
                        for foreign_key in foreign_keys:
                            truncated = not _edge_append(
                                edges,
                                {
                                    "kind": "foreign_key",
                                    "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                                    "to": f"table:{foreign_key['references_table']}",
                                    "framework": "django",
                                    "path": rel,
                                    "line": foreign_key.get("line"),
                                },
                                max_edges=max_edges,
                            ) or truncated
                else:
                    table = _py_sqlalchemy_table_name(node)
                    if table:
                        columns, foreign_keys = _py_sqlalchemy_model_fields(node, table, rel)
                        if columns or foreign_keys:
                            symbol["role"] = "sqlalchemy_model"
                            database["tables"].append(
                                {
                                    "table": table,
                                    "model": node.name,
                                    "orm": "sqlalchemy",
                                    "path": rel,
                                    "line": node.lineno,
                                    "columns": columns[:200],
                                    "foreign_keys": foreign_keys[:100],
                                }
                            )
                            frameworks.add("sqlalchemy")
                            truncated = not _edge_append(
                                edges,
                                {
                                    "kind": "model_table",
                                    "from": node.name,
                                    "to": f"table:{table}",
                                    "framework": "sqlalchemy",
                                    "path": rel,
                                    "line": node.lineno,
                                },
                                max_edges=max_edges,
                            ) or truncated
                            for foreign_key in foreign_keys:
                                truncated = not _edge_append(
                                    edges,
                                    {
                                        "kind": "foreign_key",
                                        "from": f"table:{foreign_key['table']}.{foreign_key['column']}",
                                        "to": f"table:{foreign_key['references_table']}",
                                        "framework": "sqlalchemy",
                                        "path": rel,
                                        "line": foreign_key.get("line"),
                                    },
                                    max_edges=max_edges,
                                ) or truncated
                if len(symbols) < max_symbols:
                    symbols.append(symbol)
                else:
                    truncated = True
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if len(symbols) < max_symbols:
                    symbols.append({"kind": "function", "name": node.name, "path": rel, "line": node.lineno})
                else:
                    truncated = True
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    decorator_name = _py_dotted_name(decorator.func)
                    decorator_parts = decorator_name.split(".")
                    method = decorator_parts[-1] if decorator_parts else ""
                    router_name = decorator_parts[-2] if len(decorator_parts) >= 2 else ""
                    if method not in PY_HTTP_METHODS or not decorator.args:
                        continue
                    route_path = _py_string(decorator.args[0])
                    if not route_path:
                        continue
                    route = {
                        "framework": "fastapi",
                        "method": "ANY" if method in {"api_route", "route"} else method.upper(),
                        "path": _join_url_path(router_prefixes.get(router_name, ""), route_path),
                        "handler": node.name,
                        "source_path": rel,
                        "line": getattr(decorator, "lineno", node.lineno),
                    }
                    route_name = _py_keyword_string(decorator, "name")
                    if route_name:
                        route["name"] = route_name
                    routes.append(route)
                    frameworks.add("fastapi")
                    truncated = not _edge_append(
                        edges,
                        {
                            "kind": "route_handler",
                            "from": f"route:{_py_route_id(route)}",
                            "to": node.name,
                            "framework": "fastapi",
                            "path": rel,
                            "line": getattr(decorator, "lineno", node.lineno),
                        },
                        max_edges=max_edges,
                    ) or truncated
            if len(symbols) >= max_symbols:
                truncated = True

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call_name = _py_dotted_name(node.func).split(".")[-1]
            if call_name not in PY_DJANGO_ROUTE_FUNCS or len(node.args) < 2:
                continue
            route_path = _py_string(node.args[0])
            handler = _py_dotted_name(node.args[1])
            if not route_path or not handler:
                continue
            route = {
                "framework": "django",
                "method": "ROUTE",
                "path": route_path,
                "handler": handler,
                "source_path": rel,
                "line": getattr(node, "lineno", 0),
            }
            route_name = _py_keyword_string(node, "name")
            if route_name:
                route["name"] = route_name
            routes.append(route)
            frameworks.add("django")
            truncated = not _edge_append(
                edges,
                {
                    "kind": "route_handler",
                    "from": f"route:{_py_route_id(route)}",
                    "to": handler,
                    "framework": "django",
                    "path": rel,
                    "line": getattr(node, "lineno", 0),
                },
                max_edges=max_edges,
            ) or truncated

        truncated = _append_python_call_edges(
            tree,
            rel,
            py_imports,
            edges,
            log_events,
            max_edges=max_edges,
        ) or truncated
        if len(symbols) >= max_symbols:
            break

    tests, tests_truncated = _build_test_map(
        workspace_root,
        candidates,
        routes,
        symbols,
        edges,
        max_edges=max_edges,
        max_file_bytes=max_file_bytes,
    )
    truncated = truncated or tests_truncated
    logs = {
        "schema": "hades.log_map.v1",
        "event_count": len(log_events),
        "events": log_events[:MAX_LOG_EVENTS],
        "truncated": len(log_events) > MAX_LOG_EVENTS,
        "raw_source_included": False,
    }
    if routes or database["tables"]:
        framework = "python_web" if len(frameworks) > 1 else next(iter(frameworks), "python")
        graph_database = {**database, "tables": database["tables"][:500]}
        graph = {
            "schema": "hades.code_graph.v1",
            "language": "python",
            "framework": framework,
            "root": workspace_root.name,
            "routes": routes[:500],
            "symbols": symbols,
            "edges": edges,
            "database": graph_database,
            "tests": tests,
            "logs": logs,
            "summary": "",
            "omitted": omitted,
            "truncated": truncated
            or len(symbols) >= max_symbols
            or len(edges) >= max_edges
            or len(routes) > 500
            or len(database["tables"]) > 500,
            "redactions": len(omitted),
            "retention_class": "source_symbols",
            "raw_source_included": False,
        }
        graph["summary"] = _py_graph_summary(
            graph["routes"],
            symbols,
            edges,
            framework=framework,
            database=graph_database,
            tests=tests,
            logs=logs,
        )
        return graph

    return {
        "schema": "hades.symbols.v1",
        "symbols": symbols,
        "tests": tests,
        "logs": logs,
        "omitted": omitted,
        "truncated": truncated or len(symbols) >= max_symbols,
        "redactions": len(omitted),
        "retention_class": "source_symbols",
        "raw_source_included": False,
    }


def _execute_populate_backend_ast(job: dict[str, Any], workspace_root: Path) -> dict[str, Any]:
    payload = job.get("payload") or {}
    max_files = int(payload.get("max_files") or 1_000)
    max_symbols = int(payload.get("max_symbols") or 5_000)
    max_file_bytes = int(payload.get("max_file_bytes") or 512_000)
    candidates, omitted, truncated = _iter_workspace_files(workspace_root, max_files=max_files)
    if any(path.suffix.lower() == ".php" for path in candidates):
        max_edges = int(payload.get("max_edges") or max_symbols * 2)
        graph = _build_php_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
        return {
            "status": "completed",
            "summary": graph["summary"],
            "artifact": graph,
        }
    if any(path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx", ".prisma"} for path in candidates):
        max_edges = int(payload.get("max_edges") or max_symbols * 2)
        graph = _build_ts_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
        return {
            "status": "completed",
            "summary": graph["summary"],
            "artifact": graph,
        }
    if any(path.suffix.lower() == ".sql" for path in candidates):
        max_edges = int(payload.get("max_edges") or max_symbols * 2)
        graph = _build_sql_graph(
            workspace_root,
            candidates,
            omitted,
            truncated=truncated,
            max_symbols=max_symbols,
            max_edges=max_edges,
            max_file_bytes=max_file_bytes,
        )
        return {
            "status": "completed",
            "summary": graph["summary"],
            "artifact": graph,
        }

    max_edges = int(payload.get("max_edges") or max_symbols * 2)
    artifact = _build_python_artifact(
        workspace_root,
        candidates,
        omitted,
        truncated=truncated,
        max_symbols=max_symbols,
        max_edges=max_edges,
        max_file_bytes=max_file_bytes,
    )
    return {
        "status": "completed",
        "summary": artifact.get("summary") or f"Collected {len(artifact.get('symbols') or [])} symbol(s).",
        "artifact": artifact,
    }


def execute_job(job: dict[str, Any], *, workspace_root: str | Path) -> dict[str, Any]:
    root = Path(workspace_root).resolve()
    capability = str(job.get("capability") or "")
    if capability == "read_files":
        return _execute_read_files(job, root)
    if capability == "read_source_slice":
        return _execute_read_source_slice(job, root)
    if capability == "sync_git_tree":
        return _execute_sync_git_tree(job, root)
    if capability == "populate_backend_ast":
        return _execute_populate_backend_ast(job, root)
    if capability == "project_inspection":
        return _execute_project_inspection(job, root)
    return {
        "status": "failed",
        "summary": f"Unsupported Hades backend job capability: {capability}",
        "omitted": [{"reason": "unsupported_capability"}],
    }
