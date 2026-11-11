import json
from dataclasses import dataclass

from graphql_jwt.shortcuts import get_token

from claim.models import Claim
from claim.test_helpers import create_test_claim
from core.models import User
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase
from core.test_helpers import create_test_interactive_user


@dataclass
class DummyContext:
    user: User


class ClaimRowFilterArgsTest(openIMISGraphQLTestCase):
    """claims / claimHistory: validity and audits are applied by Claim.get_queryset.

    One claim code with three rows: the live claim, an audit copy of it
    (save_history: legacy_id set), and a deleted claim (validity_to set, no
    legacy_id). Built here rather than in ClaimGraphQLTestCase, whose fixture
    cannot be loaded into a database that already holds the demo diagnoses.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin = create_test_interactive_user(username="claimRowArgsAdmin")
        cls.token = get_token(cls.admin, DummyContext(user=cls.admin))
        cls.live = create_test_claim(custom_props={"code": "RFA-CLAIM"})
        cls.copy_id = cls.live.save_history()
        cls.deleted = create_test_claim(custom_props={
            "code": "RFA-CLAIM",
            "insuree_id": cls.live.insuree_id,
            "health_facility_id": cls.live.health_facility_id,
        })
        Claim.objects.filter(id=cls.deleted.id).update(validity_to="2023-01-02 00:00:00")

    def _ids(self, field, args):
        response = self.query(
            '{ %s(%s) { edges { node { id } } } }' % (field, args),
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.token}"})
        self.assertResponseNoErrors(response)
        edges = json.loads(response.content)["data"][field]["edges"]
        return {e["node"]["id"] for e in edges}

    def _gql_id(self, pk):
        from graphql_relay import to_global_id
        return to_global_id("ClaimGQLType", pk)

    def test_claim_history_is_every_non_live_row_of_the_code(self):
        found = self._ids("claimHistory", 'claimUuid: "%s"' % self.live.uuid)
        self.assertEqual({self._gql_id(self.copy_id), self._gql_id(self.deleted.id)}, found)

    def test_claims_default_to_live_rows(self):
        self.assertEqual({self._gql_id(self.live.id)}, self._ids("claims", 'code: "RFA-CLAIM"'))

    def test_claims_is_deleted(self):
        self.assertEqual({self._gql_id(self.deleted.id)},
                         self._ids("claims", 'code: "RFA-CLAIM", isDeleted: true'))

    def test_claims_show_history(self):
        self.assertEqual({self._gql_id(self.live.id), self._gql_id(self.copy_id)},
                         self._ids("claims", 'code: "RFA-CLAIM", showHistory: true'))


class ClaimAdminsAndOfficersRowFilterArgsTest(openIMISGraphQLTestCase):
    """claimAdmins / claimOfficers: live rows by default, isDeleted is new."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from claim.models import ClaimAdmin
        from claim.test_helpers import create_test_claim_admin
        from core.models import Officer
        from core.test_helpers import create_test_officer
        from location.test_helpers import create_test_health_facility, create_test_location
        cls.admin = create_test_interactive_user(username="claimRowArgsAdmin2")
        cls.token = get_token(cls.admin, DummyContext(user=cls.admin))
        district = create_test_location('D')
        hf = create_test_health_facility("RFAHF", district.id)
        cls.live_ca = create_test_claim_admin(custom_props={"code": "RFACA1", "health_facility_id": hf.id})
        cls.deleted_ca = create_test_claim_admin(custom_props={"code": "RFACA2", "health_facility_id": hf.id})
        ClaimAdmin.objects.filter(id=cls.deleted_ca.id).update(validity_to="2020-01-01")
        cls.live_eo = create_test_officer(custom_props={"code": "RFAEO1", "last_name": "RfaOfficer"})
        cls.deleted_eo = create_test_officer(custom_props={"code": "RFAEO2", "last_name": "RfaOfficer"})
        Officer.objects.filter(id=cls.deleted_eo.id).update(validity_to="2020-01-01")

    def _codes(self, field, args):
        response = self.query(
            '{ %s(%s) { edges { node { code } } } }' % (field, args),
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.token}"})
        self.assertResponseNoErrors(response)
        return sorted(e["node"]["code"] for e in json.loads(response.content)["data"][field]["edges"])

    def test_claim_admins(self):
        self.assertEqual(["RFACA1"], self._codes("claimAdmins", 'search: "RFACA"'))
        self.assertEqual(["RFACA2"], self._codes("claimAdmins", 'search: "RFACA", isDeleted: true'))

    def test_claim_officers(self):
        self.assertEqual(["RFAEO1"], self._codes("claimOfficers", 'search: "RFAEO"'))
        self.assertEqual(["RFAEO2"], self._codes("claimOfficers", 'search: "RFAEO", isDeleted: true'))
