import importlib
import xml.etree.ElementTree as ET
import logging
from typing import Callable, Dict

from medical.models import Item, Service

import core
from core.models import Officer
from core.utils import filter_validity
from django.db import connection, transaction
from gettext import gettext as _

from core.signals import register_service_signal
from .apps import ClaimConfig
from django.conf import settings

from claim.models import Claim, ClaimItem, ClaimService, ClaimDetail, ClaimDedRem, FeedbackPrompt
from product.models import ProductItemOrService

from claim.utils import process_items_relations, process_services_relations
from .validations import validate_claim, validate_assign_prod_to_claimitems_and_services, process_dedrem, \
    approved_amount, get_claim_category
from django.db.models import Subquery, F, OuterRef, Sum, FloatField
from django.db.models.functions import Coalesce
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError, ObjectDoesNotExist
logger = logging.getLogger(__name__)


@core.comparable
class ClaimElementSubmit(object):
    def __init__(self, type, code, quantity, price=None):
        self.type = type
        self.code = code
        self.price = price
        self.quantity = quantity

    def add_to_xmlelt(self, xmlelt):
        item = ET.SubElement(xmlelt, self.type)
        ET.SubElement(item, "%sCode" % self.type).text = "%s" % self.code
        if self.price:
            ET.SubElement(item, "%sPrice" % self.type).text = "%s" % self.price
        ET.SubElement(item, "%sQuantity" %
                      self.type).text = "%s" % self.quantity

    def to_claim_provision(self):
        raise NotImplementedError()


@core.comparable
class ClaimItemSubmit(ClaimElementSubmit):
    def __init__(self, code, quantity, price=None):
        super().__init__(type='Item',
                         code=code,
                         price=price,
                         quantity=quantity)

    def to_claim_provision(self):
        item = Item.objects.filter(validity_to__isnull=True, code=self.code).get()
        return ClaimItem(qty_provided=self.quantity, price_asked=self.price, item=item)


@core.comparable
class ClaimServiceSubmit(ClaimElementSubmit):
    def __init__(self, code, quantity, price=None):
        super().__init__(type='Service',
                         code=code,
                         price=price,
                         quantity=quantity)

    def to_claim_provision(self):
        service = Service.objects.filter(validity_to__isnull=True, code=self.code).get()
        return ClaimService(qty_provided=self.quantity, price_asked=self.price, service=service)


@core.comparable
class ClaimSubmit(object):
    def __init__(self, date, code, icd_code, total, start_date,
                 insuree_chf_id, health_facility_code,
                 claim_admin_code,
                 item_submits=None, service_submits=None,
                 end_date=None,
                 icd_code_1=None, icd_code_2=None, icd_code_3=None, icd_code_4=None,
                 visit_type=None, guarantee_no=None,
                 comment=None,
                 ):
        self.date = date
        self.code = code
        self.icd_code = icd_code
        self.total = total
        self.start_date = start_date
        self.insuree_chf_id = insuree_chf_id
        self.health_facility_code = health_facility_code
        self.end_date = end_date
        self.icd_code_1 = icd_code_1
        self.icd_code_2 = icd_code_2
        self.icd_code_3 = icd_code_3
        self.icd_code_4 = icd_code_4
        self.claim_admin_code = claim_admin_code
        self.visit_type = visit_type
        self.guarantee_no = guarantee_no
        self.comment = comment
        self.items = item_submits
        self.services = service_submits

    def _details_to_xmlelt(self, xmlelt):
        ET.SubElement(xmlelt, 'ClaimDate').text = self.date.to_ad_date().strftime(
            "%d/%m/%Y")
        ET.SubElement(
            xmlelt, 'HFCode').text = "%s" % self.health_facility_code
        if self.claim_admin_code:
            ET.SubElement(
                xmlelt, 'ClaimAdmin').text = "%s" % self.claim_admin_code
        ET.SubElement(xmlelt, 'ClaimCode').text = "%s" % self.code
        ET.SubElement(xmlelt, 'CHFID').text = "%s" % self.insuree_chf_id
        ET.SubElement(
            xmlelt, 'StartDate').text = self.start_date.to_ad_date().strftime("%d/%m/%Y")
        if self.end_date:
            ET.SubElement(xmlelt, 'EndDate').text = self.end_date.to_ad_date().strftime(
                "%d/%m/%Y")
        ET.SubElement(xmlelt, 'ICDCode').text = "%s" % self.icd_code
        if self.comment:
            ET.SubElement(xmlelt, 'Comment').text = "%s" % self.comment
        ET.SubElement(xmlelt, 'Total').text = "%s" % self.total
        if self.icd_code_1:
            ET.SubElement(xmlelt, 'ICDCode1').text = "%s" % self.icd_code_1
        if self.icd_code_2:
            ET.SubElement(xmlelt, 'ICDCode2').text = "%s" % self.icd_code_2
        if self.icd_code_3:
            ET.SubElement(xmlelt, 'ICDCode3').text = "%s" % self.icd_code_3
        if self.icd_code_4:
            ET.SubElement(xmlelt, 'ICDCode4').text = "%s" % self.icd_code_4
        if self.visit_type:
            ET.SubElement(xmlelt, 'VisitType').text = "%s" % self.visit_type
        if self.guarantee_no:
            ET.SubElement(
                xmlelt, 'GuaranteeNo').text = "%s" % self.guarantee_no

    def add_elt_list_to_xmlelt(self, xmlelt, elts_name, elts):
        if elts and len(elts) > 0:
            elts_xml = ET.SubElement(xmlelt, elts_name)
            for item in elts:
                item.add_to_xmlelt(elts_xml)

    def add_to_xmlelt(self, xmlelt):
        details = ET.SubElement(xmlelt, 'Details')
        self._details_to_xmlelt(details)
        self.add_elt_list_to_xmlelt(xmlelt, 'Items', self.items)
        self.add_elt_list_to_xmlelt(xmlelt, 'Services', self.services)

    def to_xml(self):
        claim_xml = ET.Element('Claim')
        self.add_to_xmlelt(claim_xml)
        return ET.tostring(claim_xml, encoding='utf-8', method='xml').decode()


