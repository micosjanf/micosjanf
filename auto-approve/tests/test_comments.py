import json
import unittest
from urllib.error import URLError

from approve import GitHubError
from approve_fixtures import BatchAssertions, HEAD, REPO, FakeGitHub


class ApproverTests(BatchAssertions, unittest.TestCase):
    def test_rejected_approval_comments_once_per_head_with_reasons(self):
        api = FakeGitHub()
        api.failures[1] = 422
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(len(api.comments[1]), 1)
        body = api.comments[1][0]["body"]
        for text in ("micosjanf could not auto-approve this PR", HEAD[:12], "HTTP 422", "Validation Failed", "Pending review must be submitted first", f"<!-- auto-approve:{HEAD} -->"):
            self.assertIn(text, body)
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(len(api.comments[1]), 1)
        api.pulls[1]["head"]["sha"] = "b" * 40
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(len(api.comments[1]), 2)
        self.assertIn(f"<!-- auto-approve:{'b' * 40} -->", api.comments[1][1]["body"])


    def test_comment_dedup_checks_author_and_all_pages(self):
        api = FakeGitHub()
        api.failures[1] = 422
        body = f"<!-- auto-approve:{HEAD} -->"
        api.comments[1] = [{"user": {"login": "another-user"}, "body": body} for _ in range(100)]
        self.run_batch(api)
        self.assertEqual(len(api.comments[1]), 101)
        self.run_batch(api)
        self.assertEqual(len(api.comments[1]), 101)
        self.assertTrue(any("/comments?per_page=100&page=2" in path for _, path, _ in api.calls))


    def test_ambiguous_approval_errors_post_no_comment(self):
        for failure in (408, 500, TimeoutError("private details"), URLError("private details")):
            with self.subTest(failure=type(failure).__name__ if not isinstance(failure, int) else failure):
                api = FakeGitHub()
                api.failures[1] = failure
                self.assertEqual(self.run_batch(api)[0], 0)
                self.assertEqual(api.comments[1], [])
                self.assertFalse(any("/comments" in path for _, path, _ in api.calls))


    def test_comment_post_failure_does_not_block_other_prs(self):
        for failure in (GitHubError("POST", f"/repos/{REPO}/issues/1/comments", 403), TimeoutError("private details")):
            with self.subTest(failure=type(failure).__name__):
                api = FakeGitHub(2)
                api.failures[1] = 422
                api.comment_failures[1] = failure
                result, output = self.run_batch(api)
                self.assertEqual(result, 0)
                self.assertEqual(api.post_attempts, [1, 2])
                self.assertEqual(len(api.reviews[2]), 1)
                self.assertTrue(any(item.get("action") == "rejection_comment" for item in output))
                self.assertNotIn("private details", json.dumps(output))


    def test_comment_lookup_failure_skips_comment_and_continues(self):
        api = FakeGitHub(2)
        api.failures[1] = 422
        api.comment_read_failures[1] = GitHubError("GET", f"/repos/{REPO}/issues/1/comments", 403)
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [1, 2])
        self.assertEqual(api.comments[1], [])
        self.assertFalse(any(method == "POST" and path.endswith("/comments") for method, path, _ in api.calls))


    def test_lost_comment_response_does_not_duplicate_comment_next_run(self):
        api = FakeGitHub()
        api.failures[1] = 422
        api.lost_comment_responses.add(1)
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(len(api.comments[1]), 1)


    def test_ineligible_prs_and_dry_runs_never_comment(self):
        for reason in ("author", "owner", "request", "dry-run"):
            with self.subTest(reason=reason):
                api = FakeGitHub()
                api.failures[1] = 422
                if reason == "author":
                    api.pulls[1]["user"]["login"] = "outside-author"
                elif reason == "owner":
                    api.pulls[1]["base"]["repo"]["owner"]["login"] = "outside-owner"
                elif reason == "request":
                    api.pulls[1]["requested_reviewers"] = []
                self.assertEqual(self.run_batch(api, dry_run=reason == "dry-run")[0], 0)
                self.assertEqual(api.comments[1], [])
                self.assertFalse(any("/comments" in path for _, path, _ in api.calls))



if __name__ == "__main__":
    unittest.main()
