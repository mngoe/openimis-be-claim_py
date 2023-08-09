import json
import logging
import uuid
import pathlib
import base64
import graphene
import graphene_django_optimizer
from django.db.models import OuterRef, Avg, Subquery, Q

from .apps import ClaimConfig
from claim.validations import validate_claim, get_claim_category, validate_assign_prod_to_claimitems_and_services, \
    process_dedrem, approved_amount
from core import filter_validity, assert_string_length
from core.schema import TinyInt, SmallInt, OpenIMISMutation
from core.gql.gql_mutations import mutation_on_uuids_from_filter
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError, PermissionDenied
from django.utils.translation import gettext as _
from graphene import InputObjectType
from location.schema import UserDistrict

from claim.gql_queries import ClaimGQLType
from claim.models import Claim, Feedback, ClaimAttachment, ClaimDedRem
from claim.models import ClaimItem, ClaimService, ClaimDetail, ClaimServiceItem ,ClaimServiceService
from medical.models import Item, Service
from product.models import ProductItemOrService

from claim.utils import process_items_relations, process_services_relations

logger = logging.getLogger(__name__)


class ClaimItemInputType(InputObjectType):
    id = graphene.Int(required=False)
    item_id = graphene.Int(required=True)
    status = TinyInt(required=True)
    qty_provided = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    qty_approved = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_asked = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_adjusted = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_approved = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_valuated = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    explanation = graphene.String(required=False)
    justification = graphene.String(required=False)
    rejection_reason = SmallInt(required=False)

    validity_from_review = graphene.DateTime(required=False)
    validity_to_review = graphene.DateTime(required=False)
    limitation_value = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    limitation = graphene.String(required=False)
    remunerated_amount = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    deductable_amount = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    exceed_ceiling_amount = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_origin = graphene.String(required=False)
    exceed_ceiling_amount_category = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)

class ClaimSubServiceInputType(InputObjectType):
    id = graphene.Int(required=False)
    subServiceCode = graphene.String(required=True)
    qty_provided = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    qty_asked = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_asked = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)


class ClaimSubItemInputType(InputObjectType):
    id = graphene.Int(required=False)
    subItemCode = graphene.String(required=True)
    qty_provided = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    qty_asked = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_asked = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)

class ClaimServiceInputType(InputObjectType):
    id = graphene.Int(required=False)
    legacy_id = graphene.Int(required=False)
    service_id = graphene.Int(required=True)
    status = TinyInt(required=True)
    qty_provided = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    qty_approved = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_asked = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_adjusted = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_approved = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_valuated = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    explanation = graphene.String(required=False)
    justification = graphene.String(required=False)
    rejection_reason = SmallInt(required=False)
    validity_to = graphene.DateTime(required=False)
    validity_from_review = graphene.DateTime(required=False)
    validity_to_review = graphene.DateTime(required=False)
    audit_user_id_review = graphene.Int(required=False)
    limitation_value = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    limitation = graphene.String(max_length=1, required=False)
    policy_id = graphene.Int(required=False)
    remunerated_amount = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    deductable_amount = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False,
        description="deductable is spelled with a, not deductible")
    exceed_ceiling_amount = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    price_origin = graphene.String(max_length=1, required=False)
    exceed_ceiling_amount_category = graphene.Decimal(
        max_digits=18, decimal_places=2, required=False)
    serviceLinked = graphene.List(ClaimSubItemInputType, required=False)
    serviceserviceSet = graphene.List(ClaimSubServiceInputType, required=False)


class FeedbackInputType(InputObjectType):
    id = graphene.Int(required=False, read_only=True)
    care_rendered = graphene.Boolean(required=False)
    payment_asked = graphene.Boolean(required=False)
    drug_prescribed = graphene.Boolean(required=False)
    drug_received = graphene.Boolean(required=False)
    asessment = SmallInt(
        required=False,
        description="Be careful, this field name has a typo")
    officer_id = graphene.Int(required=False)
    feedback_date = graphene.DateTime(required=False)
    validity_from = graphene.DateTime(required=False)
    validity_to = graphene.DateTime(required=False)


