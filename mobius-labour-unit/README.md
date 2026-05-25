# Labor Gateway

A typed **LLM-as-labor** execution service, implemented as a microservice in
FastAPI + Postgres + Redis. It sits *above* the existing inference gateway and calls
`/inference/chat` at the leaf; the existing routes are untouched.

It implements all 26 new endpoints from `llm-gateway.additions.json`: typed labor calls,
validation/repair/revalidate, batch labor, prompt-prefix caching, the full single-job
lifecycle (cancel/retry/fork/pause/resume/artifacts/lineage/ux-state), SSE events,
REST event append, executor registry, and executor + model policies.

## Architecture

```
client ──HTTP──▶ api (FastAPI)
                  │  persists job + emits JOB_SUBMITTED
                  ├──▶ Postgres (labor_job, labor_batch, artifact_ref, execution_event,
                  │             cost_record, idempotency_key, executor_policy, model_policy)
                  └──▶ Redis  (job queue · cache prefixes · per-job event streams)
                          ▲
                          │ dequeues job_id
                  worker ─┘  runs orchestrator: resolve prefixes → execute on a
                             registered executor → persist raw (immutable) → parse →
                             validate (typed errors) → bounded repair loop → persist
                             parsed → emit events throughout
                          │
                          └──HTTP──▶ existing /inference/chat  (the one reuse point)
```

- **api** and **worker** are separate processes (separate compose services / replicas).
- **SSE** streams events from per-job Redis Streams; **gRPC** (the proto in the change
  order) is the service-to-service channel carrying the same JobEvent shape.

## Bob / Camunda orchestration (coarse-grained)

The labor job is **one coarse step** inside a Bob/Camunda workflow. The fast inner loop
(execute → parse → validate → repair) stays in-process for performance; Camunda only sees
`submit → terminal result` and orchestrates *across* services (labor → DITA → PI → human
approval). Both invocation styles are supported:

- **External-task (pull):** `app/worker/camunda_worker.py` long-polls Bob's Camunda
  gateway for the `labor.execute` topic, runs the job via the in-process orchestrator, and
  completes the task with `{succeeded, status, parsed_output_ref, ...}` so the BPMN can
  branch. Runs as the `camunda-worker` compose service.
- **Service-task (push):** `POST /camunda/labor/run-sync` (wait for terminal) or
  `/camunda/labor/run-async` (return job_id, Redis worker drains, Camunda polls).

**Fine-grained, opt-in:** for workflows that need a human between validate and repair, the
worker also services `labor.validate` and `labor.repair` topics. A coarse workflow never
emits these, so there is zero per-stage overhead on the hot path. See
`workflows/labor_pipeline.bpmn` for a sample: coarse `labor.execute`, then an *optional*
human-review + `labor.repair` branch only on failure.

**State ownership:** Bob/Camunda's process instance is the source of truth for the outer
**workflow**; the gateway's `labor_job.status` is the source of truth for the inner labor
**run**. `GET /camunda/labor/jobs/{id}/reconcile?pipeline_id=...` surfaces both and projects
Bob's `ProcessDefinition` state onto our JobState vocabulary.

**Re-stitching:** `POST /camunda/workflows/publish` imports/creates a `WorkflowPostDto` in
Bob (`/v1.0/wf/import` or `/v1.0/wf`), so the labor pipeline is defined in the engine
rather than hardcoded. The Bob client (`app/services/bob_camunda.py`) is wired to the real
`api-docs_bobservice` shapes: `pipeline/trigger/llm`, `pipeline/status`, `wf/status`, `wf`.



```bash
docker compose up --build          # postgres, redis, migrate, api (x1), worker (x2)
# api on http://localhost:8000  ·  OpenAPI at /docs
```

## Run tests (no Postgres/Redis needed)

```bash
pip install -r requirements-dev.txt
pytest            # SQLite in-memory + fakeredis + a stub executor
```

Tests cover the headline acceptance criteria: submit→succeed, idempotency replay returns
the original job, cross-tenant read is 403, validation→repair→succeed, repair-budget
exhaustion lands in terminal `repair_required` (no infinite loop), batch echoes
`custom_id` verbatim, and cache-prefix round-trip.

## What is fully implemented vs. left as a seam

**Fully implemented:** all 26 endpoints with handlers; the orchestration pipeline;
real JSON-Schema validation with the typed ValidationError contract; the bounded repair
loop; idempotency (replay returns the original); per-tenant isolation on every read;
async job queue + worker; per-job SSE event streams with replay; cost/usage recording
with the cache-token split; Alembic migration with **append-only** trigger on
`execution_event` and an **immutability** trigger on raw artifacts; Dockerfiles and
compose; bearer/JWT dependency on every route.

**Honest seams (documented, not hidden):**
- **Auth verification** uses PyJWT against a JWKS URL; wire your real issuer/audience.
  `GATEWAY_AUTH_DISABLED=true` is for local/tests only.
- **Output-schema-by-ArtifactRef**: when `output_schema` / `validation_schema_ref` is an
  ArtifactRef, the validator dereferences it from the local artifact store. Cross-service
  schema fetch (from DITA/PI) is a one-function adapter in `validation`/`calls`.
- **Budget reservation** records cost; the pre-flight reserve-then-run gate reads
  `ExecutorPolicy.budget` but the hard-stop ledger is a TODO marked in `orchestrator`.
- **Executor registry** seeds a single `default` inference executor; register real
  model-backed executors at startup.
- The downstream `/inference/chat` response shape is parsed defensively (OpenAI-style and
  content-block style); confirm against the real gateway’s schema.

These are integration points, not missing logic — each is a named function with a clear
contract, so the service runs end-to-end today against a stub executor and slots into the
real mesh by filling those adapters.
```
