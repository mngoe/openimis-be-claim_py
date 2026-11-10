"""
Processing guard on the review status (Redmine #35620 / #35928).

Feature (features/claim-review-processing.feature):
  Rule: only a claim whose review is Delivered (8) or Bypassed (16) can be processed.
  A claim with any other review status is skipped: it keeps its status and an error
  is returned for it; it is not rejected.
"""
import uuid

from django.core.cache import cache
from django.test import TestCase

from claim.gql_mutations import ProcessClaimsMutation
from claim.models import Claim, ClaimService
from claim.test_helpers import create_test_claim
from core.models import MutationLog
from core.test_helpers import create_test_interactive_user
from medical.test_helpers import create_test_service

GUARD_MESSAGE = "claim.review.notin.done.nor.bypassed.status"


class ProcessClaimsReviewGuardTest(TestCase):

    @classmethod
    def setUpTestData(cls):
        # default roles of the test helper: IMIS administrator, holds the process right
        cls.user = create_test_interactive_user(username="process_review_guard")
        cls.service = create_test_service("V", custom_props={"code": "PRG-SV"})
        # the helper creates a diagnosis, a health facility and an insuree with fixed codes:
        # create them once and reuse them for every claim of the test
        base = create_test_claim(custom_props={"code": "PRG-BASE"})
        cls.shared = {"icd_id": base.icd_id, "health_facility_id": base.health_facility_id,
                      "insuree_id": base.insuree_id}

    def setUp(self):
        cache.clear()

    def _checked_claim(self, review_status):
        claim = create_test_claim(custom_props={"code": "PRG-" + uuid.uuid4().hex[:8], **self.shared})
        claim.status = Claim.STATUS_CHECKED
        claim.review_status = review_status
        claim.claimed = 100
        claim.approved = 100
        claim.save()
        ClaimService.objects.create(
            claim=claim, service=self.service, qty_provided=1, price_asked=100,
            status=ClaimService.STATUS_PASSED, audit_user_id=-1)
        return claim

    def _process(self, claim):
        client_mutation_id = str(uuid.uuid4())
        MutationLog.objects.create(json_content="{}", client_mutation_id=client_mutation_id,
                                   user=self.user)
        return ProcessClaimsMutation.async_mutate(
            self.user, uuids=[str(claim.uuid)], client_mutation_id=client_mutation_id)

    @staticmethod
    def _guard_errors(errors):
        if isinstance(errors, dict):
            errors = [errors]
        return [e for e in errors or [] for m in e.get("list", [e])
                if m.get("message") == GUARD_MESSAGE]

    # Scenario: Processing refuses a claim whose review is neither Delivered nor Bypassed
    def test_processing_skips_claim_not_reviewed(self):
        for review_status in (Claim.REVIEW_IDLE, Claim.REVIEW_SELECTED):
            with self.subTest(review_status=review_status):
                claim = self._checked_claim(review_status)
                errors = self._process(claim)
                claim.refresh_from_db()
                self.assertEqual(claim.status, Claim.STATUS_CHECKED,
                                 "a claim not reviewed must keep its status (not processed, not rejected)")
                self.assertTrue(self._guard_errors(errors), errors)

    # Scenario: Processing accepts a claim whose review is Delivered or Bypassed
    def test_processing_accepts_reviewed_claim(self):
        for review_status in (Claim.REVIEW_DELIVERED, Claim.REVIEW_BYPASSED):
            with self.subTest(review_status=review_status):
                claim = self._checked_claim(review_status)
                errors = self._process(claim)
                self.assertFalse(self._guard_errors(errors), errors)
