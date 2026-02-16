from core.test_helpers import create_test_interactive_user
from rest_framework import status
from rest_framework.test import APITestCase
from django.conf import settings
from claim.test_helpers import (
    create_test_claim,
    create_test_claimitem,
    create_test_claimservice,
)
from core.models.openimis_graphql_test_case import BaseTestContext


class ReportAPITests(APITestCase):

    admin_user = None
    admin_token = None

    test_claim = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.admin_user = create_test_interactive_user(username="testClaimAdmin")
        cls.user_context = BaseTestContext(user=cls.admin_user)
        cls.admin_token = cls.user_context.get_jwt()
        cls.test_claim = create_test_claim()
        create_test_claimservice(cls.test_claim)
        create_test_claimitem(cls.test_claim)

    def test_print_claim(self):
        URL = f"/{settings.SITE_ROOT()}claim/print/?uuid={self.test_claim.uuid}"
        headers = {"HTTP_AUTHORIZATION": f"Bearer {self.admin_token}"}
        response = self.client.get(URL, format="json", **headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


# todo expand tests
