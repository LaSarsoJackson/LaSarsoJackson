# Agent instructions

## Documentation

Write the README and public pages for people using or evaluating the project. Explain what it does, show how to get started, and link to deeper guides. Keep agent workflow rules, editing history, and implementation constraints here. Preserve real limitations and exact commands. Avoid generic slogans, invented terminology, and repeated summaries.

This README appears on the public GitHub profile. Preserve credentials, dates, team attribution, and source links. Use only verified public work; never copy private client or career notes into the profile.

## Local validation and automated delivery

Run `python .github/agent-ci.py check` while editing. The pre-push hook keeps
syntax, lint and compile feedback fast. After committing a focused branch, run
`python .github/agent-ci.py ship`. It runs the declared regression tasks affected
by the diff locally before pushing, opens the PR and requests auto-merge.
Use `check --full` for every declared task. Fix failures locally before delivery.
Install the hook once per checkout with `python .github/agent-ci.py install`.
Preserve existing hooks and use the owning application's required checks when
its workflow is broader than the shared task configuration.

Routine verification workflows are manual diagnostics in Actions. Hosted builds,
deployments and health checks keep their own release requirements. GrandTrack
also retains its main-branch CI because deployments and backups depend on it.
Local results are trusted; GitHub does not attest that a direct push ran tests.
Do not replace assertions to obtain a passing result. Routine changes require
no human PR approval. `repair --agent '<JSON argument array>'` permits two local
repair attempts, each limited to five minutes. `rollback --commit <sha>` creates
a checked revert. Deployment changes do not authorize business-record writes or
native store submissions. The maintained controller is in
`LaSarsoJackson/preference-backup/system-tools/agent-ci/`.
