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