@core.comparable
class ClaimSubmitError(Exception):
    ERROR_CODES = {
        -1: "Fatal Error",
        1: "Invalid HF Code",
        2: "Duplicate Claim Code",
        3: "Invalid Insuree CHFID",
        4: "End date is smaller than start date",
        5: "Invalid ICDCode",
        6: "Claimed amount is 0",
        7: "Invalid ItemCode",
        8: "Invalid ServiceCode",
        9: "Invalid Claim Admin",
    }

    def __init__(self, code, msg=None):
        self.code = code
        self.msg = ClaimSubmitError.ERROR_CODES.get(
            self.code, msg or "Unknown exception")

    def __str__(self):
        return "ClaimSubmitError %s: %s" % (self.code, self.msg)


class ClaimSubmitService(object):

    def __init__(self, user):
        self.user = user

    def hf_scope_check(self, claim_submit: ClaimSubmit):
        self._validate_user_hf(claim_submit.health_facility_code)

    def submit(self, claim_submit):
        with connection.cursor() as cur:
            sql = """\
                DECLARE @ret int;
                EXEC @ret = [dbo].[uspUpdateClaimFromPhone] @XML = %s;
                SELECT @ret;
            """

            cur.execute(sql, (claim_submit.to_xml(),))
            for i in range(int(ClaimConfig.claim_uspUpdateClaimFromPhone_intermediate_sets)):
                cur.nextset()
            if cur.description is None:  # 0 is considered as 'no result' by pyodbc
                return
            res = cur.fetchone()[0]  # FETCH 'SELECT @ret' returned value
            raise ClaimSubmitError(res)

    @register_service_signal('claim.enter_and_submit_claim')
    @transaction.atomic
    def enter_and_submit(self, claim: dict, rule_engine_validation: bool = True) -> Claim:
        create_claim_service = ClaimCreateService(self.user)
        entered_claim = create_claim_service.enter_claim(claim)
        submitted_claim = self.submit_claim(entered_claim, rule_engine_validation)
        return submitted_claim

    @register_service_signal('claim.submit_claim')
    def submit_claim(self, claim: Claim, rule_engine_validation=True):
        """
        Submission based on the GQL SubmitClaimMutation.async_mutate
        """
        self._validate_submit_permissions()
        self._validate_user_hf(claim.health_facility.code)
        claim.save_history()

        if rule_engine_validation:
            validation_errors = self._validate_claim(claim)
            if validation_errors:
                return self.__submit_to_rejected(claim)

        return self.__submit_to_checked(claim)

    def _validate_submit_permissions(self):
        if type(self.user) is AnonymousUser or not self.user.id:
            raise ValidationError(
                _("mutation.authentication_required"))
        if not self.user.has_perms(ClaimConfig.gql_mutation_submit_claims_perms):
            raise PermissionDenied(_("unauthorized"))

    def _validate_user_hf(self, hf_code):
        from location.models import LocationManager, HealthFacility
        hf = LocationManager().build_user_location_filter_query(self.user._u, queryset = HealthFacility.filter_queryset().filter(code=hf_code))
        if not hf and settings.ROW_SECURITY:
            raise ClaimSubmitError("Invalid health facility code or health facility not allowed for user")

    def _validate_claim(self, claim):
        errors = validate_claim(claim, True)
        if not errors:
            errors = validate_assign_prod_to_claimitems_and_services(claim)
            errors += process_dedrem(claim, self.user.id_for_audit, False)
        return errors or []

    def __submit_to_rejected(self, claim: Claim):
        claim.status = Claim.STATUS_REJECTED
        claim.save()
        return claim

    def __submit_to_checked(self, claim: Claim):
        claim.approved = approved_amount(claim)
        claim.status = Claim.STATUS_CHECKED
        from core.utils import TimeUtils
        claim.submit_stamp = TimeUtils.now()
        claim.category = get_claim_category(claim)
        claim.save()
        return claim