class ClaimCodeInputType(graphene.String):

    @staticmethod
    def coerce_string(value):
        assert_string_length(value, 8)
        return value

    serialize = coerce_string
    parse_value = coerce_string

    @staticmethod
    def parse_literal(ast):
        result = graphene.String.parse_literal(ast)
        assert_string_length(result, 8)
        return result


class ClaimGuaranteeIdInputType(graphene.String):

    @staticmethod
    def coerce_string(value):
        assert_string_length(value, 50)
        return value

    serialize = coerce_string
    parse_value = coerce_string

    @staticmethod
    def parse_literal(ast):
        result = graphene.String.parse_literal(ast)
        assert_string_length(result, 50)
        return result


class BaseAttachment:
    id = graphene.String(required=False, read_only=True)
    type = graphene.String(required=False)
    title = graphene.String(required=False)
    date = graphene.Date(required=False)
    filename = graphene.String(required=False)
    mime = graphene.String(required=False)
    url = graphene.String(required=False)


class BaseAttachmentInputType(BaseAttachment, OpenIMISMutation.Input):
    """
    Claim attachment (without the document), used on its own
    """
    claim_uuid = graphene.String(required=True)


class Attachment(BaseAttachment):
    document = graphene.String(required=False)


class ClaimAttachmentInputType(Attachment, InputObjectType):
    """
    Claim attachment, used nested in claim object
    """
    pass


class AttachmentInputType(Attachment, OpenIMISMutation.Input):
    """
    Claim attachment, used on its own
    """
    claim_uuid = graphene.String(required=True)


class ClaimInputType(OpenIMISMutation.Input):
    id = graphene.Int(required=False, read_only=True)
    uuid = graphene.String(required=False)
    code = ClaimCodeInputType(required=True)
    insuree_id = graphene.Int(required=True)
    date_from = graphene.Date(required=True)
    date_to = graphene.Date(required=False)
    icd_id = graphene.Int(required=True)
    icd_1_id = graphene.Int(required=False)
    icd_2_id = graphene.Int(required=False)
    icd_3_id = graphene.Int(required=False)
    icd_4_id = graphene.Int(required=False)
    review_status = TinyInt(required=False)
    date_claimed = graphene.Date(required=True)
    date_processed = graphene.Date(required=False)
    health_facility_id = graphene.Int(required=True)
    batch_run_id = graphene.Int(required=False)
    category = graphene.String(max_length=1, required=False)
    visit_type = graphene.String(max_length=1, required=False)
    admin_id = graphene.Int(required=False)
    guarantee_id = ClaimGuaranteeIdInputType(required=False)
    explanation = graphene.String(required=False)
    adjustment = graphene.String(required=False)
    json_ext = graphene.types.json.JSONString(required=False)

    feedback_available = graphene.Boolean(default=False)
    feedback_status = TinyInt(required=False)
    feedback = graphene.Field(FeedbackInputType, required=False)

    items = graphene.List(ClaimItemInputType, required=False)
    services = graphene.List(ClaimServiceInputType, required=False)



class CreateClaimInputType(ClaimInputType):
    attachments = graphene.List(ClaimAttachmentInputType, required=False)


def reset_claim_before_update(claim):
    claim.date_to = None
    claim.icd_1 = None
    claim.icd_2 = None
    claim.icd_3 = None
    claim.icd_4 = None
    claim.guarantee_id = None
    claim.explanation = None
    claim.adjustment = None
    claim.json_ext = None


def create_file(date, claim_id, document):
    date_iso = date.isoformat()
    root = ClaimConfig.claim_attachments_root_path
    file_dir = '%s/%s/%s/%s' % (
        date_iso[0:4],
        date_iso[5:7],
        date_iso[8:10],
        claim_id
    )
    file_path = '%s/%s' % (file_dir, uuid.uuid4())
    pathlib.Path('%s/%s' % (root, file_dir)).mkdir(parents=True, exist_ok=True)
    f = open('%s/%s' % (root, file_path), "xb")
    f.write(base64.b64decode(document))
    f.close()
    return file_path


