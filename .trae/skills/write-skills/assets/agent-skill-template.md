# Agent Skill Template

Return metadata separately from the body. The Coordinator renders this file:

```markdown
---
name: <lowercase-kebab-case-name>
description: <what this skill does and when to use it>
---

# <Workflow Title>

## Inputs
Identify the task, required inputs, approved tools and acceptance criteria.
Ask [[CLARIFY: question]] if inputs are insufficient.

## Procedure
1. Inspect inputs using permitted read tools.
2. Perform the role-specific work using explicitly authorized tools.
3. Run the relevant checks; do not invent execution evidence.

## Acceptance
Check each stated requirement against actual outputs.
Report passed, failed and not-run checks separately.

## Output
Return deliverable locations, supporting evidence and remaining limitations.

## Failure Handling
Stop on permission failures or unsafe requests; do not change policy.
Report tool errors and request approval through the platform when required.
```

For `submit_team`, place only the body in `instructions`; do not embed frontmatter.
The same skill is loaded via `skills:load`. A Skill teaches procedure, not authority.
