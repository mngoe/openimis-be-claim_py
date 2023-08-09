from claim.models import Claim, ClaimItem, ClaimService, ClaimDetail, ClaimServiceItem ,ClaimServiceService
from medical.models import Item, Service
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _


def process_child_relation(user, data_children, claim_id, children, create_hook):
    claimed = 0
    from core.utils import TimeUtils
    if __check_if_maximum_amount_overshoot(data_children, children):
        raise ValidationError(_("mutation.claim_item_service_maximum_amount_overshoot"))
    for data_elt in data_children:
        if create_hook==service_create_hook :
            claimed += calcul_amount_service(data_elt)
        else:
            claimed += data_elt['qty_provided'] * data_elt['price_asked']

        elt_id = data_elt.pop('id') if 'id' in data_elt else None
        if elt_id:
            # elt has been historized along with claim historization
            elt = children.get(id=elt_id)
            [setattr(elt, k, v) for k, v in data_elt.items()]
            elt.validity_from = TimeUtils.now()
            elt.audit_user_id = user.id_for_audit
            elt.claim_id = claim_id
            elt.validity_to = None
            if create_hook==service_create_hook :
                service_update_hook(elt.claim_id, data_elt)

            elt.save()
        else:
            data_elt['validity_from'] = TimeUtils.now()
            data_elt['audit_user_id'] = user.id_for_audit
            # Ensure claim id from func argument will be assigned
            data_elt.pop('claim_id', None)
            # Should entered claim items/services have status passed assigned?
            # Status is mandatory field, and it doesn't have default value in model
            data_elt['status'] = ClaimDetail.STATUS_PASSED
            create_hook(claim_id, data_elt)

    return claimed

def calcul_amount_service(elt):
    totalClaimed = elt['price_asked']
    if len(elt['serviceLinked'])!=0 and len(elt['serviceserviceSet'])!=0:
        totalClaimed = 0
        for serviceLinked in elt['serviceLinked']:
            if serviceLinked['qty_asked'].is_nan() == False:
                totalClaimed += serviceLinked['qty_asked'] * serviceLinked['price_asked']
        for serviceserviceSet in elt['serviceserviceSet']:
            if serviceserviceSet['qty_asked'].is_nan() == False:
                totalClaimed += serviceserviceSet['qty_asked'] * serviceserviceSet['price_asked']
    return totalClaimed
        

def __check_if_maximum_amount_overshoot(data_children, children):
    is_overshoot = False
    for entity in data_children:
        quantity = entity.get('qty_provided')
        maximum_amount = None

        if children.model == ClaimItem:
            current_item = Item.objects.get(id=entity['item_id'], validity_to__isnull=True)
            maximum_amount = int(current_item.maximum_amount) if current_item.maximum_amount else None
        elif children.model == ClaimService:
            current_service = Service.objects.get(id=entity['service_id'], validity_to__isnull=True)
            maximum_amount = int(current_service.maximum_amount) if current_service.maximum_amount else None

        if maximum_amount is not None and (quantity > maximum_amount):
            is_overshoot = True
            break

    return is_overshoot


def item_create_hook(claim_id, item):
    # TODO: investigate 'availability' is mandatory,
    # but not in UI > always true?
    item['availability'] = True
    ClaimItem.objects.create(claim_id=claim_id, **item)


def service_create_hook(claim_id, service):
    serviceLinked = service.serviceLinked
    serviceserviceSet = service.serviceserviceSet
    service.pop('serviceLinked', None)
    service.pop('serviceserviceSet', None)
    ClaimServiceId = ClaimService.objects.create(claim_id=claim_id, **service)
    if(serviceLinked):
        for serviceL in serviceLinked:
            serviceL.pop('subItemCode', None)
            if serviceL.qty_asked.is_nan() :
                serviceL.qty_asked = 0
            itemId = Item.objects.filter(code=serviceL.subItemCode).first()
            ClaimServiceItem.objects.create(
                item = itemId,
                claimlinkedItem = ClaimServiceId,
                qty_displayed = serviceL.qty_asked,
                qty_provided = serviceL.qty_provided,
                price_asked = serviceL.price_asked,
            )

    if(serviceserviceSet):
        for serviceserviceS in serviceserviceSet:
            serviceserviceS.pop('subItemCode', None)
            if serviceserviceS.qty_asked.is_nan() :
                serviceserviceS.qty_asked = 0
            serviceId = Service.objects.filter(code=serviceserviceS.subServiceCode).first()
            ClaimServiceService.objects.create(
                service = serviceId,
                claimlinkedService = ClaimServiceId,
                qty_displayed = serviceserviceS.qty_asked,
                qty_provided = serviceserviceS.qty_provided,
                price_asked = serviceserviceS.price_asked,
            )

def service_update_hook(claim_id, service):
    serviceLinked = service.serviceLinked
    serviceserviceSet = service.serviceserviceSet
    service.pop('serviceLinked', None)
    service.pop('serviceserviceSet', None)
    ClaimServiceId = ClaimService.objects.filter(claim=claim_id, service=service.service_id).first()
    if(serviceLinked):
        for serviceL in serviceLinked:
            serviceL.pop('subItemCode', None)
            if serviceL.qty_asked.is_nan() :
                serviceL.qty_asked = 0
            itemId = Item.objects.filter(code=serviceL.subItemCode).first()
            claimServiceItemId = ClaimServiceItem.objects.filter(
                item=itemId,
                claimlinkedItem = ClaimServiceId
            ).first()
            claimServiceItemId.qty_displayed=serviceL.qty_asked
            claimServiceItemId.save()

    if(serviceserviceSet):
        for serviceserviceS in serviceserviceSet:
            serviceserviceS.pop('subItemCode', None)
            if serviceserviceS.qty_asked.is_nan() :
                serviceserviceS.qty_asked = 0
            serviceId = Service.objects.filter(code=serviceserviceS.subServiceCode).first()
            claimServiceServiceId = ClaimServiceService.objects.filter(
                service=serviceId,
                claimlinkedService = ClaimServiceId
            ).first()
            claimServiceServiceId.qty_displayed=serviceserviceS.qty_asked
            claimServiceServiceId.save()

def process_items_relations(user, claim, items):
    return process_child_relation(user, items, claim.id, claim.items, item_create_hook)


def process_services_relations(user, claim, services):
    return process_child_relation(user, services, claim.id, claim.services, service_create_hook)


def autogenerate_nepali_claim_code(config):
    code_length = config.get('code_length')
    if not code_length and type(code_length) is not int:
        raise ValueError("Invalid config for `autogenerate_nepali_claim_code`, expected `code_length` value")
    prefix = __get_current_nepali_fiscal_year_code()
    last_claim = Claim.objects.filter(validity_to__isnull=True, code__icontains=prefix)
    code = 0
    if last_claim:
        code = int(last_claim.latest('code').code[-code_length:])
    return prefix + str(code+1).zfill(code_length)


def __get_current_nepali_fiscal_year_code():
    import nepali_datetime
    current_date = nepali_datetime.date.today()

    if current_date.month < 4:
        current_year = nepali_datetime.date.today().year - 1
    else:
        current_year = nepali_datetime.date.today().year

    year_code = str(current_year) + "-" + str(current_year+1)[-3:] + "-"
    return year_code
