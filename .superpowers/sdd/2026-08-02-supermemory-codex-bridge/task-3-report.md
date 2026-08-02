# Task 3 report: OpenAI compatibility contract

## RED

Added contract-first tests for accepted text messages and text parts, role preservation, structured-output normalization, explicit unsupported modes, response shape and zero-token fallback, plus exact prompt construction and XML escaping.

Command:

```text
cd services/supermemory-codex-bridge && npx tsx --test tests/openai.test.ts tests/prompt.test.ts
```

Output: failed as intended with `ERR_MODULE_NOT_FOUND` for `src/openai.js` in both test files; `2` test-file failures. The parser and prompt builder did not exist.

## GREEN

Implemented `src/openai.ts` with the normalized request, invocation, result, response, and HTTP-facing error contracts. The parser accepts only the documented model alias, roles, string/text-part content, structured-output forms, and compatibility hints. It explicitly rejects models, streaming, tools, unsupported `n`, tool roles, non-text content, unknown response formats, and malformed JSON-schema envelopes without including raw message content in errors.

Implemented `src/prompt.ts` with the fixed Supermemory instruction wrapper, ordered role-bounded messages, and escaping for `&`, `<`, and `>`.

Focused GREEN command:

```text
cd services/supermemory-codex-bridge && npx tsx --test tests/openai.test.ts tests/prompt.test.ts
```

Output: `9` tests passed, `0` failed.

## Final verification

```text
cd services/supermemory-codex-bridge && npm test && npm run build
```

Output: `11` tests passed, `0` failed; TypeScript build exited `0`.

`git diff --check` produced no output.

## Self-review

- Confirmed output usage maps `inputTokens` to `prompt_tokens`, `outputTokens` to `completion_tokens`, and defaults every counter to zero only when usage is absent.
- Confirmed the public alias is retained in both normalized requests and returned completion responses.
- Confirmed the prompt boundary cannot be closed by message content because XML-sensitive characters are escaped.
- Confirmed compatibility hints are parsed only for validation and never carried to the Codex invocation.
- Confirmed `dist/` produced by verification was removed and is not part of the source/test commit.

## Concerns

None. The task brief intentionally leaves semantic JSON Schema validation to the downstream schema consumer; this task validates the required OpenAI `json_schema` envelope and preserves the schema object unchanged.
