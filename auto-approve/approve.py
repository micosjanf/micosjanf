import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REVIEWER = "micosjanf"
REPO_PATTERN = r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"


class GitHubError(RuntimeError):
    def __init__(self, method, path, status, messages=(), rate_limited=False):
        self.method = method
        self.path = path
        self.status = status
        self.messages = messages
        self.rate_limited = rate_limited or status == 429
        super().__init__(f"GitHub {method} {path}: HTTP {status}")


class API:
    def __init__(self, token):
        if not token:
            raise ValueError("APPROVER_PAT is missing")
        self.token = token

    def call(self, method, path, data=None):
        request = Request(
            "https://api.github.com" + path,
            data=json.dumps(data).encode() if data is not None else None,
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "micosjanf/micosjanf",
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            with error:
                if method == "GET" and error.code == 404:
                    return None
                try:
                    payload = json.load(error)
                except Exception:
                    payload = {}
                messages = []
                if isinstance(payload, dict):
                    messages.append(payload.get("message"))
                    details = payload.get("errors")
                    if isinstance(details, list):
                        messages.extend(item.get("message") for item in details if isinstance(item, dict))
                messages = [message.replace(self.token, "[REDACTED]") for message in messages if isinstance(message, str) and message]
                headers = {key.lower(): value for key, value in (error.headers or {}).items()}
                rate_limited = error.code == 403 and (
                    str(headers.get("x-ratelimit-remaining", "")).strip() == "0"
                    or "retry-after" in headers
                    or any("rate limit" in message.lower() for message in messages)
                )
                raise GitHubError(method, path, error.code, messages, rate_limited) from None

    def pages(self, path):
        page = 1
        while True:
            separator = "&" if "?" in path else "?"
            items = self.call("GET", f"{path}{separator}per_page=100&page={page}")
            if not isinstance(items, list):
                raise RuntimeError("GitHub did not return a list")
            yield from items
            if len(items) < 100:
                return
            page += 1


def configuration(path):
    data = json.loads(Path(path).read_text())
    if set(data) != {"authors", "owners"}:
        raise ValueError("Configuration must contain authors and owners")
    for key in data:
        if not isinstance(data[key], list) or any(
            not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]+", value)
            for value in data[key]
        ):
            raise ValueError(f"Invalid {key} allowlist")
        data[key] = {value.lower() for value in data[key]}
    return data


