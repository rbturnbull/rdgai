# CLAUDE.md

## Workflow

- **Always write tests before coding.** For any new feature or behaviour change, first write (or update) the tests that specify it, run them to confirm they fail for the expected reason, then implement until they pass. Run the full suite (`python -m pytest`) before finishing.