def formatClaimService(s):
    return {
        "service": str(s.service),
        "quantity": s.qty_provided,
        "price": s.price_asked,
        "explanation": s.explanation
    }


def formatClaimItem(i):
    return {
        "item": str(i.item),
        "quantity": i.qty_provided,
        "price": i.price_asked,
        "explanation": i.explanation
    }


class ClaimReportService(object):
    def __init__(self, user):
        self.user = user

    def fetch(self, uuid):
        from .models import Claim
        queryset = Claim.objects.filter(*core.filter_validity())
        if settings.ROW_SECURITY:
            from location.models import LocationManager
            queryset = LocationManager().build_user_location_filter_query( self.user._u, prefix='health_facility__location', queryset=queryset, loc_types=['D'])
        claim = queryset\
            .select_related('health_facility') \
            .select_related('insuree') \
            .filter(uuid=uuid)\
            .first()
        if not claim:
            raise PermissionDenied(_("unauthorized"))
        return {
            "code": claim.code,
            "visitDateFrom": claim.date_from.isoformat() if claim.date_from else None,
            "visitDateTo":  claim.date_to.isoformat() if claim.date_to else None,
            "claimDate": claim.date_claimed.isoformat() if claim.date_claimed else None,
            "healthFacility": str(claim.health_facility),
            "insuree": str(claim.insuree),
            "claimAdmin": str(claim.admin) if claim.admin else None,
            "icd": str(claim.icd),
            "icd1": str(claim.icd1) if claim.icd_1 else None,
            "icd2": str(claim.icd1) if claim.icd_2 else None,
            "icd3": str(claim.icd1) if claim.icd_3 else None,
            "icd4": str(claim.icd1) if claim.icd_4 else None,
            "guarantee": claim.guarantee_id,
            "visitType": claim.visit_type,
            "claimed": claim.claimed,
            "services": [formatClaimService(s) for s in claim.services.all()],
            "items": [formatClaimItem(i) for i in claim.items.all()],
        }


