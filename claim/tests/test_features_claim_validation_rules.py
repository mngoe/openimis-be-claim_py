"""
Tests for backend/features/claim-validation-rules.feature (Redmine CSU Evolution issues).

`build_coverage` builds a complete, self-contained coverage set (location tree, HF with price
lists, insuree, programme, product, product service, policy) because the test databases hold no
demo data. It is reused by the other test_features_* files of this area owner.
"""
import datetime as py_datetime
import unittest
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import TestCase

from claim.models import Claim, ClaimService, ClaimDetail
from claim.test_helpers import create_test_claim
from claim.validations import (
    validate_claim,
    validate_assign_prod_to_claimitems_and_services,
    process_dedrem,
)
from core.test_helpers import create_security_tree, create_test_interactive_user
from insuree.test_helpers import create_test_insuree
from medical.models import Diagnosis
from medical.test_helpers import create_test_service
from policy.models import Policy
from product.models import ProductItemOrService
from product.test_helpers import create_test_product, create_test_product_service
from program.test_helpers import create_test_program

from claim.services import update_or_create_claim as _svc_update_or_create_claim


def update_or_create_claim(data, user):
    """As gql_mutations.update_or_create_claim calls the service: with the claim's program
    (dev-csu signature) and in one transaction, so a refused claim is rolled back."""
    from django.db import transaction
    with transaction.atomic():
        return _svc_update_or_create_claim(data, user, data.get("program"))


class AuditUser:
    """Minimal user for the service layer (id_for_audit only)."""
    def __init__(self, audit_id=-1):
        self.id_for_audit = audit_id


def existing_diagnosis():
    # the cloned test DBs have a lagging tblICDCodes sequence: reuse a diagnosis
    return Diagnosis.objects.filter(validity_to__isnull=True).first()


def build_coverage(prefix, service_category="O", service_props=None, product_props=None,
                   policy_props=None, program_code=None, program_name=None):
    """A covered insuree at HF1 of a fresh location tree. `prefix` max 4 chars."""
    from location.models import HealthFacility
    from medical_pricelist.models import (
        ServicesPricelist, ServicesPricelistDetail, ItemsPricelist)

    t = create_security_tree(prefix)
    spl = ServicesPricelist.objects.create(
        name=f"{prefix} services", location=t.d1, pricelist_date="2019-01-01",
        validity_from="2019-01-01", audit_user_id=-1)
    ipl = ItemsPricelist.objects.create(
        name=f"{prefix} items", location=t.d1, pricelist_date="2019-01-01",
        validity_from="2019-01-01", audit_user_id=-1)
    HealthFacility.objects.filter(id=t.hf1.id).update(services_pricelist=spl, items_pricelist=ipl)
    t.hf1.refresh_from_db()

    program = create_test_program(code=program_code or f"{prefix}P",
                                  name=program_name or f"{prefix} programme")
    insuree = create_test_insuree(
        is_head=True,
        custom_props={"current_village": t.v1, "health_facility": t.hf1, "dob": "1990-01-01"},
        family_custom_props={"location": t.v1})
    product = create_test_product(f"{prefix}PR", custom_props={"program": program, **(product_props or {})})
    service = create_test_service(service_category, custom_props={
        "code": f"{prefix}S1", "name": f"{prefix} service", "price": 1000,
        "program": program, "validity_from": "2019-01-01", **(service_props or {})})
    product_service = create_test_product_service(product, service, custom_props={
        "waiting_period_adult": 0, "waiting_period_child": 0})
    ServicesPricelistDetail.objects.create(
        services_pricelist=spl, service=service, validity_from="2019-01-01", audit_user_id=-1)
    policy = create_test_policy_quiet(product, insuree, policy_props)
    return SimpleNamespace(tree=t, hf=t.hf1, program=program, insuree=insuree, product=product,
                           service=service, product_service=product_service, policy=policy,
                           icd=existing_diagnosis())


def create_test_policy_quiet(product, insuree, custom_props=None):
    from policy.test_helpers import create_test_policy
    policy = create_test_policy(product, insuree, link=True, custom_props=custom_props)
    # policy_values may recompute dates/status: force the requested values back
    if custom_props:
        Policy.objects.filter(id=policy.id).update(**custom_props)
        policy.refresh_from_db()
    return policy


def covered_claim(cov, code, date="2024-06-03", qty=1, price=1000, status=Claim.STATUS_ENTERED,
                  service_props=None):
    d = py_datetime.date.fromisoformat(date)
    claim = create_test_claim(custom_props={
        "code": code, "health_facility_id": cov.hf.id, "insuree_id": cov.insuree.id,
        "icd_id": cov.icd.id, "program": cov.program, "date_from": d, "date_to": d,
        "date_claimed": d, "status": status,
    }, user=AuditUser())
    ClaimService.objects.create(
        claim=claim, service=cov.service, qty_provided=qty, price_asked=price,
        status=ClaimDetail.STATUS_PASSED, audit_user_id=-1, validity_from=d,
        **(service_props or {}))
    return claim


