# Branching and deployment

## Permanent branches

DeskMate uses three long-lived branches:

```text
temporary feature or hotfix
           |
           v
          dev
           |
           v
        staging
           |
           v
          main
           |
           v
      release tag
```

- `dev` is the active integration branch. Feature branches target `dev` and require focused tests and review.
- `staging` is the preproduction candidate. Only pull requests from `dev` should update it.
- `main` is the sole production branch. Only a validated `staging` commit should be promoted to it.

Do not force-push any permanent branch. Shared branches should be merged forward without rebasing. An emergency production hotfix must be propagated back to `staging` and `dev` immediately.

## Required checks

Feature-to-`dev` changes require relevant backend tests, frontend tests/build when applicable, fatal Ruff checks and review of the changed files. `dev`-to-`staging` runs isolated persistence, provider-selection, MCP, LangGraph, browser and production-build checks. `staging`-to-`main` requires production-environment approval plus release validation.

Ordinary CI never receives production secrets and never connects to live Aura, production ChromaDB, Azure Document Intelligence, Groq, Azure OpenAI or user data. Full Ruff, mypy and ESLint enforcement is not yet a required check because the 2026-10-23 baseline contains 624 Ruff findings, legacy missing Python type annotations and 27 existing ESLint findings. CI enforces syntax/fatal Ruff rules and frontend type checking; the broader lint/type cleanup must be completed without hiding the existing debt.

## Environment configuration

Configuration stays outside Git. Keep `.env`, SQLite databases, Chroma collections, `store/local/`, `store/backups/`, originals, extraction artifacts, credentials and checkpoints private. Development and staging must use distinct temporary or explicitly provisioned stores. Never copy production data into another environment automatically.

The GitHub workflows reference `staging` and `production` Environments, but the repository has no configured hosting provider, deployment server, domain or production URL. The workflows validate candidates and deliberately stop before deployment. Connect an authorized hosting target and environment-scoped secrets before adding deployment commands.

## GitHub settings

Repository administrators should keep `main` as the default branch and configure:

- `dev`: pull request and CI required; force pushes disabled.
- `staging`: pull request, CI and staging validation required; force pushes disabled.
- `main`: pull request, CI, production approval and release validation required; force pushes and deletion disabled.
- `production` Environment: required reviewers and restricted deployment branch `main`.
- `staging` Environment: restricted deployment branch `staging` and reviewers appropriate to the team.

These controls must be verified in GitHub Settings; workflow files alone cannot prove that repository protection or reviewer rules are active.

## Release and rollback

After staging approval, promote the exact staging commit to `main`, rerun release validation, and create an immutable semantic tag such as `v0.1.0`. Record the previous `main` SHA as the rollback point. Roll back application code with a new revert commit or redeploy a previously verified release tag; do not reset shared history. Database and document rollback is a separate, operator-authorized recovery procedure and must preserve originals and backups.

## 2026-10-23 branch reconciliation

Original local branches: 8. Original remote branches: 10, excluding the `origin/HEAD` pointer. A second detached worktree was preserved at `855c222`; its untracked `.venv` and `frontend/node_modules` were not modified.

Annotated tags under `archive/pre-consolidation-20261023/` preserve every original branch/worktree tip. Repository visibility and administrator access were verified before publishing these recovery refs; they expose no commit that was not already present in the public repository.

| Original branch | Original tip | Findings and disposition |
|---|---|---|
| `main` | `e02eb7e` | Verified production baseline; retained until staged promotion. |
| `dev` / `prod` | `503707f` | No unique commits; archived. `dev` was fast-forwarded, while `prod` is retired after promotion. |
| `feat/persistent-semantic-memory-20261022` | `9c845f0` | One unique validated commit; integrated into `dev`. Live Aura write/read remains unclaimed. |
| `feat/general-intelligence-evaluation-20261021` | `e02eb7e` | Identical to `main`; general-intelligence tools and evaluation are already integrated. |
| `feat/azure-di-graph-every-upload-20261021` | `703c87a` | Ancestor of `main`; Azure DI, local Chroma and reference-only graph behavior are retained. |
| `fix/agent-activity-and-empty-plan-20261021` | `c2b7e60` | Ancestor of `main`; real activity, empty-plan rejection and bounded recovery are retained. |
| `fix/knowledge-clarification-routing-20261019` | `d863e3e` | Three commits match stable patch IDs in `main` (`88ec80c`, `3df3d72`, `7fb2e62`); no port needed. |
| `fix/news-candidate-selection-20261020` | `39d5114` | Diverged patches; current code contains later candidate selection, fetched-page evidence and activity handling. Covered by web/activity regressions; old implementation is superseded. |
| `fix/news-evidence-hydration-20261020` | `67835fa` | Diverged patches; current bounded page hydration, source validation and fetch budgeting supersede it. Covered by web regressions. |
| `fix/source-aware-investigations-20261020` | `d5d2539` | Diverged patches; current source-scope enforcement, inventory retrieval, verifier recovery and non-decorative activity supersede it. Covered by scope/activity regressions. |
| `integration/azure-di-graph-every-upload` | `6fe228e` | Ancestor of `main`; retained only as an archived recovery point. |
| `integration/recovery-production-readiness` | `d5f9ffe` | Ancestor of `main`; retained only as an archived recovery point. |

No conflict was resolved by selecting an entire `ours` or `theirs` file. The semantic-memory commit applied as a fast-forward descendant of the current production baseline. Historical diverged branches were evaluated by merge base, changed files, stable patch IDs, current implementation inspection and focused regression coverage.

Intentionally excluded from this consolidation: live paid-model evaluation, Azure Document Intelligence calls, live Aura writes, production data migration, hosting-provider configuration and changes to Voice behavior.

