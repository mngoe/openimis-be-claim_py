import datetime
import graphene
from core import prefix_filterset, ExtendedConnection, filter_validity
from graphene.utils.deduplicator import deflate
from graphene_django import DjangoObjectType
from insuree.schema import InsureeGQLType
from location.schema import HealthFacilityGQLType
from medical.schema import DiagnosisGQLType
from claim_batch.schema import BatchRunGQLType
from .apps import ClaimConfig
from claim.models import (ClaimDedRem, Prescriber,Speciality,Status,PrescriberMutation,Claim, ClaimAdmin, Feedback, ClaimItem, ClaimService, ClaimAttachment,
                          ClaimAttachmentType, ClaimServiceService, ClaimServiceItem)
from django.utils.translation import gettext as _
from django.core.exceptions import PermissionDenied



class SpecialityGQLType(DjangoObjectType):
    class Meta:
        model = Speciality
        filter_fields = {
            "uuid": ["exact","icontains"],
            "code": ["exact", "icontains"],
            "speciality": ["exact", "icontains"]
        }
        interfaces = (graphene.relay.Node,)
        connection_class = ExtendedConnection


class StatusGQLType(DjangoObjectType):
    class Meta:
        model = Status
        filter_fields = {
            "code": ["exact"],
            "status": ["exact", "icontains"]
        }
        interfaces = (graphene.relay.Node,)
        connection_class = ExtendedConnection


class PrescriberGQLType(DjangoObjectType):
    client_mutation_id = graphene.String()
    authorized_health_facilities = graphene.List(HealthFacilityGQLType)

    def resolve_authorized_health_facilities(self, info):
        return self.authorized_health_facilities.all()

    def resolve_main_health_facility(self, info):
        if "health_facility_loader" in info.context.dataloaders:
            return info.context.dataloaders["health_facility_loader"].load(self.main_health_facility_id)

    class Meta:
        model = Prescriber
        filter_fields = {
            "uuid": ["exact"],
            "code": ["exact", "icontains"],
            "nin": ["exact", "istartswith","icontains"],
            "last_name": ["exact", "icontains"],
            "other_names": ["exact", "icontains"],
            "phone": ["exact","icontains","istartswith"],
            "entry_date": ["exact", "lt", "lte", "gt", "gte"],
            "release_date": ["exact", "lt", "lte", "gt", "gte"],
            **prefix_filterset("main_health_facility__", HealthFacilityGQLType._meta.filter_fields),
            **prefix_filterset("speciality__", SpecialityGQLType._meta.filter_fields),
            **prefix_filterset("status__", StatusGQLType._meta.filter_fields)
        }
        interfaces = (graphene.relay.Node,)
        connection_class = ExtendedConnection

    def resolve_client_mutation_id(self, info):
        prescriber_mutation = self.mutations.select_related('mutation').filter(mutation__status=0).first()
        return prescriber_mutation.mutation.client_mutation_id if prescriber_mutation else None
    

class ClaimDedRemGQLType(DjangoObjectType):
    """
    Details about Claim demands and remunerated amounts
    """
    class Meta:
        model = ClaimDedRem
        interfaces = (graphene.relay.Node,)


class ClaimAdminGQLType(DjangoObjectType):
    """
    Details about a Claim Administrator
    """

    class Meta:
        model = ClaimAdmin
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "uuid": ["exact"],
            "code": ["exact", "icontains"],
            "last_name": ["exact", "icontains"],
            "other_names": ["exact", "icontains"],
            **prefix_filterset("health_facility__", HealthFacilityGQLType._meta.filter_fields),
        }
        connection_class = ExtendedConnection

    @classmethod
    def get_queryset(cls, queryset, info):
        queryset = queryset.filter(*filter_validity())
        return queryset