class ClaimCreateService:
    def __init__(self, user):
        self.user = user

    def _validate_user_hf(self, hf_id):
        from location.models import LocationManager, HealthFacility
        hf = LocationManager().build_user_location_filter_query(self.user._u, queryset = HealthFacility.filter_queryset().filter(id=hf_id))
        if not hf and settings.ROW_SECURITY:
            raise ValidationError("Invalid health facility code or health facility not allowed for user")

    @register_service_signal('claim.enter_claim')
    def enter_claim(self, claim: dict):
        """
        Implementation based on the GQL CreateClaimMutation.async_mutate
        """
        self._validate_permissions()
        self._validate_claim_fields(claim)
        self._validate_user_hf(claim.get('health_facility_id', None))
        self._ensure_entered_claim_fields(claim)
        claim = self._create_claim_from_dict(claim)
        return claim

    def _validate_permissions(self):
        if type(self.user) is AnonymousUser or not self.user.id:
            raise ValidationError(
                _("mutation.authentication_required"))
        if not self.user.has_perms(ClaimConfig.gql_mutation_create_claims_perms):
            raise PermissionDenied(_("unauthorized"))

    def _validate_claim_fields(self, claim):
        if not claim.get('code'):
            raise ValidationError("Provided claim without code.")

        if Claim.objects.filter(code=claim['code'], validity_to__isnull=True).exists():
            raise ValidationError(F"Claim with code '{claim['code']}' already exists.")

    def _ensure_entered_claim_fields(self, claim_submit_data):
        claim_submit_data['audit_user_id'] = self.user.id_for_audit
        claim_submit_data['status'] = Claim.STATUS_ENTERED
        from core.utils import TimeUtils
        claim_submit_data['validity_from'] = TimeUtils.now()

    def _create_claim_from_dict(self, claim_submit_data):
        items = claim_submit_data.pop('items', [])
        services = claim_submit_data.pop('services', [])
        claim_submit_data.pop('service_item_set', [])
        claim_submit_data.pop('service_service_set', [])
        claim = Claim.objects.create(**claim_submit_data)
        self.__process_items(claim, items, services)
        claim.save()
        return claim

    def __process_items(self, claim, items, services):
        claimed = 0
        claimed += process_items_relations(self.user, claim, items)
        claimed += process_services_relations(self.user, claim, services)
        claim.claimed = claimed


def update_sum_claims(claim):
    claimed = 0
    service_asked = Subquery(
        ClaimItem.objects.filter(claim=OuterRef('pk')).filter(legacy_id__isnull=True).values('claim_id').annotate(
            item_sum=Sum(F('price_asked')*F('qty_provided'))).values('item_sum').order_by()[:1],
        output_field=FloatField()
    )
    item_asked = Subquery(
        ClaimService.objects.filter(claim=OuterRef('pk')).filter(legacy_id__isnull=True).values('claim_id').annotate(
            service_sum=Sum(F('price_asked')*F('qty_provided'))).values('service_sum').order_by()[:1],
        output_field=FloatField()
    )
    Claim.objects.filter(id=claim.id).update(
        claimed=Coalesce(item_asked, 0) + Coalesce(service_asked, 0)
    )


def check_unique_claim_code(code):
    if Claim.objects.filter(code=code, validity_to__isnull=True).exists():
        return [{"message": "Claim code %s already exists" % code}]
    return []


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


def __autogenerate_claim_code():
    module_name, function_name = '[undefined]', '[undefined]'
    try:
        claim_code_function = _get_autogenerating_func()
        return claim_code_function(ClaimConfig.autogenerated_claim_code_config)
    except ImportError as e:
        logger.error(f"Error: Could not import module '{module_name}' for claim code autogeneration")
        raise e
    except AttributeError as e:
        logger.error(f"Error: Could not find function '{function_name}' in module '{module_name}' for claim code autogeneration")
        raise e


def _get_autogenerating_func() -> Callable[[Dict], Callable]:
    module_name, function_name = ClaimConfig.autogenerate_func.rsplit('.', 1)
    module = importlib.import_module(module_name)
    return getattr(module, function_name)


def claim_create(data, user, autogenerate_code = False):
    restore = data.pop('restore', None)
    autogenerate_code = data.pop('autogenerate', None)
    if restore:
        data["restore"] = Claim.objects.filter(uuid=restore).first()
    
    if autogenerate_code:
        data['code'] = __autogenerate_claim_code()
    data['audit_user_id'] = user.id_for_audit
    claim = Claim()
    set_reduced_attr(claim, data, ['items', 'services'])
    claim.save()
    claim_create_items_and_services(claim, data, user)
    return claim


def claim_update(claim, data, user):
    claim.save_history()
    # reset the non required fields
    # (each update is 'complete', necessary to be able to set 'null')
    reset_claim_before_update(claim)
    set_reduced_attr(claim, data, ['items', 'services'])
    from core.utils import TimeUtils
    claim.items.update(validity_to=TimeUtils.now())
    claim.services.update(validity_to=TimeUtils.now())
    claim_create_items_and_services(claim, data, user)
    return claim


def set_reduced_attr(obj, data, exclusions):
    for key in data:
        if key not in exclusions:
            setattr(obj,key, data[key]) 


