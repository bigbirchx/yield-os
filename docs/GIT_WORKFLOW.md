# Git Workflow

## Rules
- Create or use a dedicated branch/worktree for each task.
- Make a git commit after each completed task.
- Never leave large unrelated changes uncommitted.
- Use conventional commits:
  - feat:
  - fix:
  - refactor:
  - docs:
  - test:
  - chore:

## Commit body requirements
Each commit must include:
- Task:
- Files:
- Reason:
- Tests:

## Example
feat(api): add Velo derivatives connector

Task: Build Velo derivatives ingestion for funding, OI, basis, and volume
Files: apps/api/app/connectors/velo_connector.py, apps/api/app/models/derivatives.py
Reason: Add primary derivatives source for Yield OS MVP
Tests: Added mocked connector client tests

## Agent behavior
- After completing a task, stage only relevant files.
- Show the diff summary before committing.
- Create the commit automatically once the task is confirmed complete.
- Keep commits small and task-scoped.