import copy
import io
import json
from contextlib import redirect_stdout
from urllib.parse import parse_qs, urlsplit

from approve import API, Approver, GitHubError


REPO = "allowed-owner/example"

HEAD = "a" * 40

CONFIG = {"authors": {"samzong", "zhsama"}, "owners": {"allowed-owner"}}



def pull(number):
    return {
        "number": number,
        "state": "open",
        "merged": False,
        "user": {"login": "samzong"},
        "base": {"repo": {"full_name": REPO, "owner": {"login": "allowed-owner"}, "private": False}},
        "head": {"sha": HEAD, "repo": {"owner": {"login": "fork-owner"}}},
        "requested_reviewers": [{"login": "micosjanf"}],
        "requested_teams": [],
    }



def review(number=1, login="micosjanf", sha=HEAD, state="APPROVED"):
    return {"id": number, "user": {"login": login}, "commit_id": sha, "state": state}



class FakeGitHub(API):
    def __init__(self, count=1):
        self.pulls = {number: pull(number) for number in range(1, count + 1)}
        self.reviews = {number: [] for number in self.pulls}
        self.comments = {number: [] for number in self.pulls}
        self.comment_failures = {}
        self.comment_read_failures = {}
        self.lost_comment_responses = set()
        self.candidate_numbers = list(self.pulls)
        self.calls = []
        self.post_attempts = []
        self.failures = {}
        self.lost_responses = set()
        self.after_reviews = None
        self.responses = {}
        self.login = "micosjanf"
        self.incomplete = False
        self.total_count = None

    def call(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        if method == "GET" and parsed.path == "/user":
            return {"login": self.login}
        if method == "GET" and parsed.path == "/search/issues":
            start = (int(query["page"][0]) - 1) * 100
            numbers = self.candidate_numbers[start:start + 100]
            return {
                "incomplete_results": self.incomplete,
                "total_count": len(self.candidate_numbers) if self.total_count is None else self.total_count,
                "items": [{"repository_url": "https://api.github.com/repos/" + REPO, "number": number} for number in numbers],
            }
        comments_prefix = f"/repos/{REPO}/issues/"
        if parsed.path.startswith(comments_prefix) and parsed.path.endswith("/comments"):
            number = int(parsed.path[len(comments_prefix):].split("/")[0])
            if method == "GET":
                if number in self.comment_read_failures:
                    raise self.comment_read_failures[number]
                start = (int(query["page"][0]) - 1) * 100
                return copy.deepcopy(self.comments[number][start:start + 100])
            if method == "POST":
                if number in self.comment_failures:
                    raise self.comment_failures[number]
                result = {"id": 2000 + number, "user": {"login": "micosjanf"}, "body": data["body"]}
                self.comments[number].append(result)
                if number in self.lost_comment_responses:
                    raise TimeoutError("untrusted response text")
                return copy.deepcopy(result)
        prefix = f"/repos/{REPO}/pulls/"
        if not parsed.path.startswith(prefix):
            raise AssertionError(f"Unexpected API call: {method} {path}")
        suffix = parsed.path[len(prefix):].split("/")
        number = int(suffix[0])
        if method == "GET" and len(suffix) == 1:
            return copy.deepcopy(self.pulls.get(number))
        if suffix[1:] != ["reviews"]:
            raise AssertionError(f"Unexpected API call: {method} {path}")
        if method == "GET":
            start = (int(query["page"][0]) - 1) * 100
            result = copy.deepcopy(self.reviews[number][start:start + 100])
            if self.after_reviews:
                self.after_reviews(number)
            return result
        if method == "POST":
            self.post_attempts.append(number)
            failure = self.failures.get(number)
            if isinstance(failure, int):
                raise GitHubError(method, path, failure, ("Validation Failed", "Pending review must be submitted first"))
            if failure:
                raise failure
            result = review(1000 + number, sha=data["commit_id"])
            self.reviews[number].append(result)
            self.pulls[number]["requested_reviewers"] = []
            if number in self.lost_responses:
                raise TimeoutError("untrusted response text")
            return copy.deepcopy(self.responses.get(number, result))
        raise AssertionError(f"Unexpected API call: {method} {path}")



class BatchAssertions:
    def run_batch(self, api, **kwargs):
        output = io.StringIO()
        with redirect_stdout(output):
            result = Approver(api, CONFIG, **kwargs).run()
        self.assertTrue(all("/contents" not in path for _, path, _ in api.calls))
        self.assertTrue(all(method == "GET" or (method == "POST" and path.endswith(("/reviews", "/comments"))) for method, path, _ in api.calls))
        return result, [json.loads(line) for line in output.getvalue().splitlines()]