def claim_create_items_and_services(claim, data, user):
    items = data.pop('items') if 'items' in data else []
    services = data.pop('services') if 'services' in data else []
    claimed = 0
    claimed += process_items_relations(user, claim, items)
    claimed += process_services_relations(user, claim, services)
    claim.claimed = claimed
    claim.save()


def update_or_create_claim(data, user):
         
    validate_claim_data(data, user)
    claim_uuid = data.pop("uuid", None)
    # update_or_create(uuid=claim_uuid, ...)
    # doesn't work because of explicit attempt to set null to uuid!
   
    if claim_uuid:
        claim = Claim.objects.get(uuid=claim_uuid)
        claim = claim_update(claim, data, user)
    else:
        claim = claim_create(data, user)
    return claim


def validate_claim_data(data, user):
    services = data.get('services') if 'services' in data else []
    incoming_code = data.get('code')
    claim_uuid = data.get("uuid", None)
    restore = data.get('restore', None)
    current_claim = Claim.objects.filter(uuid=claim_uuid).first()
    current_code = current_claim.code if current_claim else None

    if restore:
        restored_qs = Claim.objects.filter(uuid=restore)
        restored_from_claim = restored_qs.first()
        restored_count = Claim.objects.filter(restore=restored_from_claim).count()
        if not restored_qs.exists():
            raise ValidationError(_("mutation.restored_from_does_not_exist"))
        if not restored_from_claim.status == Claim.STATUS_REJECTED:
            raise ValidationError(_("mutation.cannot_restore_not_rejected_claim"))
        if not user.has_perms(ClaimConfig.gql_mutation_restore_claims_perms):
            raise ValidationError(_("mutation.no_restore_rights"))
        if ClaimConfig.claim_max_restore and restored_count >= ClaimConfig.claim_max_restore:
            raise ValidationError(_("mutation.max_restored_claim") % {
                "max_restore": ClaimConfig.claim_max_restore
            })
           
    elif current_claim is not None and current_claim.status not in (Claim.STATUS_CHECKED, Claim.STATUS_ENTERED):
        raise ValidationError(_("mutation.claim_not_editable")) 

    if not validate_number_of_additional_diagnoses(data):
        raise ValidationError(_("mutation.claim_too_many_additional_diagnoses"))

    if ClaimConfig.claim_validation_multiple_services_explanation_required:
        for service in services:
            if service["qty_provided"] > 1 and not service.get("explanation"):
                raise ValidationError(_("mutation.service_explanation_required"))

    if len(incoming_code) > ClaimConfig.max_claim_length:
        raise ValidationError(_("mutation.code_name_too_long"))

    if not restore and current_code != incoming_code and check_unique_claim_code(incoming_code):
        raise ValidationError(_("mutation.code_name_duplicated"))


def validate_number_of_additional_diagnoses(incoming_data):
    additional_diagnoses_count = 0
    for key in incoming_data.keys():
        if key.startswith("icd_") and key.endswith("_id") and key != "icd_id":
            additional_diagnoses_count += 1

    return additional_diagnoses_count <= ClaimConfig.additional_diagnosis_number_allowed


def submit_claim(claim, user):
    c_errors = []
    claim.save_history()
    logger.debug("SubmitClaimsMutation: validating claim %s", claim.uuid)
    c_errors += validate_claim(claim, True)
    logger.debug("SubmitClaimsMutation: claim %s validated, nb of errors: %s", claim.uuid, len(c_errors))
    if len(c_errors) == 0:
        c_errors = validate_assign_prod_to_claimitems_and_services(claim)
        logger.debug("SubmitClaimsMutation: claim %s assigned, nb of errors: %s", claim.uuid, len(c_errors))
        c_errors += process_dedrem(claim, user.id_for_audit, False)
        logger.debug("SubmitClaimsMutation: claim %s processed for dedrem, nb of errors: %s", claim.uuid,
                        len(c_errors))
    c_errors += set_claim_submitted(claim, c_errors, user)
    logger.debug("SubmitClaimsMutation: claim %s set submitted", claim.uuid)
    return c_errors


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
        error = {
            'title': claim.code,
            'list': [{'message': _("claim.mutation.failed_to_change_status_of_claim") % {'code': claim.code},
                      'detail': claim.uuid}]
        }
        if hasattr(ex, 'args') and len(ex.args)>0:
            for arg in ex.args:
            
                error['list'].append(arg)
        return [error]