def service_input(service, qty, price):
    return {"service_id": service.id, "status": 1, "qty_provided": Decimal(qty),
            "price_asked": Decimal(price)}


def claim_input(cov, code, services, **extra):
    return {
        "code": code, "health_facility_id": cov.hf.id, "insuree_id": cov.insuree.id,
        "icd_id": cov.icd.id, "program": cov.program,
        "date_from": py_datetime.date(2024, 6, 3), "date_to": py_datetime.date(2024, 6, 3),
        "date_claimed": py_datetime.date(2024, 6, 3), "status": Claim.STATUS_ENTERED,
        "services": services, **extra,
    }


def service_user(username):
    """An interactive user (superuser-like test user) for the service layer."""
    return create_test_interactive_user(username=username)


# ---------------------------------------------------------------------------------------
# Rule: Amounts are never negative
# ---------------------------------------------------------------------------------------
class NegativeAmountsTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cov = build_coverage("FVNA")
        cls.user = service_user("fvna_user")

    def _create(self, code, qty, price):
        return update_or_create_claim(
            claim_input(self.cov, code, [service_input(self.cov.service, qty, price)]), self.user)

    # Scenario: The backend refuses negative quantities and prices (@redmine-37923)
    def test_negative_quantity_refused_at_creation(self):
        with self.assertRaises(ValidationError):
            self._create("FVNA-QTYNEG", -1, 1000)
        self.assertFalse(Claim.objects.filter(code="FVNA-QTYNEG").exists(),
                         "a claim with a negative quantity was saved")

    # Scenario: The backend refuses negative quantities and prices (@redmine-37923)
    def test_negative_price_refused_at_creation(self):
        with self.assertRaises(ValidationError):
            self._create("FVNA-PRCNEG", 1, -1000)
        self.assertFalse(Claim.objects.filter(code="FVNA-PRCNEG").exists(),
                         "a claim with a negative price was saved")

    # Scenario: The backend refuses negative quantities and prices (@redmine-37923)
    def test_zero_quantity_and_price_allowed(self):
        self._create("FVNA-QTYZERO", 0, 1000)
        self._create("FVNA-PRCZERO", 1, 0)
        self.assertTrue(Claim.objects.filter(code="FVNA-QTYZERO").exists())
        self.assertTrue(Claim.objects.filter(code="FVNA-PRCZERO").exists())

    # Scenario: The backend refuses negative quantities and prices (@redmine-37923)
    # Scenario: A refused update keeps the previous claim lines valid (@redmine-37923)
    def test_negative_quantity_refused_at_update_and_old_lines_kept(self):
        claim = self._create("FVNA-UPD", 1, 1000)
        data = claim_input(self.cov, "FVNA-UPD", [service_input(self.cov.service, -2, 1000)],
                           uuid=claim.uuid)
        with self.assertRaises(ValidationError):
            update_or_create_claim(data, self.user)
        claim.refresh_from_db()
        lines = ClaimService.objects.filter(claim=claim, validity_to__isnull=True)
        self.assertEqual([int(s.qty_provided) for s in lines], [1],
                         "the refused update did not keep the previous valid service line")