def create_attachment(claim_id, data):
    data["claim_id"] = claim_id
    from core import datetime
    now = datetime.datetime.now()
    if ClaimConfig.claim_attachments_root_path:
        # don't use data date as it may be updated by user afterwards!
        data['url'] = create_file(now, claim_id, data.pop('document'))
    data['validity_from'] = now
    ClaimAttachment.objects.create(**data)


def create_attachments(claim_id, attachments):
    for attachment in attachments:
        create_attachment(claim_id, attachment)


def update_or_create_claim(data, user):
    items = data.pop('items') if 'items' in data else []
    services = data.pop('services') if 'services' in data else []
    if "client_mutation_id" in data:
        data.pop('client_mutation_id')
    if "client_mutation_label" in data:
        data.pop('client_mutation_label')
    claim_uuid = data.pop('uuid') if 'uuid' in data else None
    # update_or_create(uuid=claim_uuid, ...)
    # doesn't work because of explicit attempt to set null to uuid!
    if claim_uuid:
        claim = Claim.objects.get(uuid=claim_uuid)
        claim.save_history()
        # reset the non required fields
        # (each update is 'complete', necessary to be able to set 'null')
        reset_claim_before_update(claim)
        [setattr(claim, key, data[key]) for key in data]
    else:
        claim = Claim.objects.create(**data)
    from core.utils import TimeUtils
    claimed = 0
    claim.items.update(validity_to=TimeUtils.now())
    claimed += process_items_relations(user, claim, items)
    claim.services.update(validity_to=TimeUtils.now())
    claimed += process_services_relations(user, claim, services)

    claim.claimed = claimed
    claim.save()
    return claim


class CreateClaimMutation(OpenIMISMutation):
    """
    Create a new claim. The claim items and services can all be entered with this call
    """
    _mutation_module = "claim"
    _mutation_class = "CreateClaimMutation"

    class Input(CreateClaimInputType):
        pass

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            # TODO move this verification to OIMutation
            if type(user) is AnonymousUser or not user.id:
                raise ValidationError(
                    _("mutation.authentication_required"))
            if not user.has_perms(ClaimConfig.gql_mutation_create_claims_perms):
                raise PermissionDenied(_("unauthorized"))
            # Claim code unicity should be enforced at DB Scheme level...
            if Claim.objects.filter(code=data['code']).exists():
                return [{
                    'message': _("claim.mutation.duplicated_claim_code") % {'code': data['code']},
                }]
            print("Async Mutate")
            print(data)
            data['audit_user_id'] = user.id_for_audit
            data['status'] = Claim.STATUS_ENTERED
            from core.utils import TimeUtils
            data['validity_from'] = TimeUtils.now()
            attachments = data.pop('attachments') if 'attachments' in data else None
            claim = update_or_create_claim(data, user)
            if attachments:
                create_attachments(claim.id, attachments)
            return None
        except Exception as exc:
            return [{
                'message': _("claim.mutation.failed_to_create_claim") % {'code': data['code']},
                'detail': str(exc)}]


class UpdateClaimMutation(OpenIMISMutation):
    """
    Update a claim. The claim items and services can all be updated with this call
    """
    _mutation_module = "claim"
    _mutation_class = "UpdateClaimMutation"

    class Input(ClaimInputType):
        pass

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            # TODO move this verification to OIMutation
            if type(user) is AnonymousUser or not user.id:
                raise ValidationError(
                    _("mutation.authentication_required"))
            if not user.has_perms(ClaimConfig.gql_mutation_update_claims_perms):
                raise PermissionDenied(_("unauthorized"))
            data['audit_user_id'] = user.id_for_audit
            update_or_create_claim(data, user)
            return None
        except Exception as exc:
            return [{
                'message': _("claim.mutation.failed_to_update_claim") % {'code': data['code']},
                'detail': str(exc)}]


