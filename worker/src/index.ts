const REVIEWER = "micosjanf";
const AUTHORS = ["samzong", "zhsama"];
const API = "https://api.github.com";

type Env = { APPROVER_PAT: string };
type Call = (method: string, url: string, body?: unknown) => Promise<any>;
type SearchItem = { user: { login: string }; pull_request: { url: string } };

class GitHubError extends Error {
  status: number;
  messages: string[];
  rateLimited: boolean;

  constructor(method: string, url: string, response: Response, payload: any) {
    super(`GitHub ${method} ${new URL(url).pathname}: HTTP ${response.status}`);
    this.status = response.status;
    this.messages = [payload?.message, ...(payload?.errors ?? []).map((error: any) => error?.message)].filter(
      (message): message is string => typeof message === "string" && message !== "",
    );
    this.rateLimited =
      response.status === 429 ||
      (response.status === 403 &&
        (response.headers.get("x-ratelimit-remaining") === "0" || response.headers.has("retry-after")));
  }
}

export function pullsToApprove(items: SearchItem[]): string[] {
  return items.filter((item) => AUTHORS.includes(item.user.login.toLowerCase())).map((item) => item.pull_request.url);
}

function client(token: string): Call {
  return async (method, path, body) => {
    const url = path.startsWith(API) ? path : API + path;
    const response = await fetch(url, {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "micosjanf-auto-approve",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const payload = response.status === 204 ? null : await response.json().catch(() => null);
    if (!response.ok) throw new GitHubError(method, url, response, payload);
    return payload;
  };
}

function rejected(error: unknown): error is GitHubError {
  return (
    error instanceof GitHubError &&
    error.status >= 400 &&
    error.status < 500 &&
    ![401, 408].includes(error.status) &&
    !error.rateLimited
  );
}

async function approve(github: Call, url: string): Promise<"approved" | "rejected" | "stale"> {
  const pull = await github("GET", url);
  if (pull.state !== "open" || !pull.requested_reviewers.some((user: any) => user.login.toLowerCase() === REVIEWER)) {
    return "stale";
  }
  try {
    await github("POST", `${url}/reviews`, { commit_id: pull.head.sha, event: "APPROVE" });
    return "approved";
  } catch (error) {
    if (!rejected(error)) throw error;
    await github("DELETE", `${url}/requested_reviewers`, { reviewers: [REVIEWER] });
    const reasons = error.messages.length ? error.messages : ["GitHub did not provide an error message."];
    await github("POST", pull.comments_url, {
      body: [
        `${REVIEWER} could not auto-approve this PR at head \`${pull.head.sha.slice(0, 12)}\` (HTTP ${error.status}).`,
        reasons.map((message) => `GitHub: ${JSON.stringify(message)}`).join("\n"),
        `Fix the cause, then request a review from ${REVIEWER} again to retry.`,
      ].join("\n\n"),
    });
    return "rejected";
  }
}

export async function run(token: string): Promise<void> {
  const github = client(token);
  const user = await github("GET", "/user");
  if (user.login.toLowerCase() !== REVIEWER) throw new Error(`APPROVER_PAT must belong to ${REVIEWER}`);
  const query = new URLSearchParams({ q: "is:pr is:open user-review-requested:@me", per_page: "100" });
  const search = await github("GET", `/search/issues?${query}`);
  if (search.incomplete_results) throw new Error("GitHub returned incomplete search results");
  const counts = { requested: search.total_count, approved: 0, rejected: 0, stale: 0, failed: 0 };
  for (const url of pullsToApprove(search.items)) {
    const pr = url.slice(`${API}/repos/`.length);
    try {
      const result = await approve(github, url);
      counts[result]++;
      console.log(JSON.stringify({ pr, result }));
    } catch (error) {
      counts.failed++;
      console.error(JSON.stringify({ pr, result: "failed", error: error instanceof Error ? error.message : String(error) }));
    }
  }
  console.log(JSON.stringify(counts));
  if (counts.failed) throw new Error(`${counts.failed} pull request(s) failed`);
}

export default {
  async scheduled(_controller: unknown, env: Env): Promise<void> {
    await run(env.APPROVER_PAT);
  },
};
