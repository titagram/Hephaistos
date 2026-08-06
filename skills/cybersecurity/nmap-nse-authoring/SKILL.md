---
name: nmap-nse-authoring
description: Author custom Nmap NSE scripts in Lua — structure, APIs, testing, and publishing.
version: 0.1.0
author: Hermes
platforms: [linux, macos]
metadata:
  hermes:
    tags:
      - Nmap
      - NSE
      - Lua
      - Scripting
---

# Nmap NSE Script Authoring

Write custom Nmap Scripting Engine (NSE) scripts in Lua. Covers script anatomy, NSE APIs, port/host rules, debugging, and distribution. Use when you need a check that doesn't exist in the default script database.

## When to Use

- Need a custom service check, vulnerability test, or enumeration not covered by built-in scripts
- Automating a proprietary protocol or internal service
- Extending Nmap for a CTF challenge or specialized audit
- Contributing a script upstream to the Nmap project

## Prerequisites

- `nmap` with NSE support (standard install)
- Lua 5.1+ runtime (embedded in Nmap)
- Text editor; optional: `nmap --script-trace` for debugging
- Read `nmap-nse-doc` script for API reference: `nmap --script-help nse_doc`

## How to Run

Test scripts through the `terminal` tool:

```bash
# Syntax check (dry-run)
nmap --script /path/to/script.nse -p 80 127.0.0.1 --script-args=help

# Run with trace
nmap --script /path/to/script.nse -p 80 192.168.1.1 --script-trace

# Install to user script dir
cp script.nse ~/.nmap/scripts/
nmap --script-updatedb
```

## Quick Reference

### Script Skeleton

```lua
-- script.nse
description = [[
Short one-line summary.
Longer description of what the script does, what it detects,
and any caveats or requirements.
]]

author = "Your Name"
license = "Same as Nmap--See https://nmap.org/book/man-legal.html"
categories = {"discovery", "safe"}  -- or "intrusive", "vuln", "auth", etc.

-- Rules: at least one of portrule, hostrule, prerule, postrule
portrule = function(host, port)
  return port.protocol == "tcp" and port.state == "open"
end

action = function(host, port)
  local result = "Script output here"
  return result
end
```

### Rule Types

| Rule | When It Runs |
|------|--------------|
| `prerule()` | Before any scan, once per Nmap run |
| `hostrule(host)` | Once per *up* host |
| `portrule(host, port)` | Once per *open* port matching criteria |
| `postrule()` | After all scans, once per Nmap run |

### Common Portrule Patterns

```lua
-- Specific port/service
portrule = shortport.port_or_service(80, "http")

-- Multiple ports
portrule = function(host, port)
  return stdnse.contains({80, 443, 8080}, port.number)
end

-- Service name match
portrule = shortport.service({"http", "https", "http-proxy"})

-- Version detection match
portrule = function(host, port)
  return port.version and port.version.name == "nginx"
end

-- SSL/TLS ports
portrule = shortport.ssl

-- Any open TCP port
portrule = function(host, port)
  return port.protocol == "tcp" and port.state == "open"
end
```

### Key NSE Libraries (require)

| Library | Purpose |
|---------|---------|
| `stdnse` | Core utilities: logging, output, args, strings, tables |
| `shortport` | Port/service rule helpers |
| `nmap` | Registry, version info, scan data |
| `http` | HTTP client (GET, POST, pipeline, auth) |
| `httpspider` | Web crawling |
| `sslcert` | SSL certificate parsing |
| `ssh2` | SSH protocol |
| `smb` | SMB/MSRPC |
| `dns` | DNS queries |
| `snmp` | SNMP |
| `ldap` | LDAP |
| `mysql`, `pgsql`, `msrpc`, `oracle`, ... | Service-specific |
| `comm` | Raw socket I/O (TCP/UDP) |
| `unpwdb` | Username/password databases |
| `brute` | Brute-force framework |
| `vulns` | Vulnerability reporting helper |
| `table` | Extended table utilities |
| `string` | Extended string utilities |
| `io`, `os`, `math`, `coroutine` | Standard Lua (sandboxed) |

### stdnse Essentials

```lua
local stdnse = require "stdnse"

-- Script arguments
local arg = stdnse.get_script_args("script.argname") or "default"

-- Formatted output (auto-handles verbosity)
stdnse.print_debug(1, "Debug: %s", var)
stdnse.print_verbose("Verbose output")

-- Return structured output for -oX/-oA
return { key = "value", list = {1,2,3} }

-- Host/port info
host.ip, host.name, host.os
port.number, port.protocol, port.service, port.version
```

