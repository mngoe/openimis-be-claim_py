import graphene
from enum import Enum

from core.models import Officer
from insuree.models import Insuree
from location.models import HealthFacility, Location
from .services import check_unique_claim_code
import django
from core.schema import signal_mutation_module_validate
from django.db.models import OuterRef, Subquery, Avg, Q
import graphene_django_optimizer as gql_optimizer
from core.schema import OrderedDjangoFilterConnectionField, OfficerGQLType
from core import filter_validity
from django.db.models.functions import Cast

from .models import ClaimMutation
from django.utils.translation import gettext as _
from graphene_django.filter import DjangoFilterConnectionField
import ast

# We do need all queries and mutations in the namespace here.
from .gql_queries import *  # lgtm [py/polluting-import]
from .gql_mutations import *  # lgtm [py/polluting-import]


class Query(graphene.ObjectType):
    claims = OrderedDjangoFilterConnectionField(
        ClaimGQLType,
        diagnosisVariance=graphene.Int(),
        code_is_not=graphene.String(),
        orderBy=graphene.List(of_type=graphene.String),
        items=graphene.List(of_type=graphene.String),
        services=graphene.List(of_type=graphene.String),
        json_ext=graphene.JSONString(),
        attachment_status=graphene.Int(required=False)
    )

    claim = graphene.Field(
        ClaimGQLType, id=graphene.Int(), uuid=graphene.UUID()
    )

    claim_attachments = DjangoFilterConnectionField(
        ClaimAttachmentGQLType
    )
    claim_admins = DjangoFilterConnectionField(
        ClaimAdminGQLType,
        search=graphene.String(),
        region_uuid=graphene.String(),
        district_uuid=graphene.String()
    )
    claim_officers = DjangoFilterConnectionField(
        OfficerGQLType, search=graphene.String()
    )

    insuree_name_by_chfid = graphene.String(
        chfId=graphene.String(required=True)
    )

    validate_claim_code = graphene.Field(
        graphene.Boolean,
        claim_code=graphene.String(required=True),
        description="Checks that the specified claim code is unique."
    )

    def resolve_insuree_name_by_chfid(self, info, **kwargs):
        if not info.context.user.has_perms(ClaimConfig.gql_mutation_create_claims_perms)\
                and not info.context.user.has_perms(ClaimConfig.gql_mutation_update_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        chf_id = kwargs.get('chfId')
        insuree = Insuree.objects\
            .filter(validity_to__isnull=True, chf_id=chf_id)\
            .values('last_name', 'other_names')\
            .first()
        if insuree:
            insuree_name = f"{insuree['other_names']} {insuree['last_name']}"
        else:
            insuree_name = ""
        return insuree_name

    def resolve_validate_claim_code(self, info, **kwargs):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        errors = check_unique_claim_code(code=kwargs['claim_code'])
        return False if errors else True

    def resolve_claim(self, info, id=None, uuid=None, **kwargs):
        if (
            not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms)
            and settings.ROW_SECURITY
        ):
            raise PermissionDenied(_("unauthorized"))

        if id is not None:
            return Claim.objects.get(id=id)
        if uuid is not None:
            return Claim.objects.get(uuid=uuid)

    def resolve_claims(self, info, **kwargs):
        if (
            not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms)
            and settings.ROW_SECURITY
        ):
            raise PermissionDenied(_("unauthorized"))
        query = Claim.objects
        code_is_not = kwargs.get("code_is_not", None)
        if code_is_not:
            query = query.exclude(code=code_is_not)
        variance = kwargs.get("diagnosisVariance", None)

        items = kwargs.get("items", None)
        services = kwargs.get("services", None)

        if items:
            query = query.filter(items__item__code__in=items)

        if services:
            query = query.filter(services__service__code__in=services)

        attachment_status = kwargs.get("attachment_status", 0)

        class AttachmentStatusEnum(Enum):
            NONE = 0
            WITH = 1
            WITHOUT = 2

        if attachment_status == AttachmentStatusEnum.WITH.value:
            query = query.filter(attachments__isnull=False)
        elif attachment_status == AttachmentStatusEnum.WITHOUT.value:
            query = query.filter(attachments__isnull=True)

        json_ext = kwargs.get("json_ext", None)

        if json_ext:
            query = query.filter(json_ext__jsoncontains=json_ext)

        if variance:
            from core import datetime, datetimedelta

            last_year = datetime.date.today() + datetimedelta(years=-1)
            diag_avg = (
                Claim.objects.filter(*filter_validity(**kwargs))
                .filter(date_claimed__gt=last_year)
                .values("icd__code")
                .filter(icd__code=OuterRef("icd__code"))
                .annotate(diag_avg=Avg("approved"))
                .values("diag_avg")
            )
            variance_filter = Q(claimed__gt=(
                1 + variance / 100) * Subquery(diag_avg))
            if not ClaimConfig.gql_query_claim_diagnosis_variance_only_on_existing:
                diags = (
                    Claim.objects.filter(*filter_validity(**kwargs))
                    .filter(date_claimed__gt=last_year)
                    .values("icd__code")
                    .distinct()
                )
                variance_filter = variance_filter | ~Q(icd__code__in=diags)
            query = query.filter(variance_filter)

        from location.models import Location
        user_districts = UserDistrict.get_user_districts(info.context.user._u)
        query = query.filter(
            Q(health_facility__location__in=Location.objects.filter(uuid__in=user_districts.values_list('location__uuid', flat=True))) | Q(
                health_facility__location__in=Location.objects.filter(uuid__in=user_districts.values_list('location__parent__uuid', flat=True))))

        return gql_optimizer.query(query.all(), info)

    def resolve_claim_attachments(self, info, **kwargs):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms):
            raise PermissionDenied(_("unauthorized"))

    def resolve_claim_admins(
            self,
            info,
            search=None,
            **kwargs
    ):
        if not info.context.user.has_perms(
                ClaimConfig.gql_query_claim_admins_perms
        ):
            raise PermissionDenied(_("unauthorized"))

        hf_filters = [*filter_validity(**kwargs)]
        district_uuid = kwargs.get('district_uuid', None)
        region_uuid = kwargs.get('region_uuid', None)
        if district_uuid is not None:
            hf_filters += [Q(location__uuid=district_uuid)]
        if region_uuid is not None:
            hf_filters += [Q(location__parent__uuid=region_uuid)]
        if settings.ROW_SECURITY:
            dist = UserDistrict.get_user_districts(info.context.user._u)
            hf_filters += [Q(location__id__in=[l.location_id for l in dist])]
        user_health_facility = HealthFacility.objects.filter(*hf_filters)

        filters = [*filter_validity(**kwargs)]
        if user_health_facility:
            filters += [Q(health_facility__in=user_health_facility)]

        if search:
            filters += [Q(code__icontains=search) |
                        Q(last_name__icontains=search) |
                        Q(other_names__icontains=search)]

        return ClaimAdmin.objects.filter(*filters)

    def resolve_claim_officers(self, info, search=None, **kwargs):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claim_officers_perms):
            raise PermissionDenied(_("unauthorized"))

        qs = Officer.objects

        if search is not None:
            qs = qs.filter(
                Q(code__icontains=search)
                | Q(last_name__icontains=search)
                | Q(other_names__icontains=search)
            )
        return qs


