---
sidebar_position: 21
title: "Accessibility And Responsive QA"
description: "Manual and automated QA checklist for Hades installer, desktop, dashboard, and docs surfaces."
---

# Accessibility And Responsive QA

Use this checklist before shipping changes to the installer, desktop app, web
dashboard, website docs, backend onboarding, settings, skills/plugins, cron, or
messaging setup.

## Automated Gates

| Surface | Command |
| --- | --- |
| Bootstrap installer | `npm run --prefix apps/bootstrap-installer typecheck` |
| Desktop app | `npm run --prefix apps/desktop typecheck` |
| Web dashboard | `npm run --prefix web typecheck` |
| Website docs | `npm run --prefix website typecheck` and `npm run --prefix website build` |

## Critical Flows

- Install and bootstrap.
- Chat, interrupt, and redirect.
- Backend status, sync, pending jobs, memory proposals, and inbox actions.
- Settings and keys.
- Project linking and unlinking.
- Skills and plugins browse/install/enable flows.
- Cron create/edit/run flows.
- Messaging setup.
- Error recovery after failed bootstrap, backend unreachable, auth failure,
  expired token, and desktop/backend version mismatch.

## Keyboard Checks

For each touched flow, navigate with Tab and Shift+Tab, activate actions with
Enter or Space, close overlays with Escape, and confirm focus returns to the
opening control or next logical action. Every interactive control needs visible
focus. No sidebar, terminal pane, code viewer, menu, or modal can trap focus.

## Screen Reader Basics

Confirm icon-only controls have accessible names, form fields have labels,
status changes appear in text, and error text names both the failed action and
the recovery step. Loading states must not hide the only actionable control
indefinitely.

## Responsive Checks

Check at least 360, 390, 768, 1024, 1440, and 1920 px widths. Primary text must
not overlap controls. Buttons must keep their action understandable. Dialog
footer actions must remain reachable. Logs, terminals, code blocks, and tables
should scroll inside their own region instead of forcing the full page sideways.

## Error Copy

Errors must be visible, specific, and redacted. Do not show API keys, backend
tokens, bootstrap tokens, local absolute paths, raw job payloads, raw source, or
SQLite paths. Degraded states should not look like success.

## Evidence

Record the build or commit, surface, flow, viewport, keyboard path, accessibility
tree or screen-reader check, result, and issues filed. If a manual flow is
skipped, release notes must name the owner and reason.
