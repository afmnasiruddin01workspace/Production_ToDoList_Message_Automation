---
module: <kebab-name>            # e.g. calendar-todo
id: "NN"                        # e.g. "01"
phase: draft                    # draft | test | stable
version: "0.1"                  # MAJOR.MINOR — MAJOR when §4 Outputs contract changes
depends_on: []                  # e.g. ["01-calendar-todo"]
input_from: <path or none>      # e.g. outputs/01-calendar-todo/todo_by_name.json
output_to: outputs/NN-<module>/
approved_by: <name / pending>
date: YYYY-MM-DD
---

# NN — <Module Title>

## 1. Frontmatter
See YAML header above. Every field must be filled before validation.

## 2. Goal
<One or two sentences: what this module achieves and for whom.>

## 3. Inputs
| Name | Path / Source | Format | Schema / Columns | Private? |
|---|---|---|---|---|
| <input> | <path or MCP tool> | <json/tsv/txt> | <fields> | yes/no |

> For NN > 01, this section MUST match the upstream module's §4 Outputs exactly.

## 4. Outputs (contract for next module)
| Name | Path | Format | Schema |
|---|---|---|---|
| <output> | outputs/NN-<module>/<file> | json/md | <fields> |

```json
// example output shape
```

## 5. Scope
**In scope**
- <item>

**Out of scope**
- <item>

## 6. Requirements
| ID | Requirement (testable) |
|---|---|
| R1 | <…> |

## 7. Steps / Design
1. <step> — tool used: <MCP tool / HTTP call / file read>
2. <step>

Configuration / environment variables:
| Name | Purpose | Where set |
|---|---|---|
| <VAR> | <…> | env / file |

## 8. Validation Checklist
- [ ] All frontmatter fields filled
- [ ] No `TBD`, `TODO`, `<placeholder>` left
- [ ] §3 Inputs match upstream §4 Outputs (or source file actually exists)
- [ ] Every R# in §6 is implemented by at least one step in §7
- [ ] Every R# in §6 is covered by at least one test in §9
- [ ] No secrets or real phone numbers written in this file
- [ ] §12 Troubleshooting filled for known failure points

Validation result: <pass / fail> — checked by Claude on YYYY-MM-DD — approved by user: <yes/no>

## 9. Test Cases
| ID | Covers | Input / Setup | Expected | Result | Evidence |
|---|---|---|---|---|---|
| T1 | R1 | <…> | <…> | pending | <output path / screenshot / log> |

Test result: <pass / fail> — run on YYYY-MM-DD — approved by user: <yes/no>

## 10. Risks & Mitigations
| Risk | Impact | Mitigation |
|---|---|---|
| <…> | <…> | <…> |

## 11. Change Log
| Version | Date | Phase | Change | Reason |
|---|---|---|---|---|
| 0.1 | YYYY-MM-DD | draft | Initial draft | — |

## 12. Troubleshooting & Rollback
| Symptom | Likely cause | Detect | Fix |
|---|---|---|---|
| <…> | <…> | <…> | <…> |

**Rollback:** `git checkout NN-<module>/v<prev> -- .claude/skills/NN-<module> plan/stableMD/NN-<module>.stable.md`
See also `docs/TROUBLESHOOTING.md#nn-<module>`.