class Mutation(graphene.ObjectType):
    create_claim = CreateClaimMutation.Field()
    update_claim = UpdateClaimMutation.Field()
    create_claim_attachment = CreateAttachmentMutation.Field()
    update_claim_attachment = UpdateAttachmentMutation.Field()
    delete_claim_attachment = DeleteAttachmentMutation.Field()
    submit_claims = SubmitClaimsMutation.Field()
    select_claims_for_feedback = SelectClaimsForFeedbackMutation.Field()
    deliver_claim_feedback = DeliverClaimFeedbackMutation.Field()
    bypass_claims_feedback = BypassClaimsFeedbackMutation.Field()
    skip_claims_feedback = SkipClaimsFeedbackMutation.Field()
    select_claims_for_review = SelectClaimsForReviewMutation.Field()
    save_claim_review = SaveClaimReviewMutation.Field()
    deliver_claims_review = DeliverClaimsReviewMutation.Field()
    bypass_claims_review = BypassClaimsReviewMutation.Field()
    skip_claims_review = SkipClaimsReviewMutation.Field()
    process_claims = ProcessClaimsMutation.Field()
    delete_claims = DeleteClaimsMutation.Field()


def on_claim_mutation(sender, **kwargs):
    uuids = kwargs["data"].get("uuids", [])
    if not uuids:
        uuid = kwargs["data"].get("claim_uuid", None)
        uuids = [uuid] if uuid else []
    if not uuids:
        return []
    impacted_claims = Claim.objects.filter(uuid__in=uuids).all()
    for claim in impacted_claims:
        ClaimMutation.objects.create(
            claim=claim, mutation_id=kwargs["mutation_log_id"])
    return []


def bind_signals():
    signal_mutation_module_validate["claim"].connect(on_claim_mutation)