class Approver:
    def __init__(self, approval, config, dry_run=False):
        self.approval = approval
        self.config = config
        self.dry_run = dry_run

    def eligible(self, pull, repo):
        return (
            pull["base"]["repo"]["full_name"].lower() == repo.lower()
            and pull["state"] == "open"
            and not pull.get("merged", False)
            and pull["user"]["login"].lower() in self.config["authors"]
            and pull["base"]["repo"]["owner"]["login"].lower() in self.config["owners"]
            and any(user["login"].lower() == REVIEWER for user in pull["requested_reviewers"])
        )

    def comment_rejection(self, repo, number, head_sha, rejection):
        endpoint = f"/repos/{repo}/issues/{number}/comments"
        marker = f"<!-- auto-approve:{head_sha} -->"
        try:
            if any(
                comment["user"]["login"].lower() == REVIEWER
                and marker in (comment.get("body") or "")
                for comment in self.approval.pages(endpoint)
            ):
                return
            reasons = rejection.messages or ["GitHub did not provide an error message."]
            body = (
                f"micosjanf could not auto-approve this PR at head `{head_sha[:12]}` (HTTP {rejection.status}).\n\n"
                + "\n".join("GitHub: " + json.dumps(message, ensure_ascii=False) for message in reasons)
                + "\n\n" + marker
            )
            self.approval.call("POST", endpoint, {"body": body})
        except GitHubError as error:
            print(json.dumps({"repo": repo, "pr": number, "action": "rejection_comment", "method": error.method, "path": error.path, "status": error.status}))
        except Exception as error:
            print(json.dumps({"repo": repo, "pr": number, "action": "rejection_comment", "error": type(error).__name__}))

    def approve(self, repo, number):
        endpoint = f"/repos/{repo}/pulls/{number}"
        pull = self.approval.call("GET", endpoint)
        if pull is None or not self.eligible(pull, repo):
            print(json.dumps({"skipped": repo, "pr": number}))
            return False
        head_sha = pull["head"]["sha"]
        if any(
            review["user"]["login"].lower() == REVIEWER
            and review["commit_id"] == head_sha
            and review["state"] == "APPROVED"
            for review in self.approval.pages(endpoint + "/reviews")
        ):
            print(json.dumps({"existing_approval": repo, "pr": number, "head_sha": head_sha}))
            return False
        current = self.approval.call("GET", endpoint)
        if current is None or not self.eligible(current, repo) or current["head"]["sha"] != head_sha:
            print(json.dumps({"changed": repo, "pr": number}))
            return False
        if self.dry_run:
            print(json.dumps({"would_approve": repo, "pr": number, "head_sha": head_sha}))
            return False
        try:
            review = self.approval.call(
                "POST", endpoint + "/reviews", {"commit_id": head_sha, "event": "APPROVE"}
            )
        except GitHubError as error:
            if 400 <= error.status < 500 and error.status != 408 and not error.rate_limited:
                self.comment_rejection(repo, number, head_sha, error)
            raise
        if (
            review.get("state") != "APPROVED"
            or review.get("commit_id") != head_sha
            or review.get("user", {}).get("login", "").lower() != REVIEWER
        ):
            raise RuntimeError("GitHub did not confirm the expected approval")
        print(json.dumps({"approved": repo, "pr": number, "head_sha": head_sha, "review_id": review["id"]}))
        return True

    def candidates(self):
        query = f"is:pr is:open review-requested:{REVIEWER}"
        page = 1
        while True:
            result = self.approval.call(
                "GET", "/search/issues?" + urlencode({"q": query, "per_page": 100, "page": page})
            )
            if result is None or result["incomplete_results"] or result["total_count"] > 1000:
                raise RuntimeError("PR search is incomplete")
            for item in result["items"]:
                prefix = "https://api.github.com/repos/"
                if not item["repository_url"].startswith(prefix):
                    raise RuntimeError("Unexpected repository URL")
                repo = item["repository_url"][len(prefix):]
                if not re.fullmatch(REPO_PATTERN, repo):
                    raise RuntimeError("Invalid repository name from search")
                yield repo, item["number"]
            if page * 100 >= result["total_count"]:
                return
            page += 1

    def run(self):
        if self.approval.call("GET", "/user")["login"].lower() != REVIEWER:
            raise ValueError("APPROVER_PAT must belong to micosjanf")
        seen = set()
        approved = failed = 0
        for repo, number in self.candidates():
            key = (repo.lower(), number)
            if key in seen:
                continue
            seen.add(key)
            try:
                approved += self.approve(repo, number)
            except GitHubError as error:
                failed += 1
                print(json.dumps({"repo": repo, "pr": number, "method": error.method, "path": error.path, "status": error.status}))
            except Exception as error:
                failed += 1
                print(json.dumps({"repo": repo, "pr": number, "error": type(error).__name__}))
        print(json.dumps({"dry_run": self.dry_run, "candidates": len(seen), "approved": approved, "failed": failed}))
        return 0


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError("Invalid command-line arguments")


def main():
    parser = SafeArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    return Approver(API(os.getenv("APPROVER_PAT")), configuration(args.config), args.dry_run).run()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        message = "Auto-approval stopped. Check APPROVER_PAT, config.json, and connectivity."
        if isinstance(error, GitHubError):
            message = f"GitHub {error.method} {error.path}: HTTP {error.status}"
        print(json.dumps({"error": type(error).__name__, "message": message}))
    sys.exit(0)
