import xml.etree.ElementTree as ET

from medical.models import Item, Service

import core
from django.db import connection, transaction
from gettext import gettext as _

from core.signals import register_service_signal
from .apps import ClaimConfig
from django.conf import settings

from claim.models import Claim, ClaimItem, ClaimService
from claim.utils import process_items_relations, process_services_relations
from .validations import validate_claim, validate_assign_prod_to_claimitems_and_services, process_dedrem, \
    approved_amount, get_claim_category

from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied, ValidationError


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
        from location.models import UserDistrict, HealthFacility
        dist = UserDistrict.get_user_districts(self.user._u)
        hf = HealthFacility.filter_queryset().filter(code=hf_code)\
            .filter(location_id__in=[l.location_id for l in dist])\
            .exists()
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
            from location.models import UserDistrict
            dist = UserDistrict.get_user_districts(self.user._u)
            queryset = queryset.filter(
                health_facility__location__id__in=[l.location_id for l in dist]
            )
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
        from location.models import UserDistrict, HealthFacility
        dist = UserDistrict.get_user_districts(self.user._u)
        hf = HealthFacility.filter_queryset().filter(id=hf_id)\
            .filter(location_id__in=[l.location_id for l in dist])\
            .exists()
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
        claim = Claim.objects.create(**claim_submit_data)
        self.__process_items(claim, items, services)
        claim.save()
        return claim

    def __process_items(self, claim, items, services):
        claimed = 0
        claimed += process_items_relations(self.user, claim, items)
        claimed += process_services_relations(self.user, claim, services)
        claim.claimed = claimed


def check_unique_claim_code(code):
    if Claim.objects.filter(code=code, validity_to__isnull=True).exists():
        return [{"message": "Claim code %s already exists" % code}]
    return []
