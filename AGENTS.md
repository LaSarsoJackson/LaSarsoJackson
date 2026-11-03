# Agent instructions

## Documentation

Write the README and public pages for people using or evaluating the project. Explain what it does, show how to get started, and link to deeper guides. Keep agent workflow rules, editing history, and implementation constraints here. Preserve real limitations and exact commands. Avoid generic slogans, invented terminology, and repeated summaries.

This README appears on the public GitHub profile. Preserve credentials, dates, team attribution, and source links. Use only verified public work; never copy private client or career notes into the profile.

## Local validation and automated delivery

Run `python .github/agent-ci.py check` before committing, and fix reported errors
in the current workspace. Use `check --full` when the affected behavior needs the
declared regression checks. Run `python .github/agent-ci.py install` once per
checkout to enable the pre-push hook. Existing hooks must be integrated first.
After committing a focused branch, use `python .github/agent-ci.py ship` to push,
open its pull request, and enable auto-merge for that exact commit. Required CI
checks decide the merge; Jackson does not need to approve routine code changes.
Do not bypass failed checks or replace assertions to obtain a passing result.
`repair --agent '<JSON argument array>'` permits two local repair attempts with
five minutes per attempt and stops if tests or validation policy change.
`rollback --commit <sha>` creates a revert through the same checked path.
Deployment changes do not authorize live business-record writes or native store
submissions. The maintained controller and usage are in
`LaSarsoJackson/preference-backup/system-tools/agent-ci/`.
