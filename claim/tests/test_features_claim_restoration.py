"""Tests for backend/features/claim-restoration.feature (Redmine CSU Evolution issues)."""
import json

from django.core.exceptions import ValidationError
from django.test import TestCase

from claim.models import Claim
from claim.services import update_or_create_claim as _svc_update_or_create_claim
from claim.tests.test_features_claim_validation_rules import (
    build_coverage, covered_claim, claim_input, service_input, service_user,
)
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import get_test_token
from policy.models import Policy


def update_or_create_claim(data, user):
    """dev-csu: the service takes the claim's program explicitly (gql_mutations resolves it)."""
    return _svc_update_or_create_claim(data, user, data.get("program"))

STATUS_REINITIALIZED = 32


class RestoreLinkTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cov = build_coverage("FRRL")
        cls.user = service_user("frrl_user")

    def _restore(self, original, code):
        data = claim_input(self.cov, code, [service_input(self.cov.service, 1, 1000)],
                           restore=original.uuid)
        return update_or_create_claim(data, self.user)

    # Scenario: Restoring a rejected claim creates a linked entered claim (@redmine-27734)
    def test_restore_rejected_claim_creates_linked_entered_claim(self):
        original = covered_claim(self.cov, "FRRL-1", status=Claim.STATUS_REJECTED)
        new = self._restore(original, "FRRL-1R")
        new.refresh_from_db()
        self.assertEqual(new.status, Claim.STATUS_ENTERED)
        self.assertEqual(new.restore_id, original.id)

    # Scenario: Only a rejected claim can be restored (@redmine-27734)
    def test_only_rejected_claim_can_be_restored(self):
        for status in (Claim.STATUS_ENTERED, Claim.STATUS_CHECKED, Claim.STATUS_VALUATED):
            with self.subTest(status=status):
                original = covered_claim(self.cov, f"FRRL-S{status}", status=status)
                with self.assertRaises(ValidationError) as cm:
                    self._restore(original, f"FRRL-S{status}R")
                self.assertIn("cannot_restore_not_rejected_claim", str(cm.exception))

    # Scenario: Saving a restored claim moves the original to status 32 (@redmine-35073 @redmine-35116)
    def test_restored_original_moves_to_status_32(self):
        original = covered_claim(self.cov, "FRRL-32", status=Claim.STATUS_REJECTED)
        self._restore(original, "FRRL-32R")
        original.refresh_from_db()
        self.assertEqual(original.status, STATUS_REINITIALIZED,
                         "the restored original stayed in its previous status")
        self.assertFalse(Claim.objects.filter(id=original.id, status=Claim.STATUS_REJECTED).exists())
        self.assertEqual(getattr(Claim, "STATUS_REINITIALIZED", None), STATUS_REINITIALIZED)

    # Scenario: A claim is saved for an insuree whose Chèque Santé policy is suspended (@redmine-35401)
    def test_claim_saved_for_suspended_policy(self):
        cov = build_coverage("FRSU", policy_props={"status": Policy.STATUS_SUSPENDED})
        claim = update_or_create_claim(
            claim_input(cov, "FRSU-CL", [service_input(cov.service, 1, 1000)]), self.user)
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.STATUS_ENTERED)


class ReinitializedSearchTest(openIMISGraphQLTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cov = build_coverage("FRSE")
        cls.admin = service_user("frse_admin")
        cls.archived = covered_claim(cls.cov, "FRSE-ARCH", status=STATUS_REINITIALIZED)
        cls.rejected = covered_claim(cls.cov, "FRSE-REJ", status=Claim.STATUS_REJECTED)

    # Scenario: The claim search filters on status 32 (@redmine-35073 @redmine-35115)
    def test_search_on_status_32(self):
        response = self.query(
            'query { claims(status: 32, code_Istartswith: "FRSE-") { edges { node { code status } } } }',
            headers={"HTTP_AUTHORIZATION": f"Bearer {get_test_token(self.admin)}"})
        content = json.loads(response.content)
        self.assertIsNone(content.get("errors"), content.get("errors"))
        codes = {e["node"]["code"] for e in content["data"]["claims"]["edges"]}
        self.assertEqual(codes, {"FRSE-ARCH"})