# ---------------------------------------------------------------------------------------
# Rule: Coverage checks
# ---------------------------------------------------------------------------------------
class CoverageChecksTest(TestCase):

    def _submit_errors(self, claim):
        errors = validate_claim(claim, True)
        if not errors:
            errors = validate_assign_prod_to_claimitems_and_services(claim)
        return errors

    # Scenario: The policy record date does not reject a covered claim with code 21 (@redmine-33628)
    def test_policy_record_date_does_not_reject_with_21(self):
        cov = build_coverage("FV21", policy_props={
            "enroll_date": py_datetime.date(2023, 10, 12),
            "start_date": py_datetime.date(2023, 10, 12),
            "effective_date": py_datetime.date(2023, 10, 12),
            "validity_from": py_datetime.datetime(2024, 1, 12, 12, 38, 7)})
        cov.policy.insuree_policies.update(effective_date=py_datetime.date(2023, 10, 12))
        claim = covered_claim(cov, "0008581", date="2023-10-16")
        errors = self._submit_errors(claim)
        codes = [e.get("code") for e in errors]
        self.assertNotIn(21, codes, errors)
        line = claim.services.filter(validity_to__isnull=True).first()
        self.assertNotEqual(line.rejection_reason, 21)
        self.assertEqual(errors, [], "the covered claim was rejected")

    # Scenario: Only active or expired policies cover a claim (@redmine-35240)
    def test_active_and_expired_policies_cover(self):
        for prefix, status in (("FVSA", Policy.STATUS_ACTIVE), ("FVSE", Policy.STATUS_EXPIRED)):
            with self.subTest(status=status):
                cov = build_coverage(prefix, policy_props={"status": status})
                claim = covered_claim(cov, f"{prefix}-CL")
                self.assertEqual(self._submit_errors(claim), [])

    # Scenario: Only active or expired policies cover a claim (@redmine-35240 @redmine-38222)
    def test_suspended_policy_rejects_with_code_3(self):
        cov = build_coverage("FVSS", policy_props={"status": Policy.STATUS_SUSPENDED})
        claim = covered_claim(cov, "FVSS-CL")
        self.assertNotEqual(self._submit_errors(claim), [])
        line = claim.services.filter(validity_to__isnull=True).first()
        line.refresh_from_db()
        self.assertEqual(line.rejection_reason, 3)

    # Scenario: Claims inside the period of a deleted policy still validate (@redmine-34059)
    @unittest.skip("postponed: #34059")
    def test_deleted_policy_period_still_validates(self):
        cov = build_coverage("FVDP")
        Policy.objects.filter(id=cov.policy.id).update(validity_to=py_datetime.datetime(2025, 1, 1))
        cov.policy.insuree_policies.update(validity_to=py_datetime.datetime(2025, 1, 1))
        claim = covered_claim(cov, "FVDP-CL", date="2024-06-03")
        self.assertEqual(self._submit_errors(claim), [])

    # Scenario: A second Chèque Santé of the same MPI is not rejected for frequency (@redmine-34052)
    @unittest.skip("open: #34052")
    def test_second_cheque_not_rejected_for_frequency(self):
        pass

    # Scenario: The insuree, programme and policy of a claim must match at save (@redmine-37899)
    @unittest.skip("open: #37899")
    def test_conformity_check_at_save(self):
        pass

    # Scenario: No product valid at the claim date gives an explicit error (@redmine-36030)
    def test_no_valid_product_gives_explicit_error(self):
        cov = build_coverage("FVNP")
        claim = covered_claim(cov, "FVNP-CL", date="2024-06-03")
        self.assertEqual(self._submit_errors(claim), [])
        # the product now starts after the claim's target date
        type(cov.product).objects.filter(id=cov.product.id).update(
            validity_from=py_datetime.datetime(2024, 8, 1))
        try:
            process_dedrem(claim, -1, False)
        except AttributeError as exc:
            self.fail(f"crash instead of an explicit error: {exc}")
        except (ValueError, ValidationError) as exc:
            self.assertIn(str(cov.product.id), str(exc))
            return
        except Exception as exc:
            self.fail(f"no explicit error naming the product, got {type(exc).__name__}: {exc}")
        self.fail("no error when no product is valid at the claim's target date")

    # Scenario: A product without threshold does not crash claim processing (@redmine-37774)
    def test_null_threshold_does_not_crash(self):
        cov = build_coverage("FVTH", product_props={"threshold": None, "max_members": 4,
                                                   "max_policy_extra_member": 10})
        claim = covered_claim(cov, "FVTH-CL")
        self.assertEqual(self._submit_errors(claim), [])
        errors = process_dedrem(claim, -1, False)
        self.assertEqual(errors, [])

    # Scenario: A worker crash does not take down the other workers (@redmine-37908)
    @unittest.skip("postponed: #37908 (worker supervision, not unit testable)")
    def test_worker_crash_isolated(self):
        pass


# ---------------------------------------------------------------------------------------
# Rule: Ceiling of package type F services
# ---------------------------------------------------------------------------------------
class PackageFCeilingTest(TestCase):

    # Scenario: The adjusted price of an F service is capped at the service price (@redmine-35257)
    def test_f_service_price_capped(self):
        from claim.apps import ClaimConfig
        cov = build_coverage("FVPF", service_props={"price": 48200, "packagetype": "F"})
        cov.product_service.price_origin = ProductItemOrService.ORIGIN_CLAIM
        cov.product_service.save()
        claim = covered_claim(cov, "FVPF-CL", qty=1, price=137935,
                              service_props={"price_origin": ProductItemOrService.ORIGIN_CLAIM})
        errors = validate_claim(claim, True)
        if not errors:
            errors = validate_assign_prod_to_claimitems_and_services(claim)
        self.assertEqual(errors, [])
        old = ClaimConfig.native_code_for_services
        ClaimConfig.native_code_for_services = False
        try:
            errors = process_dedrem(claim, -1, True)
        finally:
            ClaimConfig.native_code_for_services = old
        self.assertEqual(errors, [])
        line = claim.services.filter(validity_to__isnull=True).first()
        line.refresh_from_db()
        self.assertEqual(line.price_adjusted, Decimal("48200"))
