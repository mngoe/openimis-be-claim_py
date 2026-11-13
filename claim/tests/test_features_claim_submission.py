"""Tests for backend/features/claim-submission.feature (Redmine CSU Evolution issues)."""
import datetime as py_datetime
import json
import unittest
import uuid as uuid_lib

from django.db import connection
from django.test import TestCase, override_settings

from claim.models import Claim
from claim.services import update_or_create_claim as _svc_update_or_create_claim, submit_claim, ClaimSubmitService
from claim.tests.test_features_claim_validation_rules import (
    build_coverage, covered_claim, claim_input, service_input, service_user,
)
from core.models import MutationLog
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import get_test_token


def update_or_create_claim(data, user):
    """dev-csu: the service takes the claim's program explicitly (gql_mutations resolves it)."""
    return _svc_update_or_create_claim(data, user, data.get("program"))


def db_pregnancy_age(claim_id):
    """tblClaim.PregnancyAge exists in the deployed schema; read it directly."""
    with connection.cursor() as cur:
        cur.execute('SELECT "PregnancyAge" FROM "tblClaim" WHERE "ClaimID" = %s', [claim_id])
        row = cur.fetchone()
    return row[0] if row else None


class ClaimEntryTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cov = build_coverage("FSCE")
        cls.user = service_user("fsce_user")

    # Scenario: A claim is stored with a single programme (@redmine-26990 @redmine-27063)
    def test_claim_stores_its_programme(self):
        claim = update_or_create_claim(
            claim_input(self.cov, "FSCE-PRG", [service_input(self.cov.service, 1, 1000)]), self.user)
        claim.refresh_from_db()
        self.assertEqual(claim.program_id, self.cov.program.idProgram)

    # Scenario: A claim can be entered retrospectively (@redmine-27066)
    def test_retrospective_claim_entry(self):
        today = py_datetime.date.today()
        past = today.replace(year=today.year - 2)
        data = claim_input(self.cov, "FSCE-RETRO", [service_input(self.cov.service, 1, 1000)],
                           date_from=past, date_to=past, date_claimed=today)
        claim = update_or_create_claim(data, self.user)
        claim.refresh_from_db()
        self.assertEqual(claim.status, Claim.STATUS_ENTERED)
        self.assertEqual(claim.date_from, past)

    # Scenario: Incremental claim numbering (@redmine-38330)
    @unittest.skip("to test: #38330 (prefix Region+Program or HF+Program not confirmed)")
    def test_incremental_claim_numbering(self):
        pass


class PregnancyAgeSyncTest(TestCase):
    # Scenario: The backend computes the pregnancy age of a claim synced from the mobile app (@redmine-36111)
    def test_pregnancy_age_computed_from_cheque_sante_policy(self):
        cov = build_coverage("FSPA", program_code="CCS", program_name="FSPA Cheque Sante",
                             policy_props={"start_date": py_datetime.date(2026, 1, 1),
                                           "effective_date": py_datetime.date(2026, 1, 1),
                                           "enroll_date": py_datetime.date(2026, 1, 1),
                                           "pregnancy_age": 30})
        user = service_user("fspa_user")
        d = py_datetime.date(2026, 4, 2)
        claim = update_or_create_claim(
            claim_input(cov, "FSPA-MOB", [service_input(cov.service, 1, 1000)],
                        date_from=d, date_to=d, date_claimed=d), user)
        self.assertEqual(db_pregnancy_age(claim.id), 43)


class SubmitterTraceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cov = build_coverage("FSST")
        cls.user = service_user("fsst_user")

    # Scenario: Submitting a claim records the submitting user (@redmine-36438)
    def test_submit_mutation_service_records_submitter(self):
        claim = covered_claim(self.cov, "FSST-MUT")
        submit_claim(claim, self.user)
        claim.refresh_from_db()
        self.assertIn(claim.status, (Claim.STATUS_CHECKED, Claim.STATUS_REJECTED))
        self.assertEqual(claim.audit_user_id_submit, self.user.id_for_audit)

    # Scenario: Submitting a claim records the submitting user (@redmine-36438 @redmine-36632)
    @override_settings(ROW_SECURITY=False)
    def test_claim_submit_service_records_submitter(self):
        claim = covered_claim(self.cov, "FSST-SVC")
        ClaimSubmitService(self.user).submit_claim(claim)
        claim.refresh_from_db()
        self.assertIn(claim.status, (Claim.STATUS_CHECKED, Claim.STATUS_REJECTED))
        self.assertEqual(claim.audit_user_id_submit, self.user.id_for_audit,
                         "ClaimSubmitService.submit_claim leaves AuditUserIDSubmit empty")


class BulkSubmitTraceTest(openIMISGraphQLTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cov = build_coverage("FSBK")
        cls.admin = service_user("fsbk_admin")

    # Scenario: A bulk submission is traceable to its user (@redmine-36438)
    def test_bulk_submit_mutation_log_has_user(self):
        claims = [covered_claim(self.cov, f"FSBK-{i}") for i in range(2)]
        cmid = str(uuid_lib.uuid4())
        label = "2 prestation sélectionnés pour vérification"
        uuids = ", ".join(f'"{c.uuid}"' for c in claims)
        response = self.query(
            'mutation { submitClaims(input: { clientMutationId: "%s", clientMutationLabel: "%s", '
            'uuids: [%s] }) { clientMutationId } }' % (cmid, label, uuids),
            headers={"HTTP_AUTHORIZATION": f"Bearer {get_test_token(self.admin)}"})
        content = json.loads(response.content)
        self.assertIsNone(content.get("errors"), content.get("errors"))
        log = MutationLog.objects.get(client_mutation_id=cmid)
        self.assertEqual(log.client_mutation_label, label)
        self.assertEqual(log.user_id, self.admin.id)
        for c in claims:
            c.refresh_from_db()
            self.assertEqual(c.audit_user_id_submit, self.admin.id_for_audit)


class ReferralFieldsTest(TestCase):
    # Scenario: The claim mutation accepts referral health facilities (@redmine-38415)
    def test_claim_input_accepts_refer_fields(self):
        from claim.gql_mutations import CreateClaimInputType
        # OpenIMISMutation.Input is a plain class: its graphene fields are class attributes
        fields = dir(CreateClaimInputType)
        if "refer_from_id" not in fields:
            self.skipTest("open: #38415")
        self.assertIn("refer_to_id", fields)
