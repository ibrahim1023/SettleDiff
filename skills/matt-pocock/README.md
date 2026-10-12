# Matt Pocock skills for SettleDiff

Selected using the upstream `ask-matt` router after reviewing SettleDiff's foundation,
ADRs, agent tests, and verification loops. Source: [mattpocock/skills](https://github.com/mattpocock/skills),
pinned to `49dd158d1076134a641b33efb035946536778336` on 2026-10-12.
The upstream files and supporting references are copied without modifications; see [LICENSE](LICENSE).

| Task | Skill |
|---|---|
| Choose a workflow | [ask-matt](ask-matt/SKILL.md) |
| Build a behavior test-first | [tdd](tdd/SKILL.md) |
| Reproduce and diagnose a difficult failure | [diagnosing-bugs](diagnosing-bugs/SKILL.md) |
| Review a diff against standards and its specification | [code-review](code-review/SKILL.md) |
| Design a module interface or test seam | [codebase-design](codebase-design/SKILL.md) |
| Find architectural friction and deepening opportunities | [improve-codebase-architecture](improve-codebase-architecture/SKILL.md) |
| Clarify evidence vocabulary or an architectural decision | [domain-modeling](domain-modeling/SKILL.md) |
| Transfer context between Codex and Devin | [handoff](handoff/SKILL.md) |
| Improve the development environment after a session | [retro](retro/SKILL.md) |
| Write a pull request description | [pr](pr/SKILL.md) |
| Write agent instructions | [writing-for-agents](writing-for-agents/SKILL.md) |
| Configure the upstream engineering workflows | [setup-matt-pocock-skills](setup-matt-pocock-skills/SKILL.md) |

## Discovery and scope

Codex reads the root `AGENTS.md` pointer and loads the named skill from this directory.
Native Codex skill discovery through `.agents/skills` could not be installed because
this session's filesystem permissions protect `.agents`. Repository routing remains
available; this is not a native slash-command installation for Codex.
Devin for Terminal discovers the relative links under `.devin/skills`.
Its pre-existing `ask-matt` copy is preserved.

The router describes the complete upstream catalog; only the table above is installed
in this project. A route mentioning another skill does not imply it is available.
TypeScript tooling and whole-build orchestrators were omitted because SettleDiff
already has Python tooling and a tracked implementation workflow.

`setup-matt-pocock-skills` is installed but has not been run: tracker configuration is
outside this installation. Before using upstream flows that require tracker setup,
run that skill with the user. Preserve the existing `docs/decisions/` ADR location and
the repository verification loops when configuring it.

Installation does not run the installed workflows. Repository rules remain authoritative:
use offline evidence, exact one-use payment authorization, deterministic financial
checks, and the tracked verification loops. Handoff files must exclude credentials,
raw financial payloads, and the contents of local-only product inputs.

## Updating

Update the canonical copies here together with their supporting files and license,
record the new upstream commit, and review instruction changes before use.
`skills-lock.json` predates this installation and tracks the existing Devin `ask-matt`
installation; it does not describe this vendored set.