class CreateAttachmentMutation(OpenIMISMutation):
    _mutation_module = "claim"
    _mutation_class = "AddClaimAttachmentMutation"

    class Input(AttachmentInputType):
        pass

    @classmethod
    def async_mutate(cls, user, **data):
        claim = None
        try:
            if user.is_anonymous or not user.has_perms(ClaimConfig.gql_mutation_update_claims_perms):
                raise PermissionDenied(_("unauthorized"))
            if "client_mutation_id" in data:
                data.pop('client_mutation_id')
            if "client_mutation_label" in data:
                data.pop('client_mutation_label')
            claim_uuid = data.pop("claim_uuid")
            queryset = Claim.objects.filter(*filter_validity())
            if settings.ROW_SECURITY:
                dist = UserDistrict.get_user_districts(user._u)
                queryset = queryset.filter(
                    health_facility__location__id__in=[
                        l.location_id for l in dist]
                )
            claim = queryset.filter(uuid=claim_uuid).first()
            if not claim:
                raise PermissionDenied(_("unauthorized"))
            create_attachment(claim.id, data)
            return None
        except Exception as exc:
            return [{
                'message': _("claim.mutation.failed_to_attach_document") % {'code': claim.code if claim else None},
                'detail': str(exc)}]


class UpdateAttachmentMutation(OpenIMISMutation):
    _mutation_module = "claim"
    _mutation_class = "UpdateAttachmentMutation"

    class Input(BaseAttachmentInputType):
        pass

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if not user.has_perms(ClaimConfig.gql_mutation_update_claims_perms):
                raise PermissionDenied(_("unauthorized"))
            queryset = ClaimAttachment.objects.filter(*filter_validity())
            if settings.ROW_SECURITY:
                from location.models import UserDistrict
                dist = UserDistrict.get_user_districts(user._u)
                queryset = queryset.select_related("claim") \
                    .filter(
                    claim__health_facility__location__id__in=[
                        l.location_id for l in dist]
                )
            attachment = queryset \
                .filter(id=data['id']) \
                .first()
            if not attachment:
                raise PermissionDenied(_("unauthorized"))
            attachment.save_history()
            data['audit_user_id'] = user.id_for_audit
            [setattr(attachment, key, data[key]) for key in data]
            attachment.save()
            return None
        except Exception as exc:
            return [{
                'message': _("claim.mutation.failed_to_update_claim_attachment") % {
                    'code': attachment.claim.code,
                    'filename': attachment.filename
                },
                'detail': str(exc)}]


class DeleteAttachmentMutation(OpenIMISMutation):
    _mutation_module = "claim"
    _mutation_class = "DeleteClaimAttachmentMutation"

    class Input(OpenIMISMutation.Input):
        id = graphene.String()

    @classmethod
    def async_mutate(cls, user, **data):
        try:
            if not user.has_perms(ClaimConfig.gql_mutation_update_claims_perms):
                raise PermissionDenied(_("unauthorized"))
            queryset = ClaimAttachment.objects.filter(*filter_validity())
            if settings.ROW_SECURITY:
                from location.models import UserDistrict
                dist = UserDistrict.get_user_districts(user._u)
                queryset = queryset.select_related("claim") \
                    .filter(
                    claim__health_facility__location__id__in=[
                        l.location_id for l in dist]
                )
            attachment = queryset \
                .filter(id=data['id']) \
                .first()
            if not attachment:
                raise PermissionDenied(_("unauthorized"))
            attachment.delete_history()
            return None
        except Exception as exc:
            return [{
                'message': _("claim.mutation.failed_to_delete_claim_attachment") % {
                    'code': attachment.claim.code,
                    'filename': attachment.filename
                },
                'detail': str(exc)}]


