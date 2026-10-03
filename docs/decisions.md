# Maintained decisions

Current choices, not a session log. The linked contract owns implementation detail.
When a lasting choice changes, update its row and the affected instructions in the
same change; record the new date/reason. Release observations belong in release
evidence rather than this table. Personal/security setup never belongs in a public
decision record. Missing private notes do not block ordinary work.

| Date | Choice | Reason | Owning documentation |
|---|---|---|---|
| 2026-10-03 | Portable agent routes and task-time documentation maintenance | Standalone development stays self-contained; docs/routes change with behavior. | [agent guide](../AGENTS.md) |
| 2026-10-03 | Pin Linux image; preserve complete public CI | Avoid automatic OS migration while keeping required check names, native evidence and installed-wheel matrices. | [contributing](../CONTRIBUTING.md) |
| 2026-10-02 | Public source, optional private prebuilt wheels/tap | A general portfolio tool remains anonymously installable from a versioned source tag. | [install](install.md) |
| 2026-10-02 | Deterministic, bounded archive recovery | Keep provenance and supported-type limits; unsafe links/containers remain unprocessed. | [manual](manual.md) |
| 2026-10-02 | No PyPI, WinGet or standalone CLI binaries | Current source/wheel/Homebrew routes cover the supported use case. | [release](release.md) |

All applications were generated AI-first. Commit authorship uses the owner's
configured identity; this does not imply unaided implementation.
