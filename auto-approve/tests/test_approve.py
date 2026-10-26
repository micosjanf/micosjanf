import io
import unittest
from contextlib import redirect_stdout

from approve import Approver
from approve_fixtures import BatchAssertions, CONFIG, HEAD, REPO, FakeGitHub, review


class ApproverTests(BatchAssertions, unittest.TestCase):
    def test_eligible_approval_binds_head_without_repository_writes(self):
        api = FakeGitHub()
        result, output = self.run_batch(api)
        self.assertEqual(result, 0)
        self.assertEqual(api.post_attempts, [1])
        self.assertEqual(api.calls[-1], ("POST", f"/repos/{REPO}/pulls/1/reviews", {"commit_id": HEAD, "event": "APPROVE"}))
        self.assertEqual(output[-1]["approved"], 1)


    def test_author_and_base_owner_must_both_be_allowed(self):
        for field in ("author", "owner"):
            with self.subTest(field=field):
                api = FakeGitHub()
                if field == "author":
                    api.pulls[1]["user"]["login"] = "outside-author"
                else:
                    api.pulls[1]["base"]["repo"]["owner"]["login"] = "outside-owner"
                    api.pulls[1]["head"]["repo"]["owner"]["login"] = "allowed-owner"
                self.assertEqual(self.run_batch(api)[0], 0)
                self.assertEqual(api.post_attempts, [])


    def test_team_only_request_does_not_qualify(self):
        api = FakeGitHub()
        api.pulls[1]["requested_reviewers"] = []
        api.pulls[1]["requested_teams"] = [{"slug": "reviewers"}]
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [])


    def test_existing_approval_at_current_head_skips_even_without_marker(self):
        api = FakeGitHub()
        api.reviews[1] = [review(login="Micosjanf")]
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [])


    def test_other_reviewer_old_head_or_dismissed_review_does_not_suppress_approval(self):
        api = FakeGitHub()
        api.reviews[1] = [review(login="another-user"), review(sha="b" * 40), review(state="DISMISSED")]
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [1])


    def test_existing_approval_on_second_review_page_is_found(self):
        api = FakeGitHub()
        api.reviews[1] = [review(number=number, login="another-user") for number in range(100)] + [review()]
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [])
        self.assertTrue(any("page=2" in path for _, path, _ in api.calls))


    def test_lost_post_response_is_not_duplicated_on_next_run(self):
        api = FakeGitHub(2)
        api.lost_responses.add(1)
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [1, 2])
        api.pulls[1]["requested_reviewers"] = [{"login": "micosjanf"}]
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [1, 2])
        self.assertEqual(len(api.reviews[1]), 1)


    def test_fresh_get_rechecks_head_and_eligibility_before_post(self):
        for change in ("head", "request", "author", "owner", "closed", "merged", "missing", "repository"):
            with self.subTest(change=change):
                api = FakeGitHub()
                def mutate(number):
                    current = api.pulls[number]
                    if change == "head":
                        current["head"]["sha"] = "b" * 40
                    elif change == "request":
                        current["requested_reviewers"] = []
                    elif change == "author":
                        current["user"]["login"] = "outside-author"
                    elif change == "owner":
                        current["base"]["repo"]["owner"]["login"] = "outside-owner"
                    elif change == "closed":
                        current["state"] = "closed"
                    elif change == "merged":
                        current["merged"] = True
                    elif change == "missing":
                        del api.pulls[number]
                    else:
                        current["base"]["repo"]["full_name"] = "another-owner/example"
                api.after_reviews = mutate
                self.assertEqual(self.run_batch(api)[0], 0)
                self.assertEqual(api.post_attempts, [])


    def test_dry_run_performs_no_post(self):
        api = FakeGitHub()
        result, output = self.run_batch(api, dry_run=True)
        self.assertEqual(result, 0)
        self.assertEqual(api.post_attempts, [])
        self.assertEqual(output[0]["would_approve"], REPO)


    def test_public_and_private_prs_are_approved_without_contents_calls(self):
        api = FakeGitHub(2)
        api.pulls[1]["base"]["repo"]["private"] = True
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(api.post_attempts, [1, 2])
        self.assertEqual(sum(len(items) for items in api.reviews.values()), 2)


    def test_unconfirmed_response_is_skipped_and_continues_other_prs(self):
        for response in (review(state="PENDING"), review(sha="b" * 40), review(login="another-user")):
            with self.subTest(response=response):
                api = FakeGitHub(2)
                api.responses[1] = response
                self.assertEqual(self.run_batch(api)[0], 0)
                self.assertEqual(api.post_attempts, [1, 2])


    def test_search_completeness_is_required_before_approving(self):
        for incomplete, total in ((True, 1), (False, 1001)):
            with self.subTest(incomplete=incomplete, total=total):
                api = FakeGitHub()
                api.incomplete, api.total_count = incomplete, total
                with self.assertRaisesRegex(RuntimeError, "search is incomplete"), redirect_stdout(io.StringIO()):
                    Approver(api, CONFIG).run()
                self.assertEqual(api.post_attempts, [])


    def test_search_pagination_and_duplicate_candidates(self):
        api = FakeGitHub(101)
        api.candidate_numbers.append(1)
        self.assertEqual(self.run_batch(api)[0], 0)
        self.assertEqual(len(api.post_attempts), 101)
        self.assertEqual(len(set(api.post_attempts)), 101)


    def test_wrong_token_identity_is_rejected_before_approving(self):
        api = FakeGitHub()
        api.login = "samzong"
        with self.assertRaisesRegex(ValueError, "must belong to micosjanf"):
            Approver(api, CONFIG).run()
        self.assertEqual(api.post_attempts, [])


if __name__ == "__main__":
    unittest.main()
