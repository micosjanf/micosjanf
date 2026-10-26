# auto-approve

The approval workflow runs every five minutes from `micosjanf/micosjanf`. You can also run it from the Actions tab without inputs. Both scheduled and manual runs submit approvals for eligible PRs. GitHub can delay or drop scheduled runs.

To run it manually, open Actions, select Auto approve, and select Run workflow on `main`. Read the final log summary. A successful run reports `"dry_run": false` and `"failed": 0`. The approval count depends on the eligible PRs.

Edit `config.json` to change the author and repository owner allowlists. Both allowlists must match. The PR must be open and must directly request a review from `micosjanf`. Team requests alone do not qualify. The owner comes from the base repository.

Set the `APPROVER_PAT` repository secret in `micosjanf/micosjanf`. Use an OAuth token or classic PAT owned by `micosjanf` with the `repo` scope. The account needs write access to each target repository. Only the approval step receives this token. In the approval workflow, `GITHUB_TOKEN` has read access for checkout. Checkout does not retain credentials.

Each approval specifies the head SHA. The workflow skips a PR if `micosjanf` already approved that head. The approval on the PR is the record. The approval workflow stores no approval records and creates no commits in this repository, for either public or private PRs.

Each PR failure is logged and skipped, including a rejected approval or an uncertain POST response. The remaining candidates are still processed, and per-PR failures do not fail the run. Runs never fail for application errors: global problems are logged as one safe error line, and the script exits successfully. A broken or expired PAT shows up only as PRs no longer being approved and an error line in the Actions log. The next run checks current requests and approvals again. If a PR keeps failing, cancel its review request for `micosjanf` or fix the cause.

When GitHub rejects an approval with a definite 4xx response, `micosjanf` posts the status and error messages on the PR once per head SHA; timeouts, rate limits, and other uncertain failures do not produce comments.

Runs share one concurrency group and do not cancel a running job. No PR code runs in this workflow.

The approval job also tolerates checkout or test failures so they do not fail the workflow run. Those failures stop the job before approval. GitHub cancellations or runner outages are outside the script's control.

From `auto-approve/`, run `python3 -m unittest discover -s tests -v` before changes. Run `actionlint` from the repository root. To inspect eligible PRs locally, set `APPROVER_PAT` and run `python3 approve.py --dry-run` from `auto-approve/`.

The separate `keepalive.yml` workflow updates `auto-approve/keepalive` with a UTC timestamp at 03:00 UTC on the first day of each month. It also supports manual runs. It commits as `github-actions[bot]` with a sign-off and pushes with `GITHUB_TOKEN`. Only this workflow has `contents: write`. It uses no PAT and does not retain checkout credentials. Update or push failures produce a log line and keep the run successful. The approval workflow does not run on push.

These monthly commits are intended to prevent the 60-day inactivity shutdown. GitHub documents the [shutdown](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) but does not explicitly guarantee that `GITHUB_TOKEN` commits reset its timer. If GitHub disables a schedule, re-enable it on the Actions page.
