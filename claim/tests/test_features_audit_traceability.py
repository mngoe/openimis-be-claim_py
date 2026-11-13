"""Tests for backend/features/audit-traceability.feature (Redmine CSU Evolution issues)."""
import json
import unittest
import uuid as uuid_lib

from django.db import connection
from django.test import TestCase

from claim.models import Claim
from claim.services import update_or_create_claim as _svc_update_or_create_claim, submit_claim
from claim.tests.test_features_claim_validation_rules import (
    build_coverage, covered_claim, claim_input, service_input, service_user,
)
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import get_test_token


def update_or_create_claim(data, user):
    """dev-csu: the service takes the claim's program explicitly (gql_mutations resolves it)."""
    return _svc_update_or_create_claim(data, user, data.get("program"))

IP_FIELDS = ("user_entered_ip_address", "last_updater_ip_address", "user_submit_ip_address",
             "user_processed_ip_address", "user_valuated_ip_address")


def db_source(claim_id):
    """Read tblClaim.source directly: the column exists in the deployed schema."""
    with connection.cursor() as cur:
        cur.execute('SELECT "source" FROM "tblClaim" WHERE "ClaimID" = %s', [claim_id])
        row = cur.fetchone()
    return row[0] if row else None


def set_db_source(claim_id, source):
    with connection.cursor() as cur:
        cur.execute('UPDATE "tblClaim" SET "source" = %s WHERE "ClaimID" = %s', [source, claim_id])


CREATE = """mutation { createClaim(input: {
  clientMutationId: "%(cmid)s", code: "%(code)s", insureeId: %(insuree)s,
  dateFrom: "2024-06-03", dateTo: "2024-06-03", dateClaimed: "2024-06-03",
  icdId: %(icd)s, healthFacilityId: %(hf)s, program: %(program)s, %(extra)s
  services: [{serviceId: %(service)s, status: 1, qtyProvided: "1", priceAsked: "1000"}]
}) { clientMutationId internalId } }"""

UPDATE = """mutation { updateClaim(input: {
  clientMutationId: "%(cmid)s", uuid: "%(uuid)s", code: "%(code)s", insureeId: %(insuree)s,
  dateFrom: "2024-06-03", dateTo: "2024-06-03", dateClaimed: "2024-06-03",
  icdId: %(icd)s, healthFacilityId: %(hf)s, program: %(program)s,
  services: [{serviceId: %(service)s, status: 1, qtyProvided: "2", priceAsked: "1000",
              explanation: "two sessions"}]
}) { clientMutationId internalId } }"""

BULK = """mutation { %(name)s(input: { clientMutationId: "%(cmid)s", uuids: ["%(uuid)s"] })
{ clientMutationId internalId } }"""


class _GqlClaimCase(openIMISGraphQLTestCase):
    prefix = None

    @classmethod
    def setUpTestData(cls):
        cls.cov = build_coverage(cls.prefix)
        cls.admin = service_user(f"{cls.prefix.lower()}_admin")
        cls.token = get_test_token(cls.admin)

    def _gql(self, query, **headers):
        response = self.query(query, headers={"HTTP_AUTHORIZATION": f"Bearer {self.token}", **headers})
        return json.loads(response.content)

    def _params(self, code, **extra):
        c = self.cov
        return {"cmid": str(uuid_lib.uuid4()), "code": code, "insuree": c.insuree.id, "icd": c.icd.id,
                "hf": c.hf.id, "program": c.program.idProgram, "service": c.service.id, "extra": "",
                **extra}


# ---------------------------------------------------------------------------------------
# Rule: Every claim records its source
# ---------------------------------------------------------------------------------------
class ClaimSourceGqlTest(_GqlClaimCase):
    prefix = "FASG"

    # Scenario: The claim source is stored at creation (@redmine-35077 @redmine-35086)
    def test_web_and_mobile_mutation_store_source(self):
        for sent in ("WEB", "MOB"):
            with self.subTest(source=sent):
                code = f"FASG-{sent}"
                content = self._gql(CREATE % self._params(code, extra=f'source: "{sent}",'))
                self.assertIsNone(content.get("errors"), content.get("errors"))
                claim = Claim.objects.filter(code=code, validity_to__isnull=True).first()
                self.assertIsNotNone(claim, f"claim {code} was not created")
                self.assertEqual(db_source(claim.id), sent)


class ClaimSourceServiceTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.cov = build_coverage("FASS")
        cls.user = service_user("fass_user")

    # Scenario: The source does not change the claim workflow (@redmine-35077)
    def test_null_source_claim_is_processed_normally(self):
        claim = covered_claim(self.cov, "FASS-NULL")
        self.assertIsNone(db_source(claim.id))
        errors = submit_claim(claim, self.user)
        claim.refresh_from_db()
        self.assertEqual(errors, [])
        self.assertEqual(claim.status, Claim.STATUS_CHECKED)
        self.assertIsNone(db_source(claim.id))

    # Scenario: A restored claim keeps the source of the original (@redmine-34470 @redmine-35086)
    def test_restored_claim_keeps_source(self):
        original = covered_claim(self.cov, "FASS-ORIG", status=Claim.STATUS_REJECTED)
        set_db_source(original.id, "MOB")
        new = update_or_create_claim(
            claim_input(self.cov, "FASS-ORIGR", [service_input(self.cov.service, 1, 1000)],
                        restore=original.uuid), self.user)
        self.assertEqual(db_source(new.id), "MOB")


# ---------------------------------------------------------------------------------------
# Rule: Claim workflow steps record the client IP
# ---------------------------------------------------------------------------------------
class ClaimIpGqlTest(_GqlClaimCase):
    prefix = "FAIP"

    def _ip(self, claim, field):
        claim = Claim.objects.get(id=claim.id)
        self.assertTrue(hasattr(claim, field), f"Claim has no field {field}")
        return getattr(claim, field)

    # Scenario: The client IP is stored on each workflow step (@redmine-37026 @redmine-36656)
    def test_create_stores_entered_ip(self):
        content = self._gql(CREATE % self._params("FAIP-CRE"), HTTP_X_FORWARDED_FOR="41.202.10.11")
        self.assertIsNone(content.get("errors"), content.get("errors"))
        claim = Claim.objects.get(code="FAIP-CRE", validity_to__isnull=True)
        self.assertEqual(self._ip(claim, "user_entered_ip_address"), "41.202.10.11")

    # Scenario: The client IP is stored on each workflow step (@redmine-37026 @redmine-36656)
    def test_update_stores_last_updater_ip(self):
        claim = covered_claim(self.cov, "FAIP-UPD")
        content = self._gql(UPDATE % self._params("FAIP-UPD", uuid=claim.uuid),
                            HTTP_X_REAL_IP="41.202.10.12")
        self.assertIsNone(content.get("errors"), content.get("errors"))
        self.assertEqual(self._ip(claim, "last_updater_ip_address"), "41.202.10.12")

    # Scenario: The client IP is stored on each workflow step (@redmine-37026 @redmine-36656)
    def test_submit_stores_submit_ip(self):
        claim = covered_claim(self.cov, "FAIP-SUB")
        content = self._gql(BULK % {"name": "submitClaims", "cmid": str(uuid_lib.uuid4()),
                                    "uuid": claim.uuid}, HTTP_X_FORWARDED_FOR="41.202.10.13")
        self.assertIsNone(content.get("errors"), content.get("errors"))
        self.assertEqual(Claim.objects.get(id=claim.id).status, Claim.STATUS_CHECKED)
        self.assertEqual(self._ip(claim, "user_submit_ip_address"), "41.202.10.13")

    # Scenario: The client IP is stored on each workflow step (@redmine-37026 @redmine-37476)
    # Without relative prices, processing also valuates the claim, so the same request sets
    # both the processed and the valuated IP.
    def test_process_stores_processed_and_valuated_ip(self):
        claim = covered_claim(self.cov, "FAIP-PRO")
        submit_claim(claim, self.admin)
        self.assertEqual(Claim.objects.get(id=claim.id).status, Claim.STATUS_CHECKED)
        content = self._gql(BULK % {"name": "processClaims", "cmid": str(uuid_lib.uuid4()),
                                    "uuid": claim.uuid}, HTTP_X_FORWARDED_FOR="41.202.10.14")
        self.assertIsNone(content.get("errors"), content.get("errors"))
        self.assertEqual(Claim.objects.get(id=claim.id).status, Claim.STATUS_VALUATED)
        self.assertEqual(self._ip(claim, "user_processed_ip_address"), "41.202.10.14")
        self.assertEqual(self._ip(claim, "user_valuated_ip_address"), "41.202.10.14")

    # Scenario: Existing claims keep null IP fields (@redmine-36622 @redmine-37026)
    def test_claim_without_ip_has_null_ip_fields(self):
        claim = covered_claim(self.cov, "FAIP-NULL")
        for field in IP_FIELDS:
            with self.subTest(field=field):
                self.assertIsNone(self._ip(claim, field))


# Scenario: The date of a Medical Adviser review is recorded (@redmine-37686)
class ReviewTimestampTest(TestCase):
    @unittest.skip("open: #37686")
    def test_review_timestamp_recorded(self):
        pass
