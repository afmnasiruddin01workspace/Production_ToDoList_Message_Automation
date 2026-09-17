# Production ToDo → WhatsApp Automation

Modular chain of Claude Code skills. Each module's output is the next module's input.

| ID | Module | Input | Output |
|---|---|---|---|
| 01 | calendar-todo | Google Calendars in `src/calendarLinks/lists.txt` | `outputs/01-calendar-todo/todo_by_name.json` (+ `.md`) |
| 02 | whatsapp-message | 01 output + `src/messageListNDetails/WhatsappContacts.txt` | `outputs/02-whatsapp-message/send_log.json` |
| 03 | scheduler | 01 + 02 skills | TBD in its explore phase |

## Phase pipeline (mandatory for every module and every change)

```
explore → plan → draftMD → validate ⟲ → testMD → test ⟲ → stableMD → execute → SKILL → output → next module
```

1. **Explore** — read upstream `outputs/`, source files, MCP capabilities. Write nothing.
2. **Plan → draftMD** — copy `plan/templates/prd_todo_template.md` to `plan/draftMD/NN-<module>.draft.md`, fill all 12 sections, `phase: draft`.
3. **Validate** — run §8 checklist, report results. Fail → fix draft. Pass → **ask user to approve** → copy to `plan/testMD/NN-<module>.test.md`, `phase: test`.
4. **Test** — run §9 test cases, record Result + Evidence. Fail → fix (return to draft if design changes). Pass → **ask user to approve** → copy to `plan/stableMD/NN-<module>.stable.md`, `phase: stable`.
5. **Execute** — real run from stable §7, write to `outputs/NN-<module>/`.
6. **Skill** — create `.claude/skills/NN-<module>/SKILL.md` from stable §7; end it with an "If it fails" block linking `docs/TROUBLESHOOTING.md`.
7. **Publish** — add CHANGELOG line, commit, tag, push (see Git policy). **Ask before pushing.**

Never skip a gate. Never promote without explicit user approval.

## Fixed decisions
- Person = name inside `()` in event title, but only if it matches the `Person` column in `WhatsappContacts.txt` (case-insensitive; a leading `From `/`By ` is stripped). No match → `"No Name"`. `(A, B)` → listed under each matched name.
- A task-type item (has a `tasks.google.com/task/` link in its description) with no matched name → group `"A F M Nasir Uddin"` instead of `"No Name"` (the module can't tell which Google Tasks list an item is from, so an unnamed task is assumed to be the owner's own).
- Holiday calendars (ID contains `#holiday@group.v.calendar.google.com`) are never read, even if added to `lists.txt`.
- Known limitation: some real Google Tasks never come back from the Calendar API's `list_events`, regardless of Tasks list, due date/time, or recurrence — verified directly, no fix available without a separate Google Tasks connector. Accepted for v0.1.
- Output contains only group name + list of items (type, title). No date, time, or calendar name is output; the item's start and calendar are used internally only (ordering, run summary).
- Window: **start of today** (00:00:00) → today + 30 days (23:59:59), timezone `Asia/Dhaka`.
- WhatsApp: Business Cloud API (coexistence). Env vars `WA_PHONE_NUMBER_ID`, `WA_TOKEN`, `WA_MODE` (`dry-run` | `self` | `live`). Never write tokens into files.
- Module 02 test ladder: `dry-run` → `self` (own number only) → `live`.
- Messages in English, numbered list per person.

## Naming
- Plans: `plan/{draftMD,testMD,stableMD}/NN-<module>.{draft,test,stable}.md`
- Skills: `.claude/skills/NN-<module>/SKILL.md`
- Tags: `NN-<module>/vMAJOR.MINOR`

## Git policy
- Remote: https://github.com/afmnasiruddin01workspace/Production_ToDoList_Message_Automation (branch `main`).
- Only stable work is committed: stableMD, skills, docs, samples, CLAUDE.md, CHANGELOG.md, `.gitignore`, `.claude/settings.json`.
- Never commit: draftMD/testMD, real `lists.txt`, real `WhatsappContacts.txt`, `outputs/` data, `.env`, `settings.local.json`.
- Before each commit run `git diff --cached --name-only` and check for private files.
- Commit: `feat(NN-module): stable vX.Y`, `fix(NN-module): vX.Y — …`, `revert(NN-module): back to vX.Y`.

## Changes, upgrades, failures
- Upgrade / hotfix / rollback process: `docs/UPGRADE.md`
- Symptom → fix runbook: `docs/TROUBLESHOOTING.md`
- Real failures: `docs/incidents/YYYY-MM-DD-NN-module-slug.md`
- Stable files are never edited in place — every change starts a new draft with a version bump.
