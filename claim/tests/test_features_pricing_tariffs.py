"""
Feature tests for features/pricing-tariffs.feature (backend scenarios, module claim).
"""
import datetime

from django.test import TestCase

from claim.models import Claim, ClaimService
from claim.test_helpers import create_test_claim
from claim.validations import validate_claimservice_validity, REJECTION_REASON_TARGET_DATE
from core.test_helpers import create_security_tree
from insuree.test_helpers import create_test_insuree
from medical.models import Diagnosis
from medical.test_helpers import create_test_service, create_test_diagnosis
from program.test_helpers import create_test_program


class PricingTariffsClaimFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.t = create_security_tree("FPC")
        cls.program = create_test_program(code="FPCP", name="FPC program")
        cls.icd = Diagnosis.objects.filter(validity_to__isnull=True).first() \
            or create_test_diagnosis({"code": "FPCD"})
        cls.insuree = create_test_insuree(
            custom_props={"current_village": cls.t.v1, "health_facility": cls.t.hf1},
            family_custom_props={"location": cls.t.v1})

    def _check(self, code, service_start):
        service = create_test_service("A", custom_props={
            "code": code, "program": self.program, "validity_from": service_start})
        target = datetime.datetime(2024, 3, 15)
        claim = create_test_claim(custom_props={
            "code": code + "-CL", "health_facility_id": self.t.hf1.id, "insuree_id": self.insuree.id,
            "icd_id": self.icd.id, "program": self.program,
            "date_from": target, "date_to": target, "date_claimed": target})
        line = ClaimService.objects.create(
            claim=claim, service=service, qty_provided=1, price_asked=100, status=1,
            validity_from="2019-06-01", audit_user_id=-1)
        # read back as the validation sees them in production (core DateTimeField values)
        claim = Claim.objects.get(pk=claim.pk)
        line = ClaimService.objects.select_related("service").get(pk=line.pk)
        errors = validate_claimservice_validity(claim, line)
        return line, errors

    # Scenario: Claim line rejected with code 9 when the service starts after the claim date (@redmine-38095)
    def test_service_started_before_claim_date_is_accepted(self):
        line, errors = self._check("FPC-UVIH10A", datetime.datetime(2023, 1, 1))
        self.assertEqual(errors, [])
        self.assertIn(line.rejection_reason, (None, 0))

    # Scenario: Claim line rejected with code 9 when the service starts after the claim date (@redmine-38095)
    def test_service_starting_after_claim_date_is_rejected_code_9(self):
        line, errors = self._check("FPC-UVIH10B", datetime.datetime(2025, 6, 20))
        self.assertEqual(line.rejection_reason, REJECTION_REASON_TARGET_DATE)
        self.assertEqual([e["code"] for e in errors], [REJECTION_REASON_TARGET_DATE])