class SubmitClaimsMutation(OpenIMISMutation):
    """
    Submit one or several claims.
    """
    __filter_handlers = {
        'services': 'services__service__code__in',
        'items': 'items__item__code__in'
    }
    _mutation_module = "claim"
    _mutation_class = "SubmitClaimsMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)
        additional_filters = graphene.String()

    @classmethod
    @mutation_on_uuids_from_filter(Claim, ClaimGQLType, 'additional_filters', __filter_handlers)
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_submit_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        errors = []
        uuids = data.get("uuids", [])

        for claim_uuid in uuids:
            c_errors = []
            claim = Claim.objects \
                .filter(uuid=claim_uuid,
                        validity_to__isnull=True) \
                .prefetch_related("items") \
                .prefetch_related("services") \
                .first()
            if claim is None:
                errors += {
                    'title': claim_uuid,
                    'list': [
                        {'message': _(
                            "claim.validation.id_does_not_exist") % {'id': claim_uuid}}
                    ]
                }
                continue
            claim.save_history()
            logger.debug("SubmitClaimsMutation: validating claim %s", claim_uuid)
            c_errors += validate_claim(claim, True)
            logger.debug("SubmitClaimsMutation: claim %s validated, nb of errors: %s", claim_uuid, len(c_errors))
            if len(c_errors) == 0:
                c_errors = validate_assign_prod_to_claimitems_and_services(claim)
                logger.debug("SubmitClaimsMutation: claim %s assigned, nb of errors: %s", claim_uuid, len(c_errors))
                c_errors += process_dedrem(claim, user.id_for_audit, False)
                logger.debug("SubmitClaimsMutation: claim %s processed for dedrem, nb of errors: %s", claim_uuid,
                             len(errors))
            c_errors += set_claim_submitted(claim, c_errors, user)
            logger.debug("SubmitClaimsMutation: claim %s set submitted", claim_uuid)
            if c_errors:
                errors.append({
                    'title': claim.code,
                    'list': c_errors
                })
        if len(errors) == 1:
            errors = errors[0]['list']
        logger.debug("SubmitClaimsMutation: claim done, errors: %s", len(errors))
        return errors


def set_claims_status(uuids, field, status, audit_data=None):
    errors = []
    for claim_uuid in uuids:
        claim = Claim.objects \
            .filter(uuid=claim_uuid,
                    validity_to__isnull=True) \
            .first()
        if claim is None:
            errors += [{'message': _(
                "claim.validation.id_does_not_exist") % {'id': claim_uuid}}]
            continue
        try:
            claim.save_history()
            setattr(claim, field, status)
            if audit_data:
                for k, v in audit_data.items():
                    setattr(claim, k, v)
            claim.save()
        except Exception as exc:
            errors += [
                {'message': _("claim.mutation.failed_to_change_status_of_claim") %
                            {'code': claim.code}}]

    return errors


def update_claims_dedrems(uuids, user):
    # We could do it in one query with filter(claim__uuid__in=uuids) but we'd loose the logging
    errors = []
    for uuid in uuids:
        logger.debug(f"delivering review on {uuid}, reprocessing dedrem ({user})")
        claim = Claim.objects.get(uuid=uuid)
        errors += validate_and_process_dedrem_claim(claim, user, False)
    return errors


class SelectClaimsForFeedbackMutation(OpenIMISMutation):
    """
    Select one or several claims for feedback.
    """
    _mutation_module = "claim"
    _mutation_class = "SelectClaimsForFeedbackMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_select_claim_feedback_perms):
            raise PermissionDenied(_("unauthorized"))
        return set_claims_status(data['uuids'], 'feedback_status', Claim.FEEDBACK_SELECTED)


class BypassClaimsFeedbackMutation(OpenIMISMutation):
    """
    Bypass feedback for one or several claims
    """
    _mutation_module = "claim"
    _mutation_class = "BypassClaimsFeedbackMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_bypass_claim_feedback_perms):
            raise PermissionDenied(_("unauthorized"))
        return set_claims_status(data['uuids'], 'feedback_status', Claim.FEEDBACK_BYPASSED)


class SkipClaimsFeedbackMutation(OpenIMISMutation):
    """
    Skip feedback for one or several claims
    Skip indicates that the claim is not selected for feedback
    """
    _mutation_module = "claim"
    _mutation_class = "SkipClaimsFeedbackMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_skip_claim_feedback_perms):
            raise PermissionDenied(_("unauthorized"))
        return set_claims_status(data['uuids'], 'feedback_status', Claim.FEEDBACK_NOT_SELECTED)


