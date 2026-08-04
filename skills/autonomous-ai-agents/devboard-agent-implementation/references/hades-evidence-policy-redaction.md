# HadesEvidencePolicy Redaction Reference

`HadesEvidencePolicy` lives at `backend/app/Services/Hades/HadesEvidencePolicy.php`.
It provides two public redaction methods and a set of secret-detection patterns.

## Public Methods

### `redactTextMaterial(string $text): array{text: string, redactions: int}`

Use this to redact plain text before sending it to an LLM prompt. Returns the
redacted text and a count of substitutions made.

```php
$redacted = $this->evidencePolicy->redactTextMaterial($rawText);
$safeForLlm = $redacted['text'];
```

**Usage rule**: the redacted text goes to the LLM. Keep the original `$rawText`
for local fallback logic (keyword detection, deterministic normalization).

### `redactBugEvidenceMaterial(string $summary, array $payload): array`

Use this to redact structured bug evidence payloads. Returns `{summary, payload,
redactions}`. Redacts both the summary string and all string values in the
payload array recursively.

## Patterns Redacted

All patterns use case-insensitive matching and pre-existing `[redacted]` markers
are left intact (no double-redaction).

| Pattern | Matches | Replaced With |
|---|---|---|
| `Bearer <token>` | `Bearer ` followed by 12+ base64/URL-safe chars (unless already `***`, `redacted`, or `[redacted]`) | `Bearer [redacted]` |
| `api_key: <value>` and similar | `api_key`, `access_token`, `auth_token`, `authorization`, `cookie`, `password`, `private_key`, `secret`, `token` followed by `:` or `=` and a value of 8+ chars | `$1[redacted]` |
| Stripe-style keys | `sk-live-...` and `pk-live-...`, `sk-test-...`, `pk-test-...` with 8+ alphanumeric/underscore/dash chars | `[redacted-token]` |
| PEM private keys | `-----BEGIN * PRIVATE KEY-----` through `-----END * PRIVATE KEY-----` | `[redacted-private-key]` |

## Validation (reject, not redact)

### `validateBugEvidence(string $summary, array $payload): ?array`

Returns an error array if unredacted secrets are detected. Returns `null` if
the payload is clean.

### `rejectUnredactedSecret(array $values): ?array`

Shared by all `validate*` methods. Checks the same pattern list for any
unredacted secret appearance and rejects the payload.

## Example Flow

```
Raw user text:
  "The Stripe key sk-live-test-AbCdEfGhIjKlMnOpQrStUvWxYz leaked.
   Error: secret: xyz-token-abcdefghijklmnop"

After redactTextMaterial():
  "The Stripe key [redacted-token] leaked.
   Error: [redacted]"

⬇  sent to LLM prompt
   (original kept for fallback keyword detection)
```

## When to Use

- **Always** before sending user-provided text to an LLM prompt, whether via
  the Laravel AI SDK or any other provider.
- **Never** apply redaction to the raw text used for deterministic fallback —
  the fallback never leaves the backend, so it does not leak secrets.
