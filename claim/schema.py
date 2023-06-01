from core.models import Officer
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
        user_health_facility=graphene.String()
    )
    claim_officers = DjangoFilterConnectionField(
        OfficerGQLType, search=graphene.String()
    )

    validate_claim_code = graphene.Field(
        graphene.Boolean,
        claim_code=graphene.String(required=True),
        description="Checks that the specified claim code is unique."
    )

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
        queryset = ClaimAdmin.objects.all()
        user_health_facility = kwargs.get("user_health_facility", None)
        if user_health_facility:
            user_health_facility = ast.literal_eval(user_health_facility)
            queryset = queryset.filter(
                health_facility__uuid__in=user_health_facility
            )
        if search:
            queryset = queryset.filter(
                Q(code__icontains=search)
                | Q(last_name__icontains=search)
                | Q(other_names__icontains=search)
            )
        return queryset

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