class DeliverClaimFeedbackMutation(OpenIMISMutation):
    """
    Deliver feedback of a claim
    """
    _mutation_module = "claim"
    _mutation_class = "DeliverClaimFeedbackMutation"

    class Input(OpenIMISMutation.Input):
        claim_uuid = graphene.String(required=False, read_only=True)
        feedback = graphene.Field(FeedbackInputType, required=True)

    @classmethod
    def async_mutate(cls, user, **data):
        claim = None
        try:
            if not user.has_perms(ClaimConfig.gql_mutation_deliver_claim_feedback_perms):
                raise PermissionDenied(_("unauthorized"))
            claim = Claim.objects.select_related('feedback').get(
                uuid=data['claim_uuid'],
                validity_to__isnull=True)
            prev_feedback = claim.feedback
            prev_claim_id = claim.save_history()
            if prev_feedback:
                prev_feedback.claim_id = prev_claim_id
                prev_feedback.save()
            feedback = data['feedback']
            from core.utils import TimeUtils
            feedback['validity_from'] = TimeUtils.now()
            feedback['audit_user_id'] = user.id_for_audit
            # The legacy model has a Foreign key on both sides of this one-to-one relationship
            f, created = Feedback.objects.update_or_create(
                claim=claim,
                defaults=feedback
            )
            claim.feedback = f
            claim.feedback_status = Claim.FEEDBACK_DELIVERED
            claim.feedback_available = True
            claim.save()
            return None
        except Exception as exc:
            return [{
                'message': _("claim.mutation.failed_to_update_claim") % {'code': claim.code if claim else None},
                'detail': str(exc)}]


class SelectClaimsForReviewMutation(OpenIMISMutation):
    """
    Select one or several claims for review.
    """
    _mutation_module = "claim"
    _mutation_class = "SelectClaimsForReviewMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_select_claim_review_perms):
            raise PermissionDenied(_("unauthorized"))
        return set_claims_status(data['uuids'], 'review_status', Claim.REVIEW_SELECTED)


class BypassClaimsReviewMutation(OpenIMISMutation):
    """
    Bypass review for one or several claims
    Bypass indicates that review of a previously selected claim won't be delivered
    """
    _mutation_module = "claim"
    _mutation_class = "BypassClaimsReviewMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_bypass_claim_review_perms):
            raise PermissionDenied(_("unauthorized"))
        return set_claims_status(data['uuids'], 'review_status', Claim.REVIEW_BYPASSED)


class DeliverClaimsReviewMutation(OpenIMISMutation):
    """
    Mark claim review as delivered for one or several claims
    """
    _mutation_module = "claim"
    _mutation_class = "DeliverClaimsReviewMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        logger.error("SaveClaimReviewMutation")
        if not user.has_perms(ClaimConfig.gql_mutation_deliver_claim_review_perms):
            raise PermissionDenied(_("unauthorized"))
        errors = set_claims_status(data['uuids'], 'review_status', Claim.REVIEW_DELIVERED,
                                   {'audit_user_id_review': user.id_for_audit})
        # OMT-208 update the dedrem for the reviewed claims
        errors += update_claims_dedrems(data["uuids"], user)

        return errors


class SkipClaimsReviewMutation(OpenIMISMutation):
    """
    Skip review for one or several claims
    Skip indicates that the claim is not selected for review
    """
    _mutation_module = "claim"
    _mutation_class = "SkipClaimsReviewMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_skip_claim_review_perms):
            raise PermissionDenied(_("unauthorized"))
        return set_claims_status(data['uuids'], 'review_status', Claim.REVIEW_NOT_SELECTED)


