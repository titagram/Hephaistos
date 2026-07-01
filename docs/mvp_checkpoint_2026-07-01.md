# MVP Checkpoint - 2026-07-01

Checkpoint operativo sullo stato Hades MVP dopo il completamento delle slice
locali e del backend Laravel remoto M1-M4.

## Stato sintetico

Il nucleo MVP tecnico e' funzionante end-to-end:

- client locale Hades con setup backend, project link/unlink, sync manuale,
  memory provider `hades_backend`, dispatcher job read-only e diagnostica base;
- backend Laravel remoto con health, token verify, agent registration,
  capabilities, workspace binding, shared memory snapshot/proposals e agent
  jobs status/result;
- smoke end-to-end locale -> `https://home-sweet-home.cloud` gia' riuscito con
  `hades backend setup`, `hades project link`, `hades backend sync`, memory
  proposal accettata e job `read_files` completato.

## Verifiche fresche

- Locale: `scripts/run_tests.sh tests/hermes_cli/test_hades_backend_cmd.py
  tests/hermes_cli/test_hades_backend_db.py
  tests/hermes_cli/test_hades_backend_jobs.py
  tests/tui_gateway/test_hades_backend_rpc.py
  tests/agent/test_hades_backend_memory_provider.py` -> 15 test passati.
- Locale docs: `python3 scripts/docs_audit.py` -> 18 documenti richiesti
  controllati, audit passato.
- Remoto Laravel:
  `APP_ENV=testing DB_CONNECTION=sqlite DB_DATABASE=:memory: DB_URL= php artisan test tests/Feature/Hades tests/Feature/PluginAuthTest.php`
  -> 23 test passati, 189 assertion.
- Remoto Laravel: `php artisan route:list --path=hades` -> 11 route Hades.
- Public smoke: `GET https://home-sweet-home.cloud/api/hades/v1/health` ->
  HTTP 200, `status=ok`.
- Worktree locale e remoto puliti sui rispettivi branch.

## Manca per chiudere MVP

### P0 - blocca MVP consegnabile

1. Provisioning bootstrap token e install command dalla dashboard Laravel.
   Oggi il contratto runtime funziona, ma serve una UI/comando admin reale per
   generare token e one-liner project-scoped senza insert manuali o seed ad hoc.
2. Installer/onboarding quasi plug-and-play.
   Servono `install.sh` e, se incluso nella prima consegna, `install.ps1`, con
   backend URL, project id e token incorporabili nel comando generato dalla
   dashboard.
3. Documentazione utente/developer organica.
   Serve una doc stabile distinta da `docs/CODEX_AGENTS.md`: installazione,
   setup backend, project linking, shared memory, sync job, doctor e
   troubleshooting.
4. Contract API minimo stabile.
   Serve almeno una OpenAPI/contract fixture o documento equivalente per le
   route Hades M1-M4 gia' operative, inclusi error codes, capability policy,
   payload limits e redaction/truncation expectations.
5. UX minima per stati sospesi/degradati.
   `hades backend sync` funziona, ma per MVP vanno resi leggibili/azionabili:
   job `waiting_confirmation`, proposal refused/conflicted, backend degraded e
   ultimo errore sync.

### P1 - obbligatorio per chiudere MVP completo

1. Integrare sync/pull nel ciclo agent o in un trigger periodico leggero.
   Oggi il flusso principale e' manuale via `hades backend sync`.
2. Retry/backoff, deadline e retention completi per job/proposals.
   La base locale/remota esiste, ma manca la policy completa e il cleanup.
3. `hades doctor` piu' completo.
   Mancano cleanup cache/job, stato Persephone reale e reporting doctor verso
   Laravel.
4. Admin UI backend per creare job/manual review.
   Gli endpoint job esistono; serve una superficie Laravel per usarli senza
   operazioni DB/manuali.
5. Chiusura schema M5 `sync_git_tree` / `populate_backend_ast`.
   Il client locale produce artifact bounded, ma il backend non ha ancora
   artifact upload/schema AST definitivo.
6. Persephone/inbox realtime completa.
   Il piano la considera strategica per la comunicazione tra istanze Hades e
   deve rientrare nel percorso obbligatorio MVP, anche se i job backend restano
   serviti dal canale pull/piggyback.
7. Skill/model-routing/subagent locali curati.
   Servono profili e skill operative per coordinare subagent locali e routing
   modello senza esporre queste scelte al backend.

### P2 - post-MVP naturale

1. Distribuzione PyPI/npm definitiva.
2. Policy privacy completa per promuovere memorie personali locali a shared
   memory backend.
3. Audit complessivo skill/plugin/default distribution e strategia rebase.

## Prossimo step consigliato

Chiudere prima il P0.1 + P0.2 lato backend/dashboard: generazione bootstrap
token, comando one-liner project-scoped e script install. Subito dopo chiudere
il resto del P0 e poi tutto il P1, che e' obbligatorio per dichiarare completo
l'MVP. Le domande o decisioni lato Laravel possono essere coordinate con
l'agente backend remoto via:

```bash
ssh -tt ubuntu@162.19.229.31 'cd /home/ubuntu/dev-sandbox && codex resume --last --yolo'
```