def details_with_relative_prices(details):
    return details.filter(status=ClaimDetail.STATUS_PASSED) \
        .filter(price_origin=ProductItemOrService.ORIGIN_RELATIVE) \
        .exists()


def with_relative_prices(claim):
    return details_with_relative_prices(claim.items) or details_with_relative_prices(claim.services)


def set_claims_status(uuids, field, status, audit_data=None, user=None):
    errors = []
    claims = Claim.objects \
            .filter(uuid__in=uuids,
                    *filter_validity()) 
    remaining_uuid = list(map(str.upper,uuids))
    for claim in claims:
        remaining_uuid.remove(claim.uuid.upper())        
        try:
            claim.save_history()
            setattr(claim, field, status)
            # creating/cancelling feedback prompts
            if field == 'feedback_status':
                if status == Claim.FEEDBACK_SELECTED:
                    create_feedback_prompt(claim, user)
                elif status in [Claim.FEEDBACK_NOT_SELECTED, Claim.FEEDBACK_BYPASSED]:
                    set_feedback_prompt_validity_to_to_current_date(claim.uuid)
            if audit_data:
                for k, v in audit_data.items():
                    setattr(claim, k, v)
            claim.save()
        except Exception as exc:
            errors += [
                {'message': _("claim.mutation.failed_to_change_status_of_claim") %
                            {'code': claim.code} }
            ]
            if hasattr(exc, 'messages') and len(exc.messages):
                for m in exc.messages:
                    errors.append({'message': m })
            elif hasattr(exc, 'args') and len(exc.args):
                for m in exc.args:
                    errors.append({'message': m })
        if len(remaining_uuid):
            errors.append(_(
                "claim.validation.id_does_not_exist") % {'id': ','.join(remaining_uuid)})                  
                        
    return errors


def create_feedback_prompt(current_claim, user):
    
    feedback_prompt = {}
    from core.utils import TimeUtils
    feedback_prompt['feedback_prompt_date'] = TimeUtils.date()
    feedback_prompt['validity_from'] = TimeUtils.now()
    feedback_prompt['claim'] = current_claim
    villages = []
    if current_claim.insuree.current_village:
        villages.append(current_claim.insuree.current_village)
    if current_claim.insuree.family.location:
        villages.append(current_claim.insuree.family.location)
    officer = Officer.objects.filter(
        *filter_validity(),
        officer_villages__location__in=villages,
        phone__isnull = False
    ).exclude(phone__exact= '').first()
    if not officer:
        bad_officer = Officer.objects.filter(
        *filter_validity(),
        officer_villages__location__in=villages,
        ).first()
        if bad_officer:
            msg = [' officer '+bad_officer.code+ 'has not phone setup']
        else:
            msg = []
        raise RuntimeError(f"No officer with a phone number found for the insuree village code, \
            {', '.join([str(v.code) for v in villages ])}", *msg)
            
    feedback_prompt['officer_id'] = officer.id
    feedback_prompt['phone_number'] = officer.phone
    feedback_prompt['audit_user_id'] = user.id_for_audit
    FeedbackPrompt.objects.create(
        **feedback_prompt
    )


def set_feedback_prompt_validity_to_to_current_date(claim_uuid):
    try:
        claim = Claim.objects.get(uuid=claim_uuid).id
        feedback_prompt_id = FeedbackPrompt.objects.get(claim=claim, validity_to=None).id
        from core.utils import TimeUtils
        current_feedback_prompt = FeedbackPrompt.objects.get(id=feedback_prompt_id)
        current_feedback_prompt.validity_to = TimeUtils.now()
        current_feedback_prompt.save()
    except ObjectDoesNotExist:
        return "No such feedback prompt exist."


def update_claims_dedrems(uuids, user):
    # We could do it in one query with filter(claim__uuid__in=uuids) but we'd loose the logging
    errors = []
    claims = Claim.objects.filter(uuid__in=uuids)
    remaining_uuid = list(map(str.upper,uuids))
    for claim in claims:
        remaining_uuid.remove(claim.uuid.upper())       
        logger.debug(f"delivering review on {claim.uuid}, reprocessing dedrem ({user})")
        errors += validate_and_process_dedrem_claim(claim, user, False)
    if len(remaining_uuid):
        errors.append(_(
            "claim.validation.id_does_not_exist") % {'id': ','.join(remaining_uuid)})
    return errors
