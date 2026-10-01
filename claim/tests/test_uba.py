"""
UBA coverage for the claim module.

Two independent questions, and the tests keep them apart because the implementation does:

* the *rights* side - `User.has_perms(right, access_requirements=...)`: a right sitting in
  the UBA bag of a role is granted only on a health facility the user holds a CLAIM_ADMIN
  link on, and only for the rights actually in that bag;
* the *rows* side - `Claim.get_queryset`: a claim administrator sees the claims of the
  facilities they are linked to and no others. This used to be driven by
  `InteractiveUser.health_facility_id`, which could only ever express one facility.
"""
from django.core.cache import cache
from django.test import TestCase

from claim.apps import ClaimConfig
from claim.models import Claim, ClaimAdmin, Feedback
from claim.test_helpers import create_test_claim
from core import datetime, datetimedelta
from core.apps import CLAIM_ADMIN_UBA_LINK_TYPE, HEALTH_FACILITY_MODEL
from core.models import UserBusinessAccess
from core.test_helpers import (
    create_test_interactive_user,
    create_test_role,
    create_test_user_business_access,
)
from location.test_helpers import create_test_health_facility, create_test_village


def claim_rights():
    """
    Every right the claim module declares, read off `ClaimConfig` rather than hardcoded so
    that a right added to the module is covered without touching this file. Returns
    `(config attribute, right)` pairs, the attribute only being there to name the subtest.
    """
    rights = set()
    for name in dir(ClaimConfig):
        if not name.endswith("_perms"):
            continue
        for right in getattr(ClaimConfig, name) or []:
            rights.add((name, str(right)))
    return sorted(rights)


