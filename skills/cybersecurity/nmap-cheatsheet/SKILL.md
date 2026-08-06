---
name: nmap-cheatsheet
description: Quick-reference skill for Nmap network scanning commands and techniques from the jasonniebauer cheatsheet.
version: 0.1.0
author: Hermes
platforms: [linux, macos]
metadata:
  hermes:
    tags:
      - Nmap
      - Network-Scanning
      - Recon
      - Pentest
---

# Nmap Cheat Sheet Reference

A practical quick-reference for Nmap (Network Mapper) scanning commands organized by task. Based on the jasonniebauer/Nmap-Cheatsheet repository. Use this when you need to recall exact Nmap syntax for common scanning scenarios during recon or CTF engagements.

## When to Use

- Need to scan a target but forgot the exact flag for a specific scan type
- Performing network reconnaissance and want a catalog of scan techniques
- CTF challenge requires specific port scanning or service enumeration
- Building a scan command and want to verify options before running

## Prerequisites

- `nmap` installed (`sudo apt install nmap` / `brew install nmap`)
- Target authorization — only scan networks you own or have explicit permission to test
- Basic familiarity with IP addressing and CIDR notation

## How to Run

Invoke commands directly through the `terminal` tool. For long-running scans, use `terminal(background=true, notify_on_complete=true)`.

```bash
# Quick syntax check
nmap -h

# Version check
nmap -V
```

## Quick Reference

### Scan Types (determined by -s flag)

| Switch | Scan Type |
|--------|-----------|
| `-sS` | TCP SYN (default, stealthy, requires root) |
| `-sT` | TCP Connect (no root needed) |
| `-sU` | UDP scan |
| `-sA` | ACK scan (firewall mapping) |
| `-sF` | FIN scan |
| `-sN` | NULL scan |
| `-sX` | XMAS scan |
| `-sO` | IP Protocol scan |
| `-sL` | List/DNS scan (no port scan) |
| `-sP` / `-sn` | Ping scan (host discovery only) |
| `-sV` | Service version detection |
| `-sC` | Default NSE scripts |
| `-sR` | RPC scan |
| `-sI` | Idle/Zombie scan |

### Host Discovery

| Switch | Description |
|--------|-------------|
| `-Pn` | Skip host discovery (treat all as up) |
| `-PS<ports>` | TCP SYN ping |
| `-PA<ports>` | TCP ACK ping |
| `-PU<ports>` | UDP ping |
| `-PY<port>` | SCTP INIT ping |
| `-PE` | ICMP echo ping |
| `-PP` | ICMP timestamp ping |
| `-PM` | ICMP address mask ping |
| `-PO` | IP protocol ping |
| `-PR` | ARP ping (local LAN) |
| `-n` | Disable reverse DNS |
| `-R` | Force reverse DNS |
| `--dns-servers <servers>` | Specify DNS servers |

### Port Specification

| Switch | Description |
|--------|-------------|
| `-p <ports>` | Specific ports (e.g., `-p 80,443,8080`) |
| `-p-` | All ports 1-65535 |
| `-F` | Fast scan (top 100 ports) |
| `--top-ports <n>` | Scan top n most common ports |
| `-r` | Sequential port scan (no randomization) |
| `-sU -sT -p U:<ports>,T:<ports>` | Scan UDP and TCP ports by protocol |

### Timing Templates

| Template | Speed | Use Case |
|----------|-------|----------|
| `-T0` | Paranoid | IDS evasion, very slow |
| `-T1` | Sneaky | IDS evasion |
| `-T2` | Polite | Slow, less bandwidth |
| `-T3` | Normal | Default |
| `-T4` | Aggressive | Fast, reliable networks |
| `-T5` | Insane | Very fast, may miss results |

### Output Formats

| Switch | Format |
|--------|--------|
| `-oN <file>` | Normal text |
| `-oX <file>` | XML |
| `-oG <file>` | Grepable |
| `-oA <basename>` | All three (normal, XML, grepable) |
| `-oS <file>` | Script kiddie/1337 |
| `--stats-every <time>` | Periodic stats (e.g., `10s`) |

### Common Combinations

```bash
# Aggressive scan (OS, version, scripts, traceroute)
nmap -A <target>

# Full port scan with service detection
nmap -sS -sV -p- <target>

# Fast scan top 1000 ports
nmap -sS -sV --top-ports 1000 <target>

# UDP scan (slow, combine with top ports)
nmap -sU --top-ports 100 <target>

# Vulnerability scan with NSE
nmap -sV --script vuln <target>

# Firewall evasion
nmap -f -D RND:10 <target>       # Fragment + decoys
nmap --source-port 53 <target>   # Source port 53 (DNS)
```

## Procedure

### 1. Basic Target Specification

