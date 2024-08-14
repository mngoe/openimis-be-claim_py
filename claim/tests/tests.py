import base64
import json
from dataclasses import dataclass
from core.models import User
from core.models.openimis_graphql_test_case import openIMISGraphQLTestCase

from core.utils import filter_validity
from core.test_helpers import create_test_interactive_user
from django.conf import settings
from graphene_django.utils.testing import GraphQLTestCase
from graphql_jwt.shortcuts import get_token
#credits https://docs.graphene-python.org/projects/django/en/latest/testing/
from claim import schema as claim_schema
from graphene.test import Client
from graphene import Schema

from claim.models import Claim, ClaimAdmin


from policy.models import Policy
from policy.test_helpers import create_test_policy2
from product.test_helpers import create_test_product, create_test_product_service
from core.test_helpers import create_test_officer
from insuree.test_helpers import create_test_insuree
from location.models import Location
from medical.test_helpers import create_test_service
from medical_pricelist.test_helpers import add_service_to_hf_pricelist

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
        cls.claim_admin = ClaimAdmin.objects.filter(*filter_validity()).first()
        cls.svc_pl_detail = add_service_to_hf_pricelist(cls.service, hf_id = cls.claim_admin.health_facility.id )
        cls.product_service = create_test_product_service(cls.product, cls.service, custom_props={"limit_no_adult": 20})
        
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
                healthFacilityId: {self.claim_admin.health_facility.id}
                visitType: "O"
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
                healthFacilityId: {self.claim_admin.health_facility.id}
                visitType: "O"
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