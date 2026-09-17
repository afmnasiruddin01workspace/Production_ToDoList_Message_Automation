# Upgrade, Hotfix & Rollback Guide

Every change to a module that is already **stable** follows this process. No exceptions.

## Versioning
- Version is `MAJOR.MINOR`, stored in the plan frontmatter.
  - **MINOR** — internal change; §4 Outputs contract unchanged (e.g. better parsing, wording).
  - **MAJOR** — §4 Outputs contract changes (field added/removed/renamed, file path changed).
- Every stable publish is tagged `NN-<module>/vX.Y` (e.g. `01-calendar-todo/v1.0`).
- `plan/stableMD/NN-<module>.stable.md` always holds the current version; older versions live in git tags.

## Upgrade flow
1. **Open the change**
   - Copy `plan/stableMD/NN-<module>.stable.md` → `plan/draftMD/NN-<module>.draft.md`.
   - Set `phase: draft`, bump `version`, add a §11 Change Log row with the reason.
2. **Impact check**
   - Did §4 Outputs change? → MAJOR.
   - List every module whose `input_from` points to this module. Each must also go draft → test → stable, in chain order, **before anything is pushed**.
3. **Gates** — validate (§8) → user approves → test (§9) → user approves.
   - Tests must include **regression**: all previous T# cases still pass, plus new T# for the change.
4. **Publish**
   - Replace stableMD and `.claude/skills/NN-<module>/SKILL.md`.
   - Add a line to `CHANGELOG.md`.
   - Commit (`feat(NN-module): vX.Y — summary` or `fix(NN-module): vX.Y — summary`).
   - `git tag NN-<module>/vX.Y`, then push commit + tag **after user approval**.

## Hotfix (stable skill broken in real use)
1. Contain first — for module 02 set `WA_MODE=dry-run` so nothing more is sent.
2. Write an incident file in `docs/incidents/` (see template below).
3. Minimal draft: only the fix, MINOR bump.
4. testMD: the failing case as a new T# + regression cases.
5. User approves → publish as above. The approval gate is never skipped.

## Rollback
```bash
git checkout NN-<module>/vPREV -- .claude/skills/NN-<module> plan/stableMD/NN-<module>.stable.md
git commit -m "revert(NN-module): back to vPREV"
git tag NN-<module>/vPREV-rollback-YYYYMMDD
# push after user approval
```
If the rolled-back version has a different §4 contract, downstream modules must be rolled back to compatible versions too.

## Incident file template (`docs/incidents/YYYY-MM-DD-NN-module-slug.md`)
```markdown
# Incident: <short title>
- Date / time:
- Module & version:
- What happened (symptom):
- Impact (who / what affected):
- Root cause:
- Fix applied (version / commit):
- Prevention (new test T#, new TROUBLESHOOTING row, config change):
```
Incident files are git-ignored (they may contain names/numbers). Commit a sanitized copy as `*.sanitized.md`.

## Backlog (future upgrades, not yet planned)
- 03 scheduler module.
- `config/settings.yaml` for window days, timezone, message format.
- Health-check skill: connector, token validity, calendar IDs, contacts format — run before scheduled jobs.
- WhatsApp delivery/read status via webhook (needs hosted endpoint — separate module).
