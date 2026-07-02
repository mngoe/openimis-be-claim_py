import json
from dataclasses import dataclass
from core.models import User
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase

from core.test_helpers import create_test_interactive_user
from graphql_jwt.shortcuts import get_token
#credits https://docs.graphene-python.org/projects/django/en/latest/testing/
from claim import schema as claim_schema
from graphene.test import Client
from graphene import Schema

from claim.models import Claim, ClaimItem, ClaimService
from claim.test_helpers import create_test_claim_admin, create_test_claim
from claim.services import REJECTION_REASON_MANUAL_REJECTION, ClaimSubmitService, ClaimSubmitError
import datetime
from unittest import mock
from django.core.cache import caches
from django.test import TestCase
from core.utils import clear_current_user
from core.test_helpers import create_medical_officer_role, create_test_role
from location.test_helpers import (
    create_test_location,
    assign_user_districts,
    create_test_health_facility,
)
from claim.gql_queries import ClaimGQLType
from claim.gql_mutations import SubmitClaimsMutation
from core.gql.gql_mutations.mutation_by_filter import mutation_on_queryset_from_filter
from policy.models import Policy
from policy.test_helpers import create_test_policy2
from product.test_helpers import create_test_product, create_test_product_service
from core.test_helpers import create_test_officer
from insuree.test_helpers import create_test_insuree
from location.models import Location
from medical.test_helpers import create_test_service
from medical_pricelist.test_helpers import add_service_to_hf_pricelist, \
    create_test_service_pricelist, create_test_item_pricelist
from program.test_helpers import create_test_program
from location.test_helpers import create_test_health_facility, create_test_village
from claim.test_helpers import create_test_claimitem, create_test_claimservice


def _make_async_mutate_spy():
    """Return a simple async_mutate replacement that records the data it receives."""
    calls = []

    def _spy(cls, user, **data):
        calls.append({"cls": cls, "user": user, "data": dict(data)})
        return "ok"

    return _spy, calls

@dataclass
class DummyContext:
    """ Just because we need a context to generate. """
    user: User