class ClaimAdminUbaRightsTest(TestCase):
    """A CLAIM_ADMIN link grants the UBA rights on that facility, and only there."""

    @classmethod
    def setUpTestData(cls):
        cls.village = create_test_village({"name": "ClaimUba"})
        cls.district = cls.village.parent.parent
        cls.linked_hf = create_test_health_facility(
            "UBAHF1", cls.district.id, custom_props={"code": "UBAHF1"})
        cls.other_hf = create_test_health_facility(
            "UBAHF2", cls.district.id, custom_props={"code": "UBAHF2"})
        cls.rights = claim_rights()

        # one role carrying every claim right in its UBA bag, so the tests can walk the
        # rights without rebuilding a role per right
        cls.all_rights_role = create_test_role(
            name="UBA claim all rights",
            uba_rights=sorted({int(right) for _, right in cls.rights}),
        )
        cls.claim_admin_user = create_test_interactive_user(
            username="ubaclaimadmin", roles=[cls.all_rights_role.id])
        cls.link = create_test_user_business_access(
            user=cls.claim_admin_user,
            business_object=cls.linked_hf,
            link_type=CLAIM_ADMIN_UBA_LINK_TYPE,
        )

        # a second role carrying a single right, to show the link does not hand over the
        # whole module
        cls.one_right = ClaimConfig.gql_query_claims_perms[0]
        cls.one_right_role = create_test_role(
            name="UBA claim one right", uba_rights=[int(cls.one_right)])
        cls.one_right_user = create_test_interactive_user(
            username="ubaclaimone", roles=[cls.one_right_role.id])
        create_test_user_business_access(
            user=cls.one_right_user,
            business_object=cls.linked_hf,
            link_type=CLAIM_ADMIN_UBA_LINK_TYPE,
        )

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _map(self, health_facility, link_type=CLAIM_ADMIN_UBA_LINK_TYPE):
        return [HEALTH_FACILITY_MODEL, health_facility.uuid, link_type]

    # --- a UBA right is never global ------------------------------------------- #

    def test_no_claim_right_is_granted_without_a_business_map(self):
        for name, right in self.rights:
            with self.subTest(right=right, config=name):
                self.assertFalse(
                    self.claim_admin_user.has_perms([right]),
                    f"{name} ({right}) leaked into the global bag",
                )

    # --- granted on the linked facility ---------------------------------------- #

    def test_every_claim_right_is_granted_on_the_linked_health_facility(self):
        for name, right in self.rights:
            with self.subTest(right=right, config=name):
                self.assertTrue(
                    self.claim_admin_user.has_perms(
                        [right], access_requirements=self._map(self.linked_hf)),
                    f"{name} ({right}) denied on the linked health facility",
                )

    def test_no_claim_right_is_granted_on_another_health_facility(self):
        for name, right in self.rights:
            with self.subTest(right=right, config=name):
                self.assertFalse(
                    self.claim_admin_user.has_perms(
                        [right], access_requirements=self._map(self.other_hf)),
                    f"{name} ({right}) granted on a health facility with no link",
                )

    # --- only the rights in the bag -------------------------------------------- #

    def test_only_the_rights_in_the_uba_bag_are_granted_on_the_linked_facility(self):
        granted = self._map(self.linked_hf)
        self.assertTrue(self.one_right_user.has_perms([self.one_right], access_requirements=granted))
        for name, right in self.rights:
            if right == self.one_right:
                continue
            with self.subTest(right=right, config=name):
                self.assertFalse(
                    self.one_right_user.has_perms([right], access_requirements=granted),
                    f"{name} ({right}) granted although it is not in the role's UBA bag",
                )

    # --- the credential is part of the demand ---------------------------------- #

    def test_another_credential_on_the_same_facility_grants_nothing(self):
        user = create_test_interactive_user(
            username="ubaclaimwrongcred", roles=[self.all_rights_role.id])
        create_test_user_business_access(
            user=user, business_object=self.linked_hf, link_type="ENROLMENT")
        self.assertFalse(user.has_perms(
            [self.one_right], access_requirements=self._map(self.linked_hf)))

    def test_a_map_naming_no_credential_is_satisfied_by_the_claim_admin_link(self):
        # the registry knows CLAIM_ADMIN is declared on the health facility, so a caller
        # may leave the credential out and still be answered
        self.assertTrue(self.claim_admin_user.has_perms(
            [self.one_right], access_requirements=[HEALTH_FACILITY_MODEL, self.linked_hf.uuid]))

    def test_a_user_without_any_link_is_denied_everywhere(self):
        user = create_test_interactive_user(
            username="ubaclaimnolink", roles=[self.all_rights_role.id])
        for name, right in self.rights:
            with self.subTest(right=right, config=name):
                self.assertFalse(user.has_perms(
                    [right], access_requirements=self._map(self.linked_hf)))

    # --- the link has to be live ----------------------------------------------- #

    def test_an_inactive_link_grants_nothing(self):
        self.link.active = False
        self.link.save(user=self.claim_admin_user)
        self.assertFalse(self.claim_admin_user.has_perms(
            [self.one_right], access_requirements=self._map(self.linked_hf)))
        self.link.active = True
        self.link.save(user=self.claim_admin_user)

    def test_an_expired_link_grants_nothing(self):
        self.link.date_valid_to = datetime.datetime.now() - datetimedelta(days=1)
        self.link.save(user=self.claim_admin_user)
        self.assertFalse(self.claim_admin_user.has_perms(
            [self.one_right], access_requirements=self._map(self.linked_hf)))
        self.link.date_valid_to = None
        self.link.save(user=self.claim_admin_user)