### http Library Basics

```lua
local http = require "http"

-- Simple GET
local response = http.get(host, port, "/path")
if response.status == 200 then
  stdnse.print_debug(1, "Body: %s", response.body)
end

-- With options
local opts = {header={["User-Agent"]="Custom"}, timeout=5000}
local response = http.get(host, port, "/", opts)

-- POST
local response = http.post(host, port, "/login", {username="admin", password="admin"})

-- Pipeline (multiple requests)
local responses = http.pipeline(host, port, {"/a", "/b", "/c"})
```

### Vulnerability Reporting Helper

```lua
local vulns = require "vulns"

local vuln = {
  title = "CVE-XXXX-XXXX",
  state = vulns.STATE.EXPLOIT,  -- or VULN, LIKELY_VULN, NOT_VULN
  description = "Details...",
  references = {"https://cve.mitre.org/..."},
  dates = {disclosure = {year=2024, month=1, day=1}}
}
local report = vulns.Report:new(SCRIPT_NAME, host, port)
return report:make_output(vuln)
```

## Procedure

### 1. Create Script File

```bash
cat > mycheck.nse <<'EOF'
description = [[
Detects example service misconfiguration on port 8080.
]]
author = "You"
license = "Same as Nmap"
categories = {"discovery", "safe"}

portrule = shortport.port_or_service(8080, "http-proxy")

action = function(host, port)
  local http = require "http"
  local response = http.get(host, port, "/")
  if response.status == 200 and response.body:match("Example") then
    return "Example service detected"
  end
end
EOF
```

### 2. Test Locally

```bash
# Syntax + basic run
nmap --script ./mycheck.nse -p 8080 192.168.1.1

# With trace for debugging
nmap --script ./mycheck.nse -p 8080 192.168.1.1 --script-trace

# Dry-run args help
nmap --script ./mycheck.nse --script-args=help
```

### 3. Install for Permanent Use

```bash
mkdir -p ~/.nmap/scripts
cp mycheck.nse ~/.nmap/scripts/
nmap --script-updatedb

# Now runs like built-in
nmap --script mycheck 192.168.1.1
```

### 4. Advanced: Script Arguments

```lua
description = [[
Checks for custom header.
]]
categories = {"safe"}

portrule = shortport.http

action = function(host, port)
  local http = require "http"
  local header_name = stdnse.get_script_args("mycheck.header") or "X-Custom"
  local response = http.get(host, port, "/")
  if response.header and response.header[header_name] then
    return string.format("Header %s: %s", header_name, response.header[header_name])
  end
end
```

Usage: `nmap --script mycheck --script-args=mycheck.header=X-Forwarded-For`

### 5. Submit Upstream (Optional)

1. Test thoroughly on multiple targets
2. Follow Nmap style guide (2-space indent, docs at top)
3. Submit PR to `https://github.com/nmap/nmap/tree/master/scripts`
4. Include `nse_doc` entry if new library used

## Pitfalls

- **Sandboxing**: no `os.execute`, `io.popen`, `loadfile`, `dofile`, `require` outside allowed libs
- **Global state**: don't use globals across runs; use `nmap.registry` for persistence
- **Timeouts**: set `http`/`comm` timeouts; scripts killed after 10min default
- **Portrule precision**: overly broad rules waste scan time; prefer `shortport.port_or_service`
- **Categories**: `safe` = non-intrusive; `intrusive` = may crash/DoS; `vuln` = exploits
- **Output**: return `nil` for "no result" (script won't appear in output); return string or table for findings
- **Lua 5.1**: no `bit32`, no UTF-8 lib, limited `math` — check Nmap's embedded version
- **Threading**: NSE runs scripts concurrently; don't assume sequential execution

## Verification

```bash
# 1. Syntax check
nmap --script ./mycheck.nse --script-args=help 2>&1 | head -5
# Should show script help, not Lua errors

# 2. Run against test target
nmap --script ./mycheck.nse -p 80 127.0.0.1
# Should complete without "Script engine error"

# 3. Verify output format
nmap --script ./mycheck.nse -p 80 127.0.0.1 -oX - | grep -A5 "<script"
# Should show <script id="mycheck" output="..."> in XML
```