class SaveClaimReviewMutation(OpenIMISMutation):
    """
    Save the review of a claim (items and services)
    """
    _mutation_module = "claim"
    _mutation_class = "SaveClaimReviewMutation"

    class Input(OpenIMISMutation.Input):
        claim_uuid = graphene.String(required=False, read_only=True)
        adjustment = graphene.String(required=False)
        items = graphene.List(ClaimItemInputType, required=False)
        services = graphene.List(ClaimServiceInputType, required=False)

    @classmethod
    def async_mutate(cls, user, **data):
        claim = None
        try:
            if not user.has_perms(ClaimConfig.gql_mutation_deliver_claim_review_perms):
                raise PermissionDenied(_("unauthorized"))
            claim = Claim.objects.get(uuid=data['claim_uuid'],
                                      validity_to__isnull=True)
            if claim is None:
                return [{'message': _(
                    "claim.validation.id_does_not_exist") % {'id': data['claim_uuid']}}]
            claim.save_history()
            claim.adjustment = data.get('adjustment', None)
            items = data.pop('items') if 'items' in data else []
            all_rejected = True
            for item in items:
                item_id = item.pop('id')
                claim.items.filter(id=item_id).update(**item)
                if item['status'] == ClaimItem.STATUS_PASSED:
                    all_rejected = False
            services = data.pop('services') if 'services' in data else []
            claimed = 0
            ClaimServiceElts = []
            for service in services:
                service_id = service.pop('id')
                service_linked = service.pop('serviceLinked', [])
                serviceserviceSet = service.pop('serviceserviceSet', [])
                for claim_service_service in serviceserviceSet:
                    claim_service_code = claim_service_service.pop('subServiceCode')
                    claim_service = claim.services.filter(id=service_id).first()
                    if claim_service:
                        service_elt = Service.objects.filter(code=claim_service_code).first()
                        if service_elt:
                            claim_service_to_update = claim_service.claimlinkedService.filter(service=service_elt.id)
                            if claim_service_to_update:
                                qty_asked = claim_service_service.pop('qty_asked', 0)
                                price_asked = claim_service_service.pop('price_asked', 0)
                                claim_service_service['qty_displayed'] = qty_asked
                                price = qty_asked * price_asked
                                claimed += price
                                claim_service_to_update.update(**claim_service_service)
                        ClaimServiceElts.append(claim_service)
                for claim_service_item in service_linked:
                    claim_item_code = claim_service_item.pop('subItemCode')
                    claim_service = claim.services.filter(id=service_id).first()
                    if claim_service:
                        item_elt = Item.objects.filter(code=claim_item_code).first()
                        if service_elt:
                            claim_item_to_update = claim_service.claimlinkedItem.filter(item=item_elt.id)
                            if claim_item_to_update:
                                qty_asked = claim_service_item.pop('qty_asked', 0)
                                price_asked = claim_service_item.pop('price_asked', 0)
                                claim_service_item['qty_displayed'] = qty_asked
                                price = qty_asked * price_asked
                                claimed += price
                                claim_item_to_update.update(**claim_service_item)     

                if service['status'] == ClaimService.STATUS_PASSED:
                    all_rejected = False
            claim.approved = approved_amount(claim)
            claim.claimed = claimed
            for claimservice in ClaimServiceElts:
                setattr(claimservice, 'price_adjusted', claimed)
                claimservice.save()
            claim.audit_user_id_review = user.id_for_audit
            if all_rejected:
                claim.status = Claim.STATUS_REJECTED
            claim.save()
            return None
        except Exception as exc:
            return [{
                'message': _("claim.mutation.failed_to_update_claim") % {'code': claim.code if claim else None},
                'detail': str(exc)}]


class ProcessClaimsMutation(OpenIMISMutation):
    """
    Process one or several claims.
    """
    _mutation_module = "claim"
    _mutation_class = "ProcessClaimsMutation"

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_process_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        errors = []
        for claim_uuid in data["uuids"]:
            logger.debug("ProcessClaimsMutation: processing %s", claim_uuid)
            c_errors = []
            claim = Claim.objects \
                .filter(uuid=claim_uuid) \
                .prefetch_related("items") \
                .prefetch_related("services") \
                .first()
            if claim is None:
                errors += {
                    'title': claim_uuid,
                    'list': [{'message': _(
                        "claim.validation.id_does_not_exist") % {'id': claim_uuid}}]
                }
                continue
            claim.save_history()
            claim.audit_user_id_process = user.id_for_audit
            logger.debug("ProcessClaimsMutation: validating claim %s", claim_uuid)
            c_errors += validate_and_process_dedrem_claim(claim, user, True)

            logger.debug("ProcessClaimsMutation: claim %s set processed or valuated", claim_uuid)
            if c_errors:
                errors.append({
                    'title': claim.code,
                    'list': c_errors
                })

        if len(errors) == 1:
            errors = errors[0]['list']
        logger.debug("ProcessClaimsMutation: claims %s done, errors: %s", data["uuids"], len(errors))
        return errors


class DeleteClaimsMutation(OpenIMISMutation):
    """
    Mark one or several claims as Deleted (validity_to)
    """

    class Input(OpenIMISMutation.Input):
        uuids = graphene.List(graphene.String)

    _mutation_module = "claim"
    _mutation_class = "DeleteClaimsMutation"

    @classmethod
    def async_mutate(cls, user, **data):
        if not user.has_perms(ClaimConfig.gql_mutation_delete_claims_perms):
            raise PermissionDenied(_("unauthorized"))
        errors = []
        for claim_uuid in data["uuids"]:
            claim = Claim.objects \
                .filter(uuid=claim_uuid) \
                .prefetch_related("items") \
                .prefetch_related("services") \
                .first()
            if claim is None:
                errors += {
                    'title': claim_uuid,
                    'list': [{'message': _(
                        "claim.validation.id_does_not_exist") % {'id': claim_uuid}}]
                }
                continue
            errors += set_claim_deleted(claim)
        if len(errors) == 1:
            errors = errors[0]['list']
        return errors


def set_claim_submitted(claim, errors, user):
    try:
        claim.audit_user_id_submit = user.id_for_audit
        if errors:
            claim.status = Claim.STATUS_REJECTED
        else:
            claim.approved = approved_amount(claim)
            claim.status = Claim.STATUS_CHECKED
            from core.utils import TimeUtils
            claim.submit_stamp = TimeUtils.now()
            claim.category = get_claim_category(claim)
        claim.save()
        return []
    except Exception as exc:
        return {
            'title': claim.code,
            'list': [{
                'message': _("claim.mutation.failed_to_change_status_of_claim") % {'code': claim.code},
                'detail': claim.uuid}]
        }


def set_claim_deleted(claim):
    try:
        claim.delete_history()
        return []
    except Exception as exc:
        return {
            'title': claim.code,
            'list': [{
                'message': _("claim.mutation.failed_to_change_status_of_claim") % {'code': claim.code},
                'detail': claim.uuid}]
        }


def details_with_relative_prices(details):
    return details.filter(status=ClaimDetail.STATUS_PASSED) \
        .filter(price_origin=ProductItemOrService.ORIGIN_RELATIVE) \
        .exists()


def with_relative_prices(claim):
    return details_with_relative_prices(claim.items) or details_with_relative_prices(claim.services)


def set_claim_processed_or_valuated(claim, errors, user):
    try:
        if errors:
            claim.status = Claim.STATUS_REJECTED
        else:
            claim.status = Claim.STATUS_PROCESSED if with_relative_prices(claim) else Claim.STATUS_VALUATED
            claim.audit_user_id_process = user.id_for_audit
            from core.utils import TimeUtils
            claim.process_stamp = TimeUtils.now()
        claim.save()
        return []
    except Exception as ex:
        return {
            'title': claim.code,
            'list': [{'message': _("claim.mutation.failed_to_change_status_of_claim") % {'code': claim.code},
                      'detail': claim.uuid}]
        }


def validate_and_process_dedrem_claim(claim, user, is_process):
    errors = validate_claim(claim, False)
    logger.debug("ProcessClaimsMutation: claim %s validated, nb of errors: %s", claim.uuid, len(errors))
    if len(errors) == 0:
        errors = validate_assign_prod_to_claimitems_and_services(claim)
        logger.debug("ProcessClaimsMutation: claim %s assigned, nb of errors: %s", claim.uuid, len(errors))
        errors += process_dedrem(claim, user.id_for_audit, is_process)
        logger.debug("ProcessClaimsMutation: claim %s processed for dedrem, nb of errors: %s", claim.uuid,
                     len(errors))
    else:
        # OMT-208 the claim is invalid. If there is a dedrem, we need to clear it (caused by a review)
        deleted_dedrems = ClaimDedRem.objects.filter(claim=claim).delete()
        if deleted_dedrems:
            logger.debug(f"Claim {claim.uuid} is invalid, we deleted its dedrem ({deleted_dedrems})")
    if is_process:
        errors += set_claim_processed_or_valuated(claim, errors, user)
    return errors
