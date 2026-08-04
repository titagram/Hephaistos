---
name: google-drive-file-transfer
description: Download files/folders from Google Drive (gdown, no auth for shared links) and upload files to Google Drive (rclone + OAuth) from a headless Linux box. Includes the remote OAuth authorization dance for when the user is not at the machine (e.g. Telegram). Use whenever the user shares a drive.google.com file/folder link and wants something downloaded, uploaded, converted, or delivered there.
---

# Google Drive file transfer (download + upload, headless)

## When to use
- User shares a `drive.google.com/file/...` or `drive.google.com/drive/folders/...` link and asks you to fetch its content.
- User asks to put a produced artifact (converted file, report, export) into a specific Drive folder.
- Folder ID = the part after `/folders/` in the URL; file ID = the part after `/file/d/` (or the `id=` query param).

## Download — gdown (no auth needed for shared links)
- Folder: `uvx gdown --folder "https://drive.google.com/drive/folders/<FOLDER_ID>"` — recursive, keeps structure, works with "anyone with link" sharing.
- Single file: `uvx gdown "https://drive.google.com/uc?id=<FILE_ID>"` (or pass the share link directly).
- `uvx` runs gdown without polluting the environment (PEP 668 safe; no pip install needed).
- Verify with `ls -la` after — confirm the file exists and size is plausible.

## Upload — rclone (always needs OAuth, even for "anyone can edit" folders)
- `gdown` does NOT upload. Drive API uploads require an authenticated account — a folder shared read/write "for anyone" still needs OAuth to upload into it.
- Install rclone: `sudo apt-get install -y rclone` gives OLD 1.60.x → immediately upgrade with `curl https://rclone.org/install.sh | sudo bash` (1.75+ required for current Google OAuth).
- Upload after auth: `rclone copy <local-file> gdrive:<FOLDER_ID>` (folder ID works directly as the remote path). Verify: `rclone lsl gdrive:<FOLDER_ID>`.

## Headless OAuth flow (user is remote, machine has no browser)
The only part needing the user. Run the flow, hand them ONE Google URL, they paste back ONE URL. **Never send `127.0.0.1` links to the user — those resolve to THEIR device and fail.**

1. Start a local auth server (background + PTY): `rclone authorize "drive" --auth-no-open-browser`
   → prints: `Please go to the following link: http://127.0.0.1:53682/auth?state=<STATE>`
2. Capture the REAL Google consent URL (the local server 302-redirects to Google):
   `curl -s -D - -o /dev/null "http://127.0.0.1:53682/auth?state=<STATE>" | grep -i '^location:'`
   → gives `https://accounts.google.com/o/oauth2/auth?...&scope=...drive&state=<STATE>`
3. Send that accounts.google.com URL to the user with these instructions:
   - Log in with the Google account that owns/has access to the Drive folder.
   - If "Google hasn't verified this app" → **Advanced → "Go to rclone"** (normal for open-source apps).
   - After authorizing, Google redirects to `http://127.0.0.1:53682/?code=...` which FAILS to load on their device — **expected**. Copy the ENTIRE URL from the address bar (contains `code=...&state=...`) and paste it back.
4. User pastes the redirect URL → complete the exchange against the local server:
   `curl "http://127.0.0.1:53682/?<paste-the-query-string>"`
   → rclone's server validates state, exchanges the code, prints the token JSON.
5. Persist the remote: `rclone config create gdrive drive token='<token-json>' scope=drive`
6. Upload: `rclone copy <local-file> gdrive:<FOLDER_ID>`; verify `rclone lsl gdrive:<FOLDER_ID>`.

## Pitfalls
- **Interactive `rclone config` menu**: typing "q" at a `name> ` prompt creates a remote literally named "q" — "q" is only the quit command at the top-level menu. Delete stubs via the menu's `d` option.
- **Non-interactive `rclone config create ... config_is_local=false` silently writes a stub** with no token and exits (no OAuth prompt) — useless for auth; use `rclone authorize` (above) or drive the interactive menu over PTY.
- **Shared client_id retirement**: rclone's shared Google client_id (`202264815644.apps.googleusercontent.com`) is being retired during 2026; the consent screen still works for now, but the user may eventually need their own (rclone docs: "making your own client id").
- The code in the pasted URL is a one-time credential — handle it in-chat only; don't echo it into logs or files.
- If the `state` no longer matches the running server (server died/timed out), restart `rclone authorize` and recapture the Location URL.

## Verification checklist
- [ ] Download: file exists with plausible size (`ls -la`)
- [ ] Upload: `rclone lsl gdrive:<FOLDER_ID>` shows the file with correct size
- [ ] If converting before upload, the conversion skill (pdf-to-epub) must pass its own checks first