class ClaimAdminUbaRowSecurityTest(TestCase):
    """
    `Claim.get_queryset` narrows a claim administrator to the facilities they are linked
    to. This is the entry point the REST/FHIR API uses too (it calls
    `Claim.get_queryset(None, request.user)`), so the rule is not GraphQL only.

    The narrowing is AND'ed with the district filter the user already had, so every user
    here is assigned to the district holding both facilities: a credential restricts
    within what the user was granted, it never reaches outside it.
    """

    @classmethod
    def setUpTestData(cls):
        from core.services.userServices import create_or_update_user_districts

        cls.village = create_test_village({"name": "ClaimUbaRows"})
        cls.district = cls.village.parent.parent
        cls.linked_hf = create_test_health_facility(
            "UBARW1", cls.district.id, custom_props={"code": "UBARW1"})
        cls.other_hf = create_test_health_facility(
            "UBARW2", cls.district.id, custom_props={"code": "UBARW2"})

        cls.role = create_test_role(
            name="UBA claim rows", uba_rights=[int(ClaimConfig.gql_query_claims_perms[0])])
        cls.linked_user = create_test_interactive_user(
            username="ubarowslinked", roles=[cls.role.id])
        cls.plain_user = create_test_interactive_user(
            username="ubarowsplain", roles=[cls.role.id])
        for user in (cls.linked_user, cls.plain_user):
            create_or_update_user_districts(user.i_user, [cls.district.id], -1)

        create_test_user_business_access(
            user=cls.linked_user,
            business_object=cls.linked_hf,
            link_type=CLAIM_ADMIN_UBA_LINK_TYPE,
        )

        cls.linked_claim = create_test_claim(
            {"health_facility_id": cls.linked_hf.id, "code": "UBACLM1"})
        cls.other_claim = create_test_claim(
            {"health_facility_id": cls.other_hf.id, "code": "UBACLM2"})

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _claims_for(self, user):
        return set(
            Claim.get_queryset(Claim.objects.filter(code__startswith="UBACLM"), user)
            .values_list("code", flat=True)
        )

    def test_a_claim_admin_only_sees_the_claims_of_the_linked_facility(self):
        self.assertEqual({"UBACLM1"}, self._claims_for(self.linked_user))

    def test_a_user_without_a_link_keeps_the_district_scope(self):
        # no credential, so the plain district filter stands and both facilities of the
        # district are in scope: a link narrows, its absence does not
        self.assertEqual({"UBACLM1", "UBACLM2"}, self._claims_for(self.plain_user))

    def test_a_second_link_widens_to_both_facilities(self):
        create_test_user_business_access(
            user=self.linked_user,
            business_object=self.other_hf,
            link_type=CLAIM_ADMIN_UBA_LINK_TYPE,
        )
        self.assertEqual({"UBACLM1", "UBACLM2"}, self._claims_for(self.linked_user))

    def test_a_link_outside_the_assigned_districts_grants_nothing(self):
        # the two filters are AND'ed: the credential cannot reach past the districts the
        # user is assigned to
        far_village = create_test_village({"name": "ClaimUbaFar"})
        far_hf = create_test_health_facility(
            "UBARW3", far_village.parent.parent.id, custom_props={"code": "UBARW3"})
        far_claim = create_test_claim(
            {"health_facility_id": far_hf.id, "code": "UBACLM3"})
        user = create_test_interactive_user(username="ubarowsfar", roles=[self.role.id])
        from core.services.userServices import create_or_update_user_districts
        create_or_update_user_districts(user.i_user, [self.district.id], -1)
        create_test_user_business_access(
            user=user, business_object=far_hf, link_type=CLAIM_ADMIN_UBA_LINK_TYPE)
        self.assertEqual(set(), self._claims_for(user))
        self.assertNotIn(far_claim.code, self._claims_for(self.plain_user))

    def test_the_claim_list_is_open_to_a_linked_claim_admin(self):
        # the search right only sits in the UBA bag: the list names no facility, so the
        # CLAIM_ADMIN link is what opens it, get_queryset then scoping the rows
        from claim.schema import _can_search_claims
        self.assertTrue(_can_search_claims(self.linked_user))

    def test_the_claim_list_is_closed_without_a_link(self):
        # without a link get_queryset would fall back on the whole district
        from claim.schema import _can_search_claims
        self.assertFalse(_can_search_claims(self.plain_user))

    def test_the_claim_admin_table_is_narrowed_the_same_way(self):
        # ClaimAdmin hangs off the health facility too, and its GQL type now routes
        # through the model, so the same credential narrows the picker
        ClaimAdmin.objects.create(
            code="UBACA1", last_name="Linked", other_names="Admin",
            health_facility=self.linked_hf, audit_user_id=-1, validity_from="2019-01-01")
        ClaimAdmin.objects.create(
            code="UBACA2", last_name="Other", other_names="Admin",
            health_facility=self.other_hf, audit_user_id=-1, validity_from="2019-01-01")
        visible = set(
            ClaimAdmin.get_queryset(
                ClaimAdmin.objects.filter(code__startswith="UBACA"), self.linked_user
            ).values_list("code", flat=True)
        )
        self.assertEqual({"UBACA1"}, visible)

    def test_feedback_is_narrowed_the_same_way(self):
        # a Feedback carries no health facility of its own, it reaches one through its
        # claim, so the filter has to travel 'claim__health_facility__location'
        linked = Feedback.objects.create(
            claim=self.linked_claim, audit_user_id=-1, validity_from="2019-01-01")
        Feedback.objects.create(
            claim=self.other_claim, audit_user_id=-1, validity_from="2019-01-01")
        visible = Feedback.get_queryset(
            Feedback.objects.filter(claim__code__startswith="UBACLM"), self.linked_user)
        self.assertEqual([linked.id], [row.id for row in visible])