class ClaimGQLType(DjangoObjectType):
    """
    Main element for a Claim. It can contain items and/or services.
    The filters are possible on BatchRun, Insuree, HealthFacility, Admin and ICD in addition to the Claim fields
    themselves.
    """

    attachments_count = graphene.Int()
    client_mutation_id = graphene.String()
    date_processed_to = graphene.Date()
    restore_id = graphene.Int()
    
    def resolve_insuree(self, info):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        if "insuree_loader" in info.context.dataloaders and self.insuree_id:
            return info.context.dataloaders["insuree_loader"].load(self.insuree_id)
        return self.insuree

    def resolve_health_facility(self, info):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        if (
            "health_facility_loader" in info.context.dataloaders
            and self.health_facility_id
        ):
            return info.context.dataloaders["health_facility_loader"].load(
                self.health_facility_id
            )
        return self.health_facility
    
    def resolve_restore_id(self, info):
        return self.restore_id
        
        
    class Meta:
        model = Claim
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "uuid": ["exact"],
            "code": ["exact", "istartswith", "icontains", "iexact"],
            "status": ["exact", "gt"],
            "date_claimed": ["exact", "lt", "lte", "gt", "gte"],
            "date_from": ["exact", "lt", "lte", "gt", "gte"],
            "date_to": ["exact", "lt", "lte", "gt", "gte"],
            "date_processed": ["exact", "lt", "lte", "gt", "gte"],
            "feedback_status": ["exact"],
            "review_status": ["exact"],
            "claimed": ["exact", "lt", "lte", "gt", "gte"],
            "approved": ["exact", "lt", "lte", "gt", "gte"],
            "visit_type": ["exact"],
            "attachments_count__value": ["exact", "lt", "lte", "gt", "gte"],
            "pre_authorization": ["exact"],
            "is_pre_authorization": ["exact"],
            "code_pre_authorization":["exact", "istartswith", "icontains", "iexact"],
            "date_pre_authorization":["exact", "lt", "lte", "gt", "gte"],
            "status_pre_authorization": ["exact", "in"],
            **prefix_filterset("icd__", DiagnosisGQLType._meta.filter_fields),
            **prefix_filterset("admin__", ClaimAdminGQLType._meta.filter_fields),
            **prefix_filterset("health_facility__", HealthFacilityGQLType._meta.filter_fields),
            **prefix_filterset("insuree__", InsureeGQLType._meta.filter_fields),
            **prefix_filterset("prescriber__", PrescriberGQLType._meta.filter_fields),
            **prefix_filterset("batch_run__", BatchRunGQLType._meta.filter_fields)
        }
        connection_class = ExtendedConnection

    def resolve_attachments_count(self, info):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        return self.attachments.filter(legacy_id__isnull=True).filter(validity_to__isnull=True).count()

    def resolve_items(self, info):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        return self.items.filter(legacy_id__isnull=True).filter(validity_to__isnull=True)

    def resolve_services(self, info):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        return self.services.filter(legacy_id__isnull=True).filter(validity_to__isnull=True)

    def resolve_client_mutation_id(self, info):
        if not info.context.user.has_perms(ClaimConfig.gql_query_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        claim_mutation = self.mutations.select_related(
            'mutation').filter(mutation__status=0).first()
        return claim_mutation.mutation.client_mutation_id if claim_mutation else None

    @classmethod
    def get_queryset(cls, queryset, info):
        return Claim.get_queryset(queryset, info).all()


class ClaimAttachmentGQLType(DjangoObjectType):
    doc = graphene.String()

    class Meta:
        model = ClaimAttachment
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "type": ["exact", "icontains"],
            "title": ["exact", "icontains"],
            "date": ["exact", "lt", "lte", "gt", "gte"],
            "filename": ["exact", "icontains"],
            "mime": ["exact", "icontains"],
            "general_type": ["exact", "icontains"],
            "url": ["exact", "icontains"],
            **prefix_filterset("claim__", ClaimGQLType._meta.filter_fields),
        }
        connection_class = ExtendedConnection

    @classmethod
    def get_queryset(cls, queryset, info):
        queryset = queryset.filter(*filter_validity())
        return queryset


class ClaimAttachmentTypeGQLType(DjangoObjectType):
    class Meta:
        model = ClaimAttachmentType
        interfaces = (graphene.relay.Node,)
        filter_fields = {
            "id": ["exact"],
            "claim_general_type": ["exact"]
        }
        connection_class = ExtendedConnection


class FeedbackGQLType(DjangoObjectType):
    class Meta:
        model = Feedback


class ClaimItemGQLType(DjangoObjectType):
    """
    Contains the items within a specific Claim
    """

    class Meta:
        model = ClaimItem


class ClaimServiceGQLType(DjangoObjectType):
    """
    Contains the services within a specific Claim
    """

    class Meta:
        model = ClaimService

class ClaimServiceServiceGQLType(DjangoObjectType):
    """
    Contains the Claim services within a specific Claim
    """

    class Meta:
        model = ClaimServiceService

class ClaimServiceItemGQLType(DjangoObjectType):
    """
    Contains the Claim services within a specific Claim
    """

    class Meta:
        model = ClaimServiceItem
