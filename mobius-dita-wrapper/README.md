# DITA Service

The programmable-document substrate, implemented as a microservice in FastAPI + Postgres
+ Redis — same architecture as the Labor Gateway. It implements all 53 endpoints from
`dita.additions.json`: data/prompt templates (CRUD + versioning + render + validate),
prompt chains + chain runs, async document jobs, the document lifecycle state machine,
feedback, and first-class seed packets.

It sits *above* the existing synchronous render engine (`POST /dita`, untouched) and has
two downstream seams the Labor Gateway doesn't:
- it calls the **Labor Gateway** for `call_llm` chain steps and seed-packet `to-llm`;
- it calls **Bob ETL** for hydration (`HydrationBinding.source = etl_job`).

## Architecture

```
client ──HTTP──▶ api (FastAPI)  ── Postgres (templates, chains, runs, document jobs,
                  │                          workflow + versions, seed packets, feedback,
                  │                          artifacts, lineage, events, idempotency)
                  └── Redis (document-job queue · chain-run queue · per-job event streams)
                        ▲
              worker ───┘  run_document_job (render / decompose / project / hydrate /
                           generate) · run_chain (DAG, depth-guarded)
                        │
       ┌────────────────┼────────────────────────────┐
       ▼                ▼                              ▼
  existing render   Labor Gateway (call_llm)     Bob ETL (hydration)
  (POST /dita)      run-sync / run-async         trigger/ml · etl/job-info
```

## Bob / Camunda orchestration (coarse)

A document job or chain run is **one coarse step** in a Bob/Camunda workflow. The fast
inner work (render, chain steps, labor calls) stays in-process. Both invocation styles:
- **External-task pull:** `app/worker/camunda_worker.py` services `dita.document_job` and
  `dita.chain_run` topics (the `camunda-worker` compose service).
- **Service-task push:** `POST /camunda/document-jobs/run-async`.
- **Re-stitch:** `POST /camunda/workflows/publish` imports a workflow into Bob;
  `GET /camunda/document-jobs/{id}/reconcile` projects Bob process state onto JobState.

See `workflows/document_pipeline.bpmn` for a coarse sample.

## Run

```bash
docker compose up --build      # postgres(5433), redis(6380), migrate, api(8001), worker, camunda-worker
pip install -r requirements-dev.txt && pytest   # SQLite + fakeredis + stubbed downstreams
```

## Endpoint coverage

All 53 fragment endpoints have handlers (verified: 53 declared = 53 implemented). Three
additional `/camunda/*` endpoints provide the orchestration tier (not part of the
fragment, by design).

## Fully implemented vs. seams

**Implemented:** every endpoint; the document-job orchestrator (render / decompose /
project_to_schema / hydrate / generate); the prompt-chain DAG engine with the bounded
re-entry (depth) guard; the document workflow state machine with transition guards and
optimistic version locking; template + chain versioning with history; seed-packet
lifecycle; per-job SSE event streams; the two downstream seams (Labor Gateway, Bob ETL)
wired to real spec shapes; Alembic migration with append-only `execution_event` and
immutable-artifact triggers; the coarse Bob/Camunda tier (pull + push).

**Seams (documented):**
- `decompose_source` / `project_to_schema` produce structurally-correct artifacts but the
  actual content decomposition and PI schema synthesis are single-function stubs to fill
  against your component model and PI's schema vocabulary.
- Hydration polls Bob ETL once; a production loop adds backoff + completion wait.
- Render response parsing is defensive (json vs html/pdf); confirm against the engine.
- JWT verification needs your issuer/JWKS; `DITA_AUTH_DISABLED=true` is local/test only.