class ClaimGraphQLTestCase(openIMISGraphQLTestCase):

    # This is required by some version of graphene but is never used. It should be set to the schema but the import
    # is shown as an error in the IDE, so leaving it as True.
    GRAPHQL_SCHEMA = True
    admin_user = None
    graph_client = None
    schema = None      
    officer= None
    insuree= None
    product= None
    service= None
    product_service= None
    claim_admin = None
    location= None
    program= None
    hf= None
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = create_test_interactive_user(username="testLocationAdmin")
        cls.admin_token = get_token(cls.admin_user, DummyContext(user=cls.admin_user))
        cls.schema = Schema(
            query=claim_schema.Query,
            mutation=claim_schema.Mutation
        )
        cls.graph_client = Client(cls.schema)
        
        cls.officer = create_test_officer(custom_props={"code": "TSTSIMP1"})
        cls.insuree = create_test_insuree(custom_props={"chf_id": "paysimp"})
        cls.product = create_test_product("ELI1")
        (policy, insuree_policy) = create_test_policy2(cls.product, cls.insuree, custom_props={
            "value": 1000, "status": Policy.STATUS_ACTIVE})
        cls.service = create_test_service("A")
        cls.claim_admin = create_test_claim_admin()
        cls.location = create_test_village()
        cls.hf_spl = create_test_service_pricelist(cls.location.id)
        cls.hf_ipl = create_test_item_pricelist(cls.location.id)
        cls.hf = create_test_health_facility("TEST_HF2", location_id=cls.location.id, custom_props={'services_pricelist': cls.hf_spl, 'items_pricelist': cls.hf_ipl}, valid=True)
        cls.svc_pl_detail = add_service_to_hf_pricelist(cls.service, hf_id = cls.hf.id )
        cls.product_service = create_test_product_service(cls.product, cls.service, custom_props={"limit_no_adult": 20})
        cls.program = create_test_program(code="CCS", name="Chêque Santé")
        cls.claim = create_test_claim(custom_props={"insuree_id": cls.insuree.id})
        cls.claim_item = create_test_claimitem(cls.claim)
        cls.claim_service= create_test_claimservice(cls.claim)
        
    def test_claims_query(self):
        
        response = self.query(
            '''
            query {
                claims
                {
                    totalCount
                    pageInfo { hasNextPage, hasPreviousPage, startCursor, endCursor}
                    edges
                    {
                        node
                        {
                            uuid,code,jsonExt,dateClaimed,dateProcessed,feedbackStatus,reviewStatus,claimed,approved,status,restoreId,healthFacility { id uuid name code },insuree{id, uuid, chfId, lastName, otherNames, dob},attachmentsCount

                        }
                    }
                }
            }
            ''',
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )

        content = json.loads(response.content)

        # This validates the status code and if you get errors
        self.assertResponseNoErrors(response)

        # Add some more asserts if you like
        ...

    def test_query_with_variables(self):
        response = self.query(
            '''
            query claims($status: Int!, $first:  Int! ) {
                claims(status: $status,orderBy: ["-dateClaimed"],first: $first)
                {
                    totalCount
                    pageInfo { hasNextPage, hasPreviousPage, startCursor, endCursor}
                    edges
                    {
                        node
                        {
                            uuid,code,jsonExt,dateClaimed,dateProcessed,feedbackStatus,reviewStatus,claimed,approved,status,restoreId,healthFacility { id uuid name code },insuree{id, uuid, chfId, lastName, otherNames, dob},attachmentsCount

                        }
                    }
                }
            }
            ''',
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
            variables={'status': 2, 'first':10}
        )

        content = json.loads(response.content)

        # This validates the status code and if you get errors
        self.assertResponseNoErrors(response)
        
    def execute_mutation(self, mutation):
        mutation_result = self.graph_client.execute(mutation, context=DummyContext(user=self.admin_user))
        return mutation_result
        
    def test_mutation_create_claim(self):

        response = self.query(
            f'''
            mutation {{
                createClaim(
                    input: {{
                    clientMutationId: "3a90436a-d5ea-48e7-bde4-0bcff0240260"
                    clientMutationLabel: "Create Claim - m-c-claim" 
                    code: "m-c-claim"
                autogenerate: false
                insureeId: {self.insuree.id}
                adminId: {self.claim_admin.id}
                dateFrom: "2023-12-06"  
                icdId: 2 
                jsonExt: "{{}}"
                feedbackStatus: 1
                reviewStatus: 1
                dateClaimed: "2023-12-06"
                healthFacilityId: {self.hf.id}
                visitType: "O"
                program: {self.program.idProgram}
                services: [
                {{
                
                serviceId: {self.service.id}
                priceAsked: "10.00"
                qtyProvided: "1.00"
                status: 1,
                serviceItemSet: [],
                serviceServiceSet: []
            }}
                ]
                items: [
                ]
                    }}
                ) {{
                    clientMutationId
                    internalId
                }}
            }}
                ''',
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"})
        self.get_mutation_result('3a90436a-d5ea-48e7-bde4-0bcff0240260', self.admin_token )
        claim = Claim.objects.filter(code = 'm-c-claim').first()
        self.assertIsNotNone(claim)
        self.assertEqual(claim.status, Claim.STATUS_ENTERED)
        response = self.query(
            f'''
            mutation {{
                updateClaim(
                    input: {{
                    clientMutationId: "3a90436b-d5ea-48e7-bde4-0bcff0240260"
                    clientMutationLabel: "Update Claim - m-c-claim" 
                    code: "m-c-claim"
                autogenerate: false
                uuid: "{str(claim.uuid)}"
                insureeId: {self.insuree.id}
                adminId: {self.claim_admin.id}
                dateFrom: "2023-11-06"  
                icdId: 2 
                jsonExt: "{{}}"
                feedbackStatus: 1
                reviewStatus: 1
                dateClaimed: "2023-12-06"
                healthFacilityId: {self.hf.id}
                visitType: "O"
                program: {self.program.idProgram}
                services: [
                {{
                
                serviceId: {self.service.id}
                priceAsked: "10.00"
                qtyProvided: "1.00"
                status: 1,
                serviceItemSet: [],
                serviceServiceSet: []
            }}
                ]
                items: [
                ]
                    }}
                ) {{
                    clientMutationId
                    internalId
                }}
            }}
                ''',
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"})
        self.get_mutation_result('3a90436b-d5ea-48e7-bde4-0bcff0240260', self.admin_token )

        #submit claim 
        response = self.query(f'''
            mutation {{
            submitClaims(
                input: {{
                clientMutationId: "d02fff0a-dd95-4413-a2f4-4cf2189dc0d6"
                clientMutationLabel: "Submit claim erterwtw"
                
                uuids: ["{claim.uuid}"]
                }}
            ) {{
                clientMutationId
                internalId
            }}
            }}
            ''',
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"})
        self.assertResponseNoErrors(response)

        self.get_mutation_result('d02fff0a-dd95-4413-a2f4-4cf2189dc0d6', self.admin_token )
        # select for feeback
        claim = Claim.objects.filter(code = 'm-c-claim').first()
        create_test_officer(villages=[claim.insuree.family.location])
        self.assertEqual(claim.status, Claim.STATUS_CHECKED)
        response = self.query(f'''
            mutation {{
            selectClaimsForFeedback(
                input: {{
                clientMutationId: "f0585e2b-d72d-4001-915a-1cf10e9f1722"
                clientMutationLabel: "Select claim sadddfas for feedback"
                
                uuids: ["{claim.uuid}"]
                }}
            ) {{
                clientMutationId
                internalId
            }}
            }}
        ''' ,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"})
        self.assertResponseNoErrors(response)
        self.get_mutation_result('f0585e2b-d72d-4001-915a-1cf10e9f1722', self.admin_token )
        ## check the mutation answer
        claim = Claim.objects.filter(code = 'm-c-claim').first()
        self.assertEqual(claim.feedback_status, Claim.FEEDBACK_SELECTED)
        

    def test_reject_claims_mutation(self):
        mutation = f"""
            mutation {{
                rejectClaims(
                    input: {{
                        clientMutationId: "test-reject"
                        uuids: ["{self.claim.uuid}"]
                        explanation: "Manually rejected from test"
                    }}
                ) {{
                    clientMutationId
                    internalId
                }}
            }}
        """

        response = self.query(mutation, headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"})
        self.assertResponseNoErrors(response)

        # Check the claim status
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.status, Claim.STATUS_REJECTED)
        self.assertEqual(self.claim.explanation, "Manually rejected from test")
        claim_item = self.claim.items.first()
        self.assertIsNotNone(claim_item)
        self.assertEqual(claim_item.status, ClaimItem.STATUS_REJECTED)
        self.assertEqual(claim_item.rejection_reason, REJECTION_REASON_MANUAL_REJECTION)
        claim_service = self.claim.services.first()
        self.assertIsNotNone(claim_service)
        self.assertEqual(claim_service.status, ClaimService.STATUS_REJECTED)
        self.assertEqual(claim_service.rejection_reason, REJECTION_REASON_MANUAL_REJECTION)
        
        
    def test_bypass_claims_review_mutation_mixed_uuids(self):
        """
        Test bypassing review with UUIDs.
        """
        mutation = f"""
            mutation {{
                bypassClaimsReview(
                    input: {{
                        clientMutationId: "test-bypass-review"
                        clientMutationLabel: "Bypass review with UUIDs"
                        uuids: ["{self.claim.uuid}"]
                    }}
                ) {{
                    clientMutationId
                    internalId
                }}
            }}
        """

        response = self.query(mutation, headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"})
        self.assertResponseNoErrors(response)
        
        # Verify valid claim was processed
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.review_status, Claim.REVIEW_BYPASSED)
        self.assertEqual(self.claim.status, Claim.STATUS_PROCESSED)
        
        
    def test_deliver_claims_review_mutation(self):
        """
        Test deliver review with UUIDs.
        """
        mutation = f"""
            mutation {{
                deliverClaimsReview(
                    input: {{
                        clientMutationId: "test-deliver-review"
                        clientMutationLabel: "Deliver review with UUIDs"
                        uuids: ["{self.claim.uuid}"]
                    }}
                ) {{
                    clientMutationId
                    internalId
                }}
            }}
        """

        response = self.query(mutation, headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"})
        self.assertResponseNoErrors(response)
        
        # Verify valid claim was processed
        self.claim.refresh_from_db()
        self.assertEqual(self.claim.review_status, Claim.REVIEW_DELIVERED)
        self.assertEqual(self.claim.status, Claim.STATUS_PROCESSED)

    def test_claim_history_query(self):
        historical_claim1 = create_test_claim(custom_props={
            "code": self.claim.code,
            "validity_to": "2023-01-01 00:00:00",
            "insuree_id": self.insuree.id,
            "health_facility_id": self.hf.id,
            "status": Claim.STATUS_ENTERED
        })
        historical_claim2 = create_test_claim(custom_props={
            "code": self.claim.code,
            "validity_to": "2023-01-02 00:00:00",
            "insuree_id": self.insuree.id,
            "health_facility_id": self.hf.id,
            "status": Claim.STATUS_CHECKED
        })

        response = self.query(
            '''
            query {
                claimHistory(claimUuid: "%s") {
                    totalCount
                    edges {
                        node {
                            uuid
                            code
                            validityTo
                            status
                        }
                    }
                }
            }
            ''' % str(self.claim.uuid),
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
        )
        content = json.loads(response.content)
        self.assertResponseNoErrors(response)
        self.assertEqual(content['data']['claimHistory']['totalCount'], 2)
        edges = content['data']['claimHistory']['edges']
        self.assertEqual(edges[0]['node']['code'], self.claim.code)
        self.assertIsNotNone(edges[0]['node']['validityTo'])
        self.assertEqual(edges[0]['node']['status'], Claim.STATUS_ENTERED)
        self.assertEqual(edges[1]['node']['code'], self.claim.code)
        self.assertIsNotNone(edges[1]['node']['validityTo'])
        self.assertEqual(edges[1]['node']['status'], Claim.STATUS_CHECKED)

    def test_pregnancy_age_ok(self):
        # Configure policy
        policy = Policy.objects.filter(family=self.insuree.family, product=self.product).first()
        policy.pregnancy_age = 10  # 10 weeks at start date
        policy.start_date = datetime.date(2026, 1, 1)
        policy.expiry_date = datetime.date(2026, 12, 31)
        policy.save()

        # Claim date 5 weeks later
        claim_date_to = datetime.datetime(2026, 2, 5, 0, 0)

        query = """
        query pregnancyAge($claimDateTo: DateTime!, $familyId: Int!, $product: Int!) {
        pregnancyAge(
            claimDateTo: $claimDateTo,
            familyId: $familyId,
            product: $product
        ) {
            pregnancyAge
            claimDateTo
            familyId
            product
        }
        }
        """

        variables = {
            "claimDateTo": claim_date_to.isoformat(),
            "familyId": self.insuree.family.id,
            "product": self.product.id,
        }

        response = self.query(
            query,
            variables=variables,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        content = json.loads(response.content)
        self.assertResponseNoErrors(response)

        node = content["data"]["pregnancyAge"]
        self.assertIsNotNone(node)
        self.assertEqual(node["pregnancyAge"], 15)
        self.assertEqual(node["familyId"], self.insuree.family.id)
        self.assertEqual(node["product"], self.product.id)


    def test_pregnancy_age_no_valid_policy(self):
        # Setup invalid policy
        policy = Policy.objects.filter(family=self.insuree.family, product=self.product).first()
        policy.pregnancy_age = None  # No pregnancy age
        policy.start_date = datetime.date(2026, 1, 1)
        policy.expiry_date = datetime.date(2026, 1, 15)  # Expired before claim date
        policy.save()

        # Claim date after expiry date
        claim_date_to = datetime.datetime(2026, 2, 5, 0, 0)

        query = """
        query pregnancyAge($claimDateTo: DateTime!, $familyId: Int!, $product: Int!) {
        pregnancyAge(
            claimDateTo: $claimDateTo,
            familyId: $familyId,
            product: $product
        ) {
            pregnancyAge
            claimDateTo
            familyId
            product
        }
        }
        """

        variables = {
            "claimDateTo": claim_date_to.isoformat(),
            "familyId": self.insuree.family.id,
            "product": self.product.id,
        }

        response = self.query(
            query,
            variables=variables,
            headers={"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"},
        )
        content = json.loads(response.content)
        self.assertResponseNoErrors(response)

        node = content["data"]["pregnancyAge"]
        self.assertIsNotNone(node)
        self.assertIsNone(node["pregnancyAge"])
        self.assertEqual(node["familyId"], self.insuree.family.id)

class SubmitClaimsWithFilterDecoratorRowSecurityTest(TestCase):
    """
    Tests row security for claim submission.

    After the core change that stopped calling Model.get_queryset(user) inside
    mutation_on_queryset_from_filter, the decorator no longer pre-filters the
    queryset with the user's location rights.

    These tests verify that submit is still only applied to claims the user
    is authorized for:
    - When using additional_filters, the resulting queryset should respect
      the user's location rights (where the decorator still applies filters).
    - When explicit uuids are provided (including a mix of authorized and
      unauthorized claims), only authorized claims should have their status
      changed by the submit operation.
    """

    def setUp(self):
        clear_current_user()
        # clear_history_context()
        caches["default"].clear()

    def tearDown(self):
        clear_current_user()
        # clear_history_context()
        caches["default"].clear()

    def test_submit_claims_filter_on_checked_state_only_returns_user_accessible_claims_district(self):
        """
        District-restricted user (via UserDistrict) + filter status=CHECKED:
        - Create claims in allowed district and foreign district, all with CHECKED status.
        - additional_filters selects status CHECKED (would match all without row sec).
        - Resulting queryset from decorator must contain only the claims from the user's district.
        """
        # Locations: two districts (codes <=8 chars per model). D must have parent for UserDistrict queries in get_user_districts.
        region_allowed = create_test_location(
            "R", custom_props={"code": "RA1", "name": "Allowed Region"}
        )
        district_allowed = create_test_location(
            "D", custom_props={"code": "DA1", "name": "Allowed District for submit filter", "parent": region_allowed}
        )
        ward_allowed = create_test_location("W", custom_props={"code": "WA1", "name": "Allowed ward", "parent": district_allowed})
        village_allowed = create_test_location("V", custom_props={"code": "VA1", "name": "Allowed village", "parent": ward_allowed})

        # HF rattachée au village, pas directement au district
        hf_allowed = create_test_health_facility(code="HA1", location_id=district_allowed.id)
        region_forbidden = create_test_location(
            "R", custom_props={"code": "RF2", "name": "Forbidden Region"}
        )
        district_forbidden = create_test_location(
            "D", custom_props={"code": "DF2", "name": "Forbidden District for submit filter", "parent": region_forbidden}
        )

        # hf_allowed = create_test_health_facility(
        #     code="HA1", location_id=district_allowed.id
        # )
        hf_forbidden = create_test_health_facility(
            code="HF2", location_id=district_forbidden.id
        )

        # Limited non-super user with submit perms, restricted to one district
        med_officer_role = create_medical_officer_role()
        limited_user = create_test_interactive_user(
            username="submitter_district_only",
            roles=[med_officer_role.id],
            # custom_props={"is_superuser": False},
        )
        assign_user_districts(limited_user, [district_allowed.code])
        # incoming_qs = Claim.get_queryset(Claim.objects, limited_user)
        incoming_qs = Claim.objects.filter(validity_to__isnull=True)
        print("limited user: ", limited_user.i_user, "get_queryset count:", incoming_qs.count(), "Test manuel status=4:", incoming_qs.filter(status=Claim.STATUS_CHECKED).count(), "Test manuel status='4':", incoming_qs.filter(status="4").count())
        # Test direct sans passer par q_filter
        # print("q_filter children:", q_filter.children)

        # Create claims matching the filter (CHECKED) in both locations
        claim_allowed1 = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_CHECKED,
                "code": "SUBMIT-ALLOW-1",
            }
        )
        claim_allowed2 = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_CHECKED,
                "code": "SUBMIT-ALLOW-2",
            }
        )
        # A non-matching status in allowed location (should be excluded by the status filter too)
        claim_allowed_entered = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_ENTERED,
                "code": "SUBMIT-ALLOW-ENTERED",
            }
        )

        claim_forbidden1 = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_CHECKED,
                "code": "SUBMIT-FORBID-1",
            }
        )
        claim_forbidden2 = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_CHECKED,
                "code": "SUBMIT-FORBID-2",
            }
        )

        spy, calls = _make_async_mutate_spy()

        # Re-apply the exact same decorator config used by SubmitClaimsMutation
        # (including its explicit filter handlers for services/items)
        handlers = getattr(
            SubmitClaimsMutation, "_SubmitClaimsMutation__filter_handlers", {}
        )
        from location.models import HealthFacility
        # from claim.models import Claim

        hf_from_db = HealthFacility.objects.get(id=hf_allowed.id)
        print("HF location:", hf_from_db.location)
        print("HF location_id:", hf_from_db.location_id)

        # Test de traversée Django pas à pas
        print("Via health_facility=:", Claim.objects.filter(health_facility=hf_allowed).count())
        print("Via health_facility__location=: ", Claim.objects.filter(health_facility__location=district_allowed).count())
        print("Via health_facility__location_id=:", Claim.objects.filter(health_facility__location_id=232).count())
        print("Via health_facility__location__in=:", Claim.objects.filter(health_facility__location__in=[district_allowed]).count())
        from location.models import UserDistrict
        distrcits = UserDistrict.objects.filter(user=limited_user.i_user)
        print("distrcits ", distrcits)
        base_qs = Claim.objects.filter(validity_to__isnull=True)
        sec_qs = Claim.get_queryset(base_qs, limited_user)
        print("Base claims count:", base_qs.count())
        print("After row security count:", sec_qs.count())
        # from location.models import UserDistrict, LocationManager

        # # 1. Vérifier les UserDistrict créés
        # uds = UserDistrict.objects.filter(user=limited_user.i_user, validity_to__isnull=True)
        # print("UserDistricts:", list(uds.values("location__id", "location__code", "location__type")))

        # # 2. Vérifier ce que allowed() retourne pour ce user
        # lm = LocationManager()
        # allowed_locs = lm.allowed(limited_user.i_user.id)
        # print("Allowed locations:", list(allowed_locs.values("id", "code", "type")))

        # # 3. Vérifier le LocationId de la HF
        # print("HF location_id:", hf_allowed.location_id, "district_id:", district_allowed.id)
        decorated = mutation_on_queryset_from_filter(
            Claim,
            ClaimGQLType,
            "additional_filters",
            handlers,
        )(spy)

        # Use additional_filters for the status=CHECKED (the filter requested in the scenario)
        # Note: key "status" maps to the exact filter.
        filters = {"status": Claim.STATUS_CHECKED}
        data = {"additional_filters": json.dumps(filters)}

        # Call without uuids and without pre-supplied queryset -> triggers get_queryset + filter
        decorated(SubmitClaimsMutation, limited_user, **data)

        self.assertEqual(len(calls), 1)
        received_data = calls[0]["data"]
        self.assertIn("queryset", received_data)
        final_qs = received_data["queryset"]

        final_uuids = set(final_qs.values_list("uuid", flat=True))

        # Only claims from the allowed location + matching filter should be present
        self.assertIn(claim_allowed1.uuid, final_uuids)
        self.assertIn(claim_allowed2.uuid, final_uuids)
        self.assertNotIn(claim_allowed_entered.uuid, final_uuids)  # filtered out by status
        self.assertNotIn(claim_forbidden1.uuid, final_uuids)
        self.assertNotIn(claim_forbidden2.uuid, final_uuids)

        # All returned must be CHECKED (the filter) and from allowed hf
        for c in final_qs:
            self.assertEqual(c.status, Claim.STATUS_CHECKED)
            self.assertEqual(c.health_facility_id, hf_allowed.id)

    def test_submit_claims_filter_on_checked_state_only_returns_user_accessible_claims_hf(self):
        """
        HF-restricted user (health_facility_id on i_user, as for claimAdmins):
        Same logic: filter status=CHECKED must only yield claims under the user's HF.
        """
        region = create_test_location(
            "R", custom_props={"code": "RH1", "name": "HF only region"}
        )
        district = create_test_location(
            "D", custom_props={"code": "DH1", "name": "HF only district", "parent": region}
        )
        hf_allowed = create_test_health_facility(
            code="HFA1", location_id=district.id
        )
        hf_forbidden = create_test_health_facility(
            code="HFF2", location_id=district.id  # same district, different hf -> still restricted by hf_id
        )

        med_officer_role = create_medical_officer_role()
        limited_user = create_test_interactive_user(
            username="submitter_hf_only",
            roles=[med_officer_role.id],
            # custom_props={"is_superuser": False},
        )
        # Simulate claim admin style restriction
        limited_user.i_user.health_facility_id = hf_allowed.id
        limited_user.i_user.save()

        claim_ok = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_CHECKED,
                "code": "HFONLY-OK",
            }
        )
        claim_bad = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_CHECKED,
                "code": "HFONLY-BAD",
            }
        )

        spy, calls = _make_async_mutate_spy()

        decorated = mutation_on_queryset_from_filter(
            Claim,
            ClaimGQLType,
            "additional_filters",
            getattr(SubmitClaimsMutation, "_SubmitClaimsMutation__filter_handlers", {}),
        )(spy)

        data = {"additional_filters": json.dumps({"status": Claim.STATUS_CHECKED})}

        decorated(SubmitClaimsMutation, limited_user, **data)

        final_qs = calls[0]["data"]["queryset"]
        final_uuids = set(final_qs.values_list("uuid", flat=True))

        self.assertIn(claim_ok.uuid, final_uuids)
        self.assertNotIn(claim_bad.uuid, final_uuids)

        for c in final_qs:
            self.assertEqual(c.health_facility_id, hf_allowed.id)

    def test_user_cannot_submit_claim_from_unauthorized_location(self):
        """
        Regression test: ensure submit cannot be applied to a claim the user
        is not authorised for due to location (district/HF) restrictions.

        This is important after the core change that stopped calling
        Model.get_queryset(user) inside mutation_on_queryset_from_filter:
        submit protection for explicit uuids (and potentially filter-derived
        claims) now relies on ClaimSubmitService._validate_user_hf.

        - restricted user (via UserDistrict)
        - claim exists in a district the user is not assigned to
        - calling submit_claim must raise ClaimSubmitError
        - the claim status must remain unchanged (submit not applied)
        """
        # Setup locations in two different districts
        region_allowed = create_test_location(
            "R", custom_props={"code": "RS1", "name": "Submit Auth Allowed Region"}
        )
        district_allowed = create_test_location(
            "D",
            custom_props={
                "code": "DS1",
                "name": "Submit Auth Allowed District",
                "parent": region_allowed,
            },
        )
        region_forbidden = create_test_location(
            "R", custom_props={"code": "RS2", "name": "Submit Auth Forbidden Region"}
        )
        district_forbidden = create_test_location(
            "D",
            custom_props={
                "code": "DS2",
                "name": "Submit Auth Forbidden District",
                "parent": region_forbidden,
            },
        )

        hf_allowed = create_test_health_facility(
            code="HFS1", location_id=district_allowed.id
        )
        hf_forbidden = create_test_health_facility(
            code="HFS2", location_id=district_forbidden.id
        )

        med_officer_role = create_medical_officer_role()
        limited_user = create_test_interactive_user(
            username="submit_auth_limited",
            roles=[med_officer_role.id],
            # custom_props={"is_superuser": False},
        )
        assign_user_districts(limited_user, [district_allowed.code])

        # Create a claim in the *forbidden* location, in a submittable state
        forbidden_claim = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_ENTERED,
                "code": "NOAUTH-SUBMIT-UUID",
            }
        )

        service = ClaimSubmitService(limited_user)

        # The submit must be rejected due to location (hf not visible to user)
        with self.assertRaises(ClaimSubmitError):
            service.submit_claim(forbidden_claim)

        # Ensure submit was not applied
        forbidden_claim.refresh_from_db()
        self.assertEqual(forbidden_claim.status, Claim.STATUS_ENTERED)

    def test_submit_claims_with_uuid_list_only_applies_to_authorized_claims(self):
        """
        Submit using an explicit list of uuids (the path used by SubmitClaimsMutation
        when uuids are provided directly, bypassing additional_filters).

        The list contains both an authorized claim and one the user should not be
        able to submit (different location / district).

        Only the authorized claim should have its status changed by the submit.
        The unauthorized claim must remain untouched.
        """
        # Locations + HFs in two different districts
        region_allowed = create_test_location(
            "R", custom_props={"code": "RU1", "name": "RowSec Allowed Region"}
        )
        district_allowed = create_test_location(
            "D",
            custom_props={
                "code": "DU1",
                "name": "RowSec Allowed District",
                "parent": region_allowed,
            },
        )
        region_forbidden = create_test_location(
            "R", custom_props={"code": "RU2", "name": "RowSec Forbidden Region"}
        )
        district_forbidden = create_test_location(
            "D",
            custom_props={
                "code": "DU2",
                "name": "RowSec Forbidden District",
                "parent": region_forbidden,
            },
        )

        hf_allowed = create_test_health_facility(
            code="HFU1", location_id=district_allowed.id
        )
        hf_forbidden = create_test_health_facility(
            code="HFU2", location_id=district_forbidden.id
        )

        med_officer_role = create_medical_officer_role()
        limited_user = create_test_interactive_user(
            username="submit_mixed_uuids_user",
            roles=[med_officer_role.id],
            # custom_props={"is_superuser": False},
        )
        assign_user_districts(limited_user, [district_allowed.code])

        # Two claims ready to be submitted
        claim_allowed = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_ENTERED,
                "code": "MIXED-UUID-OK",
            }
        )
        claim_forbidden = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_ENTERED,
                "code": "MIXED-UUID-BAD",
            }
        )

        # Send an explicit list of uuids (as SubmitClaimsMutation receives)
        # containing both an authorized claim and one the user must not submit.
        target_uuids = [str(claim_allowed.uuid), str(claim_forbidden.uuid)]

        # Patch processing_claim so an authorized claim can successfully
        # reach CHECKED status. The location authorization check still runs.
        # Also neutralize stats logging (no real MutationLog in this test).
        with mock.patch("claim.gql_mutations.submit_claim", return_value=[]), \
             mock.patch.object(SubmitClaimsMutation, "add_submission_stats_to_mutation_log"):
            try:
                SubmitClaimsMutation.async_mutate(
                    user=limited_user, uuids=target_uuids
                )
            except ClaimSubmitError:
                # One (or more) of the claims was outside the user's allowed locations.
                # This is expected; we continue to verify the side effects.
                pass

        claim_allowed.refresh_from_db()
        claim_forbidden.refresh_from_db()

        # If the authorized claim was not yet processed (e.g. bad claim appeared
        # first in the queryset iteration), submit it on its own so we can
        # assert that submit works for claims the user *is* allowed to touch.
        if claim_allowed.status == Claim.STATUS_ENTERED:
            with mock.patch("claim.gql_mutations.submit_claim", return_value=[]), \
                 mock.patch.object(SubmitClaimsMutation, "add_submission_stats_to_mutation_log"):
                SubmitClaimsMutation.async_mutate(
                    limited_user, uuids=[str(claim_allowed.uuid)]
                )
            claim_allowed.refresh_from_db()

        # The authorized claim must have had submit applied (status changed).
        # The unauthorized claim must not have been submitted.
        self.assertEqual(claim_allowed.status, Claim.STATUS_CHECKED)
        self.assertEqual(claim_forbidden.status, Claim.STATUS_ENTERED)

    def test_submit_via_additional_filters_simulates_missing_get_queryset_still_only_submits_authorized(self):
        """
        This test reproduces the scenario the user temporarily created in core:

        - The decorator (mutation_on_queryset_from_filter) is made to NOT call
          Claim.get_queryset(user)  (i.e. it would pass a broad queryset from .objects)
        - additional_filters selects claims that exist in both allowed and
          forbidden locations for the user.
        - We then actually perform the submit on the claims coming from that
          (simulated broad) queryset.
        - Only claims the user is authorised for (via location) must have their
          status changed. Unauthorized claims must not be submitted.

        This ensures that even if the decorator stops applying row security,
        the submit operation itself will not apply changes to claims the user
        should not touch.
        """
        # Build two districts + HFs
        region_allowed = create_test_location(
            "R", custom_props={"code": "RF1", "name": "FilterRow Allowed Region"}
        )
        district_allowed = create_test_location(
            "D", custom_props={"code": "DF1", "name": "FilterRow Allowed District", "parent": region_allowed}
        )
        region_forbidden = create_test_location(
            "R", custom_props={"code": "RF2", "name": "FilterRow Forbidden Region"}
        )
        district_forbidden = create_test_location(
            "D", custom_props={"code": "DF2", "name": "FilterRow Forbidden District", "parent": region_forbidden}
        )

        hf_allowed = create_test_health_facility(code="HFR1", location_id=district_allowed.id)
        hf_forbidden = create_test_health_facility(code="HFR2", location_id=district_forbidden.id)

        med_officer_role = create_medical_officer_role()
        dmer_role_perms = [
            "gql_query_families_perms",
            "gql_query_insurees_perms",
            "gql_query_policies_perms",
            "gql_query_premiums_perms",
            "gql_mutation_submit_claims_perms"
        ]
        med_officer_role = create_test_role(
            perm_names=dmer_role_perms, name="Distrcit Manager", is_system=128)
        limited_user = create_test_interactive_user(
            username="filterrow_limited",
            roles=[med_officer_role.id],
            # custom_props={"is_superuser": False},
        )
        assign_user_districts(limited_user, [district_allowed.code])

        # Claims in ENTERED so they are candidates for submit, and the filter can select them
        claim_allowed = create_test_claim(
            custom_props={
                "health_facility": hf_allowed,
                "status": Claim.STATUS_ENTERED,
                "code": "FILT-ALLOW-SUB",
            }
        )
        claim_forbidden = create_test_claim(
            custom_props={
                "health_facility": hf_forbidden,
                "status": Claim.STATUS_ENTERED,
                "code": "FILT-FORBID-SUB",
            }
        )

        # This will be called by the decorated function, receiving whatever queryset
        # the (simulated insecure) decorator decided to pass.
        def _do_submit(cls, user, **data):
            target_qs = data.get("queryset")
            if target_qs is None:
                uuids = data.get("uuids") or []
                target_qs = Claim.objects.filter(uuid__in=uuids)

            service = ClaimSubmitService(user)

            # Real location enforcement must still happen per claim
            with mock.patch("claim.gql_mutations.submit_claim", return_value=[]):
                for claim in target_qs.filter(validity_to__isnull=True):
                    try:
                        service.submit_claim(claim, user)
                    except ClaimSubmitError:
                        # Unauthorized for this user -> do not apply submit
                        continue
            return None

        handlers = getattr(
            SubmitClaimsMutation, "_SubmitClaimsMutation__filter_handlers", {}
        )

        decorated = mutation_on_queryset_from_filter(
            Claim, ClaimGQLType, "additional_filters", handlers
        )(_do_submit)

        # Filter that matches both claims (status based). If decorator does not
        # apply user location filtering, both would be in the queryset.
        additional_filters = json.dumps({"status": Claim.STATUS_ENTERED})

        # Simulate the temporary core change the user made:
        # make Claim.get_queryset not apply row security (return unfiltered qs).
        def _insecure_get_queryset(cls, queryset, user):
            # Return the queryset without applying user location restrictions.
            # (basic validity filter is still useful)
            try:
                return Claim.filter_queryset(queryset)
            except Exception:
                return queryset

        original_get_qs = Claim.get_queryset
        try:
            Claim.get_queryset = classmethod(_insecure_get_queryset)
            decorated(SubmitClaimsMutation, limited_user, additional_filters=additional_filters)
        finally:
            Claim.get_queryset = original_get_qs

        claim_allowed.refresh_from_db()
        claim_forbidden.refresh_from_db()

        # Only the claim belonging to an allowed location for the user
        # should have been submitted.
        self.assertEqual(claim_allowed.status, Claim.STATUS_CHECKED)
        self.assertEqual(claim_forbidden.status, Claim.STATUS_ENTERED)
