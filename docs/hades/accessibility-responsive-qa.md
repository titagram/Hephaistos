# Hades Accessibility And Responsive QA

This checklist is the production-readiness gate for user-facing Hades surfaces.
Run it before public launch changes to installer, desktop, dashboard, docs,
backend onboarding, settings, skills/plugins, cron, or messaging setup.

## Surfaces

| Surface | Primary paths | Minimum automated gate |
| --- | --- | --- |
| Bootstrap installer | `apps/bootstrap-installer/src/**` | `npm run --prefix apps/bootstrap-installer typecheck` |
| Desktop app | `apps/desktop/src/**` | `npm run --prefix apps/desktop typecheck` |
| Web dashboard | `web/src/**` | `npm run --prefix web typecheck` |
| Website docs | `website/docs/**` | `npm run --prefix website typecheck` and `npm run --prefix website build` |

## Critical Flows

Exercise these flows with keyboard and narrow viewport checks:

- Install and bootstrap.
- Chat and interrupt/redirect.
- Backend status, sync, pending jobs, memory proposals, and inbox actions.
- Settings and keys.
- Project linking and unlinking.
- Skills and plugins browse/install/enable flows.
- Cron create/edit/run flows.
- Messaging setup.
- Error recovery after failed bootstrap, backend unreachable, auth failure,
  expired token, and desktop/backend version mismatch.

## Keyboard

For each critical flow:

1. Start with pointer idle.
2. Navigate with Tab and Shift+Tab.
3. Activate primary actions with Enter or Space.
4. Close popovers, dialogs, menus, and overlays with Escape.
5. Confirm focus returns to the control that opened the modal or to the next
   logical action.
6. Confirm visible focus is present on every interactive control.
7. Confirm no keyboard trap exists in sidebars, terminal panes, scroll regions,
   or code/log viewers.

## Screen Reader Basics

Use the browser accessibility tree, platform screen reader, or Playwright
accessibility snapshot when available. Confirm:

- Icon-only buttons have accessible names.
- Form fields have labels or explicit accessible names.
- Status changes are visible in text and not color-only.
- Error text names the failed action and the recovery step.
- Loading states do not hide the only actionable control indefinitely.
- Tables/lists expose row labels that make sense out of visual context.

## Responsive Viewports

Check these widths at minimum:

- 360 px mobile
- 390 px mobile
- 768 px tablet
- 1024 px laptop
- 1440 px desktop
- 1920 px wide desktop

For every viewport:

- No primary text overlaps adjacent controls.
- Buttons do not truncate their only action word.
- Sidebars collapse or remain scrollable without covering the main task.
- Dialogs fit within the viewport and keep footer actions reachable.
- Logs, code, terminal panes, and tables scroll inside their region instead of
  forcing the full page sideways.
- Error states remain visible without relying on hover.

## Error Copy

Error states must be specific and safe:

- Name the action that failed.
- Include the next recovery command or UI action.
- Preserve local chat/terminal state when possible.
- Redact API keys, backend tokens, bootstrap tokens, local absolute paths,
  raw job payloads, raw source, and SQLite paths.
- Avoid success-colored treatment for degraded states.

## Manual Evidence Template

Record one note per release candidate:

```text
Date:
Build or commit:
Surface:
Flow:
Viewport:
Keyboard path:
Screen reader or accessibility tree:
Result:
Issues filed:
```

## Completion Gate

A P2 accessibility/responsive QA pass is complete when:

- The automated gates for touched surfaces pass.
- Every critical flow touched by the change has a recorded keyboard path.
- Narrow and desktop viewport checks have no overlap or unreachable actions.
- Errors are visible, specific, and redacted.
- Any skipped manual flow has an owner and a reason in the release notes.
