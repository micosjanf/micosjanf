# micosjanf-auto-approve

This Cloudflare Worker approves pull requests for `micosjanf`. A Cron Trigger runs it every two minutes. The Worker has no HTTP route.

Each run does these steps:

1. Find the open pull requests that request a review directly from `micosjanf`. A request to a team does not count.
2. Skip each pull request whose author is not in `AUTHORS` in `src/index.ts`.
3. Approve the current head commit if the review request is still open.

GitHub removes the review request when `micosjanf` submits a review. A later run does not approve the same request again. To get a new approval, request a review from `micosjanf` again.

If GitHub rejects an approval with a 4xx status, the Worker removes the review request. It then posts the status and the GitHub error messages on the pull request. Rate limits, timeouts, and 5xx errors do not cause a comment. The next run tries again.

## Scope

The repositories in scope are the repositories where `micosjanf` has write access. To add a repository, give `micosjanf` write access to it. To add an author, add the GitHub login to `AUTHORS` and deploy again.

If an organization uses SAML single sign-on, authorize the token of `micosjanf` for that organization. Without this authorization, the search does not return private pull requests from that organization, and the Worker logs no error.

## Deploy

Run these commands from `worker/` as a Cloudflare user who can deploy Workers:

1. Run `wrangler secret put APPROVER_PAT`. Paste an OAuth token or a classic personal access token that belongs to `micosjanf` and has the `repo` scope.
2. Run `wrangler deploy`.

## Logs

Workers Logs show one line for each processed pull request and one summary line for each run. The summary has the counts `requested`, `approved`, `rejected`, `stale`, and `failed`. If the token is not valid or a pull request fails, the run throws an error. Cron Events in the Cloudflare dashboard then show the run as failed.

## Test

Run `npm test` from `worker/`. The test needs Node.js 24 or later.

To run the Worker on your computer, run `wrangler dev --test-scheduled` with `APPROVER_PAT` in `.dev.vars`. Then open `http://localhost:8787/__scheduled`. This run submits real approvals.
