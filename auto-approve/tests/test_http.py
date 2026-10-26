import io
import json
import runpy
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.client import IncompleteRead
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit

from approve import API, Approver, GitHubError, main
from approve_fixtures import BatchAssertions, CONFIG, REPO, FakeGitHub


class ApproverTests(BatchAssertions, unittest.TestCase):
    def test_422_continues_second_pr_and_cli_returns_success(self):
        api = FakeGitHub(2)
        api.failures[1] = 422
        output = io.StringIO()
        with patch("approve.API", return_value=api), patch("approve.configuration", return_value=CONFIG), patch("sys.argv", ["approve.py"]), redirect_stdout(output):
            self.assertEqual(main(), 0)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(lines[0], {"repo": REPO, "pr": 1, "method": "POST", "path": f"/repos/{REPO}/pulls/1/reviews", "status": 422})
        self.assertEqual(api.post_attempts, [1, 2])
        self.assertEqual(api.reviews[1], [])
        self.assertEqual(len(api.reviews[2]), 1)
        self.assertEqual(lines[-1], {"dry_run": False, "candidates": 2, "approved": 1, "failed": 1})
        self.assertTrue(all("/contents" not in path for _, path, _ in api.calls))
        api.failures.clear()
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [1, 2, 1])


    def test_cli_dry_run_makes_no_writes(self):
        api = FakeGitHub(2)
        api.failures[1] = 422
        output = io.StringIO()
        with patch("approve.API", return_value=api), patch("approve.configuration", return_value=CONFIG), patch("sys.argv", ["approve.py", "--dry-run"]), redirect_stdout(output):
            self.assertEqual(main(), 0)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([line["pr"] for line in lines[:-1]], [1, 2])
        self.assertTrue(all("would_approve" in line for line in lines[:-1]))
        self.assertEqual(lines[-1], {"dry_run": True, "candidates": 2, "approved": 0, "failed": 0})
        self.assertTrue(all(method == "GET" for method, _, _ in api.calls))


    def test_real_http_adapter_isolates_422_and_hides_response_body(self):
        backend = FakeGitHub(2)
        requests = []
        def respond(request, timeout):
            parsed = urlsplit(request.full_url)
            path = parsed.path + ("?" + parsed.query if parsed.query else "")
            method = request.get_method()
            requests.append((method, path))
            if method == "POST" and path == f"/repos/{REPO}/pulls/1/reviews":
                payload = {
                    "message": "Validation Failed",
                    "errors": [{"message": "Pending review: fixture-credential"}, {"code": "custom"}, "ignored detail"],
                    "headers": {"Authorization": "header-only-private-details"},
                }
                raise HTTPError(request.full_url, 422, "private details", {}, io.BytesIO(json.dumps(payload).encode()))
            data = json.loads(request.data) if request.data is not None else None
            result = backend.call(method, path, data)
            return io.BytesIO(json.dumps(result).encode())
        output = io.StringIO()
        with patch("approve.urlopen", side_effect=respond), redirect_stdout(output):
            self.assertEqual(Approver(API("fixture-credential"), CONFIG).run(), 0)
        lines = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(lines[0], {"repo": REPO, "pr": 1, "method": "POST", "path": f"/repos/{REPO}/pulls/1/reviews", "status": 422})
        self.assertEqual(backend.post_attempts, [2])
        self.assertNotIn("private details", output.getvalue())
        self.assertNotIn("fixture-credential", output.getvalue())
        self.assertTrue(all("/contents" not in path for _, path in requests))
        body = backend.comments[1][0]["body"]
        self.assertIn("Validation Failed", body)
        self.assertIn("Pending review: [REDACTED]", body)
        self.assertNotIn("fixture-credential", body)
        self.assertNotIn("header-only-private-details", body)
        self.assertNotIn("Authorization", body)
        self.assertNotIn("ignored detail", body)


    def test_http_and_transport_failures_do_not_block_other_prs(self):
        for failure in (403, 408, 500, TimeoutError("private details"), URLError("private details"), ConnectionError("private details"), IncompleteRead(b"private details")):
            with self.subTest(failure=type(failure).__name__ if not isinstance(failure, int) else failure):
                api = FakeGitHub(2)
                api.failures[1] = failure
                result, output = self.run_batch(api)
                self.assertEqual(result, 0)
                self.assertEqual(api.post_attempts, [1, 2])
                self.assertEqual(len(api.reviews[2]), 1)
                self.assertEqual(output[-1]["failed"], 1)
                self.assertNotIn("private details", json.dumps(output))


    def test_rate_limited_approval_does_not_comment_and_next_run_retries(self):
        cases = (
            (429, {}, "Too many requests"),
            (403, {"X-RateLimit-Remaining": "0"}, "Forbidden"),
            (403, {"Retry-After": "60"}, "Forbidden"),
            (403, {}, "API rate limit exceeded"),
            (403, {}, "You have exceeded a SECONDARY RATE LIMIT"),
        )
        for status, headers, message in cases:
            with self.subTest(status=status, headers=headers, message=message):
                backend = FakeGitHub(2)
                requests = []
                reject = True
                def respond(request, timeout):
                    parsed = urlsplit(request.full_url)
                    path = parsed.path + ("?" + parsed.query if parsed.query else "")
                    method = request.get_method()
                    requests.append((method, path))
                    if reject and method == "POST" and path == f"/repos/{REPO}/pulls/1/reviews":
                        error = HTTPError(request.full_url, status, "Rejected", headers, io.BytesIO(json.dumps({"message": message}).encode()))
                        self.addCleanup(error.close)
                        raise error
                    data = json.loads(request.data) if request.data is not None else None
                    return io.BytesIO(json.dumps(backend.call(method, path, data)).encode())
                with patch("approve.urlopen", side_effect=respond), redirect_stdout(io.StringIO()):
                    approver = Approver(API("fixture-credential"), CONFIG)
                    self.assertEqual(approver.run(), 0)
                    self.assertEqual(backend.comments[1], [])
                    self.assertFalse(any("/comments" in path for _, path in requests))
                    self.assertEqual(backend.post_attempts, [2])
                    reject = False
                    self.assertEqual(approver.run(), 0)
                self.assertEqual(backend.post_attempts, [2, 1])


    def test_api_closes_every_http_error_response(self):
        cases = (
            ("GET", f"/repos/{REPO}/pulls/1", 404, b"not found"),
            ("POST", f"/repos/{REPO}/pulls/1/reviews", 422, b'{"message": "Validation Failed"}'),
            ("POST", f"/repos/{REPO}/issues/1/comments", 403, b'{"message": "Forbidden"}'),
            ("GET", f"/repos/{REPO}/issues/1/comments", 403, b"not JSON"),
            ("GET", "/user", 401, b'{"message": "Unauthorized"}'),
        )
        for method, path, status, payload in cases:
            with self.subTest(method=method, path=path, status=status):
                response = io.BytesIO(payload)
                error = HTTPError("https://api.github.com" + path, status, "Rejected", {}, response)
                try:
                    with patch("approve.urlopen", side_effect=error):
                        if method == "GET" and status == 404:
                            self.assertIsNone(API("fixture-credential").call(method, path))
                        else:
                            with self.assertRaises(GitHubError):
                                API("fixture-credential").call(method, path)
                    self.assertTrue(response.closed)
                finally:
                    error.close()


    def test_global_errors_log_one_safe_line_and_cli_exits_zero(self):
        for problem in ("wrong-identity", "unauthorized", "incomplete-search", "unexpected-exception", "invalid-arguments"):
            with self.subTest(problem=problem):
                backend = FakeGitHub()
                if problem == "wrong-identity":
                    backend.login = "samzong"
                elif problem == "incomplete-search":
                    backend.incomplete = True
                def respond(request, timeout):
                    parsed = urlsplit(request.full_url)
                    if problem == "unexpected-exception":
                        raise RuntimeError("Authorization: Bearer fixture-credential; private details")
                    if problem == "unauthorized" and parsed.path == "/user":
                        raise HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"private details"))
                    path = parsed.path + ("?" + parsed.query if parsed.query else "")
                    value = backend.call(request.get_method(), path)
                    return io.BytesIO(json.dumps(value).encode())
                output = io.StringIO()
                arguments = ["approve.py", "--unexpected=Authorization: Bearer fixture-credential"] if problem == "invalid-arguments" else ["approve.py"]
                with patch("urllib.request.urlopen", side_effect=respond), patch("sys.argv", arguments), patch.dict("os.environ", {"APPROVER_PAT": "fixture-credential"}), redirect_stdout(output), redirect_stderr(output):
                    with self.assertRaises(SystemExit) as caught:
                        runpy.run_path("approve.py", run_name="__main__")
                self.assertEqual(caught.exception.code, 0)
                lines = output.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                logged = json.loads(lines[0])
                expected_type = {"wrong-identity": "ValueError", "unauthorized": "GitHubError", "incomplete-search": "RuntimeError", "unexpected-exception": "RuntimeError", "invalid-arguments": "ValueError"}[problem]
                self.assertEqual(logged["error"], expected_type)
                self.assertTrue(logged["message"])
                if problem == "unauthorized":
                    self.assertIn("HTTP 401", logged["message"])
                self.assertNotIn("Authorization", output.getvalue())
                self.assertEqual(backend.post_attempts, [])
                self.assertNotIn("fixture-credential", output.getvalue())
                self.assertNotIn("private details", output.getvalue())



if __name__ == "__main__":
    unittest.main()