```bash
# Single target
nmap 192.168.1.1
nmap example.com

# Multiple targets
nmap 192.168.1.1 192.168.1.2 192.168.1.3

# CIDR subnet
nmap 192.168.1.0/24

# IP range
nmap 192.168.1.1-100

# From file
nmap -iL targets.txt

# Random hosts (internet-wide)
nmap -iR 100

# Exclude hosts
nmap 192.168.1.0/24 --exclude 192.168.1.1,192.168.1.254
nmap 192.168.1.0/24 --excludefile exclude.txt
```

### 2. Host Discovery (Ping Sweeps)

```bash
# Ping scan only (no port scan)
nmap -sn 192.168.1.0/24

# Skip ping (assume all up) — useful when ICMP blocked
nmap -Pn 192.168.1.1

# ARP scan (local network, fastest)
nmap -PR 192.168.1.0/24

# TCP SYN ping on common ports
nmap -PS 192.168.1.1
nmap -PS 22,80,443 192.168.1.1
```

### 3. Port Scanning

```bash
# Default SYN scan (requires root)
nmap -sS 192.168.1.1

# TCP Connect (no root)
nmap -sT 192.168.1.1

# UDP scan
nmap -sU 192.168.1.1

# Specific ports
nmap -p 80,443,8080 192.168.1.1
nmap -p 1-1000 192.168.1.1
nmap -p- 192.168.1.1               # All 65535 ports

# Top ports
nmap --top-ports 100 192.168.1.1
nmap -F 192.168.1.1                # Top 100 (alias)

# Service version detection
nmap -sV 192.168.1.1
nmap -sV --version-intensity 5 192.168.1.1  # More aggressive

# OS detection
nmap -O 192.168.1.1
nmap -O --osscan-guess 192.168.1.1

# Aggressive (OS + version + scripts + traceroute)
nmap -A 192.168.1.1
```

### 4. Firewall/IDS Evasion

```bash
# Fragment packets
nmap -f 192.168.1.1

# Specify MTU
nmap --mtu 24 192.168.1.1

# Decoy scan
nmap -D RND:10 192.168.1.1
nmap -D 192.168.1.100,192.168.1.101,ME 192.168.1.1

# Idle/Zombie scan (requires suitable zombie host)
nmap -sI 192.168.1.100 192.168.1.1

# Source port spoofing
nmap --source-port 53 192.168.1.1
nmap --source-port 80 192.168.1.1

# Randomize host order
nmap --randomize-hosts 192.168.1.0/24

# Spoof MAC
nmap --spoof-mac 0 192.168.1.1       # Random
nmap --spoof-mac Apple 192.168.1.1   # Vendor
nmap --spoof-mac 00:11:22:33:44:55 192.168.1.1

# Bad checksums
nmap --badsum 192.168.1.1
```

### 5. Nmap Scripting Engine (NSE)

```bash
# Default scripts
nmap -sC 192.168.1.1

# Specific script
nmap --script http-title 192.168.1.1

# Multiple scripts
nmap --script http-title,ssl-cert 192.168.1.1

# By category
nmap --script vuln 192.168.1.1
nmap --script auth 192.168.1.1
nmap --script default,safe 192.168.1.1

# Script tracing (debug)
nmap --script http-title --script-trace 192.168.1.1

# Update script database
nmap --script-updatedb
```

### 6. Output and Comparison

```bash
# Save all formats
nmap -oA scan_results 192.168.1.1

# Compare two scans
ndiff scan1.xml scan2.xml
ndiff -v scan1.xml scan2.xml
ndiff --xml scan1.xml scan2.xml > diff.xml
```

### 7. Debugging and Troubleshooting

```bash
# Verbose
nmap -v 192.168.1.1
nmap -vv 192.168.1.1

# Debug
nmap -d 192.168.1.1

# Show port state reasons
nmap --reason 192.168.1.1

# Only open ports
nmap --open 192.168.1.1

# Packet trace
nmap --packet-trace 192.168.1.1

# Interface list
nmap --iflist

# Specify interface
nmap -e eth0 192.168.1.1
```

## Pitfalls

- **Root required**: `-sS`, `-O`, `-sU` need root. Use `-sT` or run with `sudo`.
- **UDP scans are slow**: default scans top 1000 TCP but only top ports for UDP; use `--top-ports` to limit.
- **`-Pn` can waste time**: scanning hosts that are down. Use host discovery first on large ranges.
- **Firewall evasion may not work**: modern IDS/IPS detect fragmentation, decoys, etc.
- **Aggressive timing (`-T4/-T5`)** can miss open ports on unstable networks.
- **NSE scripts can be intrusive**: some scripts (e.g., `vuln` category) may crash services. Test in lab first.
- **IPv6**: use `-6` flag. Not all features support IPv6 equally.
- **Large output**: always save with `-oA` or redirect; terminal buffer may truncate.

## Verification

```bash
# Confirm nmap works
nmap -V
# Should output version info, e.g., "Nmap version 7.94 ( https://nmap.org )"

# Quick sanity scan on localhost
nmap -sn 127.0.0.1
# Should show 127.0.0.1 as up with 0 ports scanned
```