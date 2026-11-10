"""
Feature tests for features/claim-review-processing.feature (backend scenarios, module claim).

Each test asserts the behaviour specified in the Redmine issues. A failure is a gap of the
local claim checkout against that behaviour.
"""
import json
import unittest
import uuid
from decimal import Decimal
from unittest import mock

from django.core.cache import cache

from claim import gql_mutations
from claim.apps import ClaimConfig
from claim.gql_mutations import (
    BypassClaimsReviewMutation,
    DeliverClaimsReviewMutation,
    ProcessClaimsMutation,
    SaveClaimReviewMutation,
)
from claim.models import Claim, ClaimItem, ClaimService, ClaimServiceItem, ClaimServiceService
from claim.test_helpers import create_test_claim
from claim.validations import approved_amount
from core.models import MutationLog
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import create_security_tree, create_right_only_user, get_test_token, perm_ids
from insuree.test_helpers import create_test_insuree
from medical.models import Diagnosis
from medical.test_helpers import create_test_diagnosis, create_test_item, create_test_service
from medical_pricelist.test_helpers import (
    add_item_to_hf_pricelist,
    add_service_to_hf_pricelist,
    create_test_item_pricelist,
    create_test_service_pricelist,
)
from policy.test_helpers import create_test_policy
from product.test_helpers import create_test_product, create_test_product_service

MANUAL_REJECTION = -2


class ClaimReviewProcessingFeatureTests(openIMISGraphQLTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.t = create_security_tree("FCR")
        cls.hf = cls.t.hf1
        cls.icd = Diagnosis.objects.filter(validity_to__isnull=True).first() \
            or create_test_diagnosis({"code": "FCRD"})
        cls.insuree = create_test_insuree(
            custom_props={"current_village": cls.t.v1, "health_facility": cls.hf},
            family_custom_props={"location": cls.t.v1})
        # product + policy so that a claim can be validated and processed
        cls.product = create_test_product("FCRPR")
        cls.program = cls.product.program
        cls.policy = create_test_policy(cls.product, cls.insuree, link=True)
        cls.service = create_test_service("V", custom_props={"code": "FCR-SV", "program": cls.program})
        cls.product_service = create_test_product_service(cls.product, cls.service)
        cls.hf.services_pricelist = create_test_service_pricelist(cls.t.r1.id)
        cls.hf.items_pricelist = create_test_item_pricelist(cls.t.r1.id)
        cls.hf.save()
        add_service_to_hf_pricelist(cls.service, hf_id=cls.hf.id)
        cls.item = create_test_item("D", custom_props={"code": "FCRIT", "program": cls.program})
        add_item_to_hf_pricelist(cls.item, hf_id=cls.hf.id)

        rights = perm_ids(
            ClaimConfig.gql_query_claims_perms
            + ClaimConfig.gql_mutation_create_claims_perms
            + ClaimConfig.gql_mutation_update_claims_perms
            + ClaimConfig.gql_mutation_submit_claims_perms
            + ClaimConfig.gql_mutation_process_claims_perms
            + ClaimConfig.gql_mutation_select_claim_review_perms
            + ClaimConfig.gql_mutation_bypass_claim_review_perms
            + ClaimConfig.gql_mutation_deliver_claim_review_perms
        )
        cls.user = create_right_only_user("fcruser", sorted(set(rights)), [cls.t.d1.id])

    def setUp(self):
        cache.clear()

    # ------------------------------------------------------------------ helpers
    def _claim(self, status=Claim.STATUS_CHECKED, review_status=Claim.REVIEW_IDLE, **extra):
        claim = create_test_claim(custom_props={
            "code": "FCR-" + uuid.uuid4().hex[:10],
            "health_facility_id": self.hf.id,
            "insuree_id": self.insuree.id,
            "icd_id": self.icd.id,
            "program": self.program,
            **extra,
        })
        claim.status = status
        claim.review_status = review_status
        claim.save()
        return claim

    def _line(self, claim, service=None, **extra):
        return ClaimService.objects.create(**{
            "claim": claim, "service": service or self.service, "qty_provided": 1,
            "price_asked": 100, "status": ClaimService.STATUS_PASSED,
            "validity_from": "2019-06-01", "audit_user_id": -1, **extra})

    def _item_line(self, claim, **extra):
        return ClaimItem.objects.create(**{
            "claim": claim, "item": self.item, "qty_provided": 1, "price_asked": 100,
            "status": ClaimItem.STATUS_PASSED, "availability": True,
            "validity_from": "2019-06-01", "audit_user_id": -1, **extra})

    def _valid_claim(self, review_status):
        claim = self._claim(review_status=review_status)
        self._line(claim)
        claim.claimed = 100
        claim.approved = 100
        claim.save()
        return claim

    def _mutation_id(self):
        cmid = str(uuid.uuid4())
        MutationLog.objects.create(json_content="{}", client_mutation_id=cmid, user=self.user)
        return cmid

    def _process(self, claim):
        return ProcessClaimsMutation.async_mutate(
            self.user, uuids=[str(claim.uuid)], client_mutation_id=self._mutation_id())

    def _reload(self, claim):
        return Claim.objects.get(id=claim.id)

    def _package_service(self, code, packagetype, price):
        return create_test_service("V", custom_props={
            "code": code, "packagetype": packagetype, "price": price, "program": self.program})

    def _sub_service(self, claim_service, price, qty, qty_adjusted=None, code=None):
        sub = create_test_service("V", custom_props={
            "code": code or ("FCR-SUB-" + uuid.uuid4().hex[:6]), "price": price, "program": self.program})
        return ClaimServiceService.objects.create(
            service=sub, claim_service=claim_service, qty_provided=qty, qty_displayed=qty,
            qty_adjusted=qty_adjusted, price_asked=price)

    # ------------------------------------------------------------------ review -> processed
    # Scenario: Claim moves to Processed when the review becomes Delivered (@redmine-33457)
    def test_review_delivered_moves_claim_to_processed(self):
        claim = self._valid_claim(Claim.REVIEW_SELECTED)
        DeliverClaimsReviewMutation.async_mutate(self.user, uuids=[str(claim.uuid)])
        self.assertEqual(self._reload(claim).review_status, Claim.REVIEW_DELIVERED)
        self.assertEqual(self._reload(claim).status, Claim.STATUS_PROCESSED)

    # Scenario: Claim moves to Processed when the review becomes Bypassed (@redmine-33457)
    def test_review_bypassed_moves_claim_to_processed(self):
        claim = self._valid_claim(Claim.REVIEW_SELECTED)
        BypassClaimsReviewMutation.async_mutate(self.user, uuids=[str(claim.uuid)])
        self.assertEqual(self._reload(claim).review_status, Claim.REVIEW_BYPASSED)
        self.assertEqual(self._reload(claim).status, Claim.STATUS_PROCESSED)

    # ------------------------------------------------------------------ processing guard
    # Scenario: Processing refuses a claim whose review is Not selected / Selected (@redmine-35620)
    def test_processing_refuses_claim_not_reviewed(self):
        for review in (Claim.REVIEW_IDLE, Claim.REVIEW_SELECTED):
            with self.subTest(review_status=review):
                claim = self._valid_claim(review)
                errors = self._process(claim)
                self.assertEqual(self._reload(claim).status, Claim.STATUS_CHECKED,
                                 "a claim whose review is neither delivered nor bypassed was processed or rejected")
                self.assertTrue(errors, "no error returned for a claim skipped by the review guard")

    # Scenario: Processing accepts a claim whose review is Delivered / Bypassed (@redmine-35620)
    def test_processing_accepts_reviewed_claim(self):
        for review in (Claim.REVIEW_DELIVERED, Claim.REVIEW_BYPASSED):
            with self.subTest(review_status=review):
                claim = self._valid_claim(review)
                errors = self._process(claim)
                self.assertIn(self._reload(claim).status,
                              (Claim.STATUS_PROCESSED, Claim.STATUS_VALUATED), errors)

    # ------------------------------------------------------------------ GD rejection -2
    def _reject(self, claim, explanation):
        mutation = getattr(gql_mutations, "RejectClaimsMutation", None)
        if mutation is None:
            self.fail("GAP: no RejectClaimsMutation (rejectClaims) in the claim module")
        return mutation.async_mutate(self.user, uuids=[str(claim.uuid)], explanation=explanation,
                                     client_mutation_id=self._mutation_id())

    # Scenario: District Manager rejects entered claims with code -2 (@redmine-33141 @redmine-33144)
    def test_district_manager_rejects_with_code_minus_2(self):
        claim = self._claim(status=Claim.STATUS_ENTERED)
        service_line = self._line(claim)
        item_line = self._item_line(claim)
        self._reject(claim, "Rejet par le GD pour fraude")
        claim = self._reload(claim)
        self.assertEqual(claim.status, Claim.STATUS_REJECTED)
        self.assertEqual(claim.explanation, "Rejet par le GD pour fraude")
        service_line.refresh_from_db()
        item_line.refresh_from_db()
        self.assertEqual((service_line.status, service_line.rejection_reason),
                         (ClaimService.STATUS_REJECTED, MANUAL_REJECTION))
        self.assertEqual((item_line.status, item_line.rejection_reason),
                         (ClaimItem.STATUS_REJECTED, MANUAL_REJECTION))

    # Scenario: Rejection explanation is mandatory and at most 280 characters (@redmine-33140)
    def test_rejection_explanation_mandatory_and_max_280(self):
        for length, accepted in ((0, False), (280, True), (281, False)):
            with self.subTest(length=length):
                claim = self._claim(status=Claim.STATUS_ENTERED)
                self._line(claim)
                self._reject(claim, "x" * length)
                rejected = self._reload(claim).status == Claim.STATUS_REJECTED
                self.assertEqual(rejected, accepted,
                                 f"explanation of {length} characters: rejected={rejected}")

    # ------------------------------------------------------------------ rejection-code filter
    # Scenario: Claims query filtered by line rejection code (service / item) (@redmine-31546)
    def test_claims_filtered_by_rejection_code(self):
        for line in ("service", "item"):
            with self.subTest(line=line):
                prefix = "FCR-RJ" + line[0].upper()
                claim_a = self._claim(code=prefix + "-A")
                claim_b = self._claim(code=prefix + "-B")
                if line == "service":
                    self._line(claim_a, status=ClaimService.STATUS_REJECTED, rejection_reason=2)
                    self._line(claim_b)
                else:
                    self._item_line(claim_a, status=ClaimItem.STATUS_REJECTED, rejection_reason=2)
                    self._item_line(claim_b)
                content = json.loads(self.query(
                    'query { claims(rejectionCode: 2, code_Istartswith: "%s") '
                    '{ edges { node { code } } } }' % prefix,
                    headers={"HTTP_AUTHORIZATION": f"Bearer {get_test_token(self.user)}"}).content)
                self.assertIsNone(content.get("errors"), content.get("errors"))
                codes = {e["node"]["code"] for e in content["data"]["claims"]["edges"]}
                self.assertEqual(codes, {claim_a.code})

    # ------------------------------------------------------------------ approved amount
    # Scenario: Approved amount of a package includes its sub-services and sub-items (@redmine-33829)
    def test_package_approved_amount_includes_sub_lines(self):
        claim = self._claim()
        cs = self._line(claim, service=self._package_service("FCR-PK1", "P", 4000), price_asked=0)
        self._sub_service(cs, 3000, 1)
        ClaimServiceItem.objects.create(item=self.item, claim_service=cs, qty_provided=2,
                                        qty_displayed=2, price_asked=500)
        self.assertEqual(approved_amount(claim), Decimal(4000))

    # Scenario: Package price is not counted twice (@redmine-35245)
    def test_package_price_not_counted_twice(self):
        claim = self._claim()
        cs = self._line(claim, service=self._package_service("FCR-ECHO", "P", 7000), price_asked=7000)
        self._sub_service(cs, 7000, 1)
        self.assertEqual(approved_amount(claim), Decimal(7000))

    # Scenario: Ceiling (plafond) service approved amount is capped at the ceiling (@redmine-34744 @redmine-35741)
    def test_ceiling_service_approved_amount_capped(self):
        for sub_prices, expected in (((4000, 2500), 4000), ((2500, 1500), 4000)):
            with self.subTest(sub_total=sum(sub_prices)):
                claim = self._claim()
                cs = self._line(claim, service=self._package_service(
                    "FCR-PCS11-" + uuid.uuid4().hex[:4], "F", 4000), price_asked=0)
                for price in sub_prices:
                    self._sub_service(cs, price, 1)
                self.assertEqual(approved_amount(claim), Decimal(expected))

    # Scenario: Approved amount uses the adjusted quantity when present (@redmine-38099)
    def test_approved_amount_uses_adjusted_quantity(self):
        for qty_adjusted, expected in ((None, 3000), (2, 2000)):
            with self.subTest(qty_adjusted=qty_adjusted):
                claim = self._claim()
                cs = self._line(claim, service=self._package_service(
                    "FCR-PKA-" + uuid.uuid4().hex[:4], "P", 5000), price_asked=0)
                self._sub_service(cs, 1000, 3, qty_adjusted=qty_adjusted)
                self.assertEqual(approved_amount(claim), Decimal(expected))

    # Scenario: Approved amount never exceeds the claimed amount (@redmine-36436)
    @unittest.skip("open: #36436")
    def test_approved_not_above_claimed(self):
        pass

    # ------------------------------------------------------------------ save review
    # Scenario: Saving the review stores adjusted sub-service quantities and recalculates the approved amount (@redmine-33732)
    def test_save_review_adjusted_sub_service_quantity(self):
        claim = self._claim()
        cs = self._line(claim, service=self._package_service("FCR-PKR", "P", 5000), price_asked=0)
        sub = self._sub_service(cs, 1000, 3, code="FCR-UCS37")
        with mock.patch.object(ClaimConfig, "native_code_for_services", False):
            errors = SaveClaimReviewMutation.async_mutate(self.user, claim_uuid=str(claim.uuid), services=[{
                "id": cs.id, "status": ClaimService.STATUS_PASSED,
                "service_service_set": [{"sub_service_code": "FCR-UCS37", "qty_asked": 3,
                                         "price_asked": 1000, "qty_adjusted": 2}],
            }])
        self.assertFalse(errors, errors)
        sub.refresh_from_db()
        self.assertEqual(sub.qty_adjusted, 2)
        self.assertEqual(self._reload(claim).approved, Decimal(2000))

    # Scenario: Saving the review of a verified claim keeps the claimed amount (@redmine-35633)
    def test_save_review_keeps_claimed(self):
        claim = self._claim(status=Claim.STATUS_CHECKED)
        first = self._line(claim, price_asked=125)
        second = self._line(claim, price_asked=15000)
        claim.claimed = 15125
        claim.save()
        with mock.patch.object(ClaimConfig, "native_code_for_services", False):
            errors = SaveClaimReviewMutation.async_mutate(self.user, claim_uuid=str(claim.uuid), services=[
                {"id": first.id, "status": ClaimService.STATUS_REJECTED},
                {"id": second.id, "status": ClaimService.STATUS_PASSED},
            ])
        self.assertFalse(errors, errors)
        claim = self._reload(claim)
        self.assertEqual(claim.claimed, Decimal(15125))
        self.assertEqual(claim.approved, Decimal(15000))
