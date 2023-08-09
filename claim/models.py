import uuid

from claim_batch import models as claim_batch_models
from core import fields, TimeUtils
from core import models as core_models
from django import dispatch
from django.conf import settings
from django.db import models
from graphql import ResolveInfo
from insuree import models as insuree_models
from location import models as location_models
from location.models import UserDistrict
from medical import models as medical_models
from policy import models as policy_models
from product import models as product_models
from django.apps import apps
from django.utils import timezone as django_tz

core_config = apps.get_app_config('core')


class ClaimAdmin(core_models.VersionedModel):
    id = models.AutoField(db_column='ClaimAdminId', primary_key=True)
    uuid = models.CharField(db_column='ClaimAdminUUID', max_length=36, default=uuid.uuid4, unique=True)

    code = models.CharField(db_column='ClaimAdminCode', max_length=core_config.user_username_and_code_length_limit,
                            blank=True, null=True)
    last_name = models.CharField(db_column='LastName', max_length=100, blank=True, null=True)
    other_names = models.CharField(db_column='OtherNames', max_length=100, blank=True, null=True)
    dob = models.DateField(db_column='DOB', blank=True, null=True)
    email_id = models.CharField(db_column='EmailId', max_length=200, blank=True, null=True)
    phone = models.CharField(db_column='Phone', max_length=50, blank=True, null=True)
    health_facility = models.ForeignKey(
        location_models.HealthFacility, models.DO_NOTHING, db_column='HFId', blank=True, null=True)
    has_login = models.BooleanField(db_column='HasLogin', blank=True, null=True)

    audit_user_id = models.IntegerField(db_column='AuditUserId', blank=True, null=True)
    # row_id = models.BinaryField(db_column='RowId', blank=True, null=True)

    def __str__(self):
        return self.code + " " + self.last_name + " " + self.other_names

    @classmethod
    def get_queryset(cls, queryset, user):
        queryset = cls.filter_queryset(queryset)
        # GraphQL calls with an info object while Rest calls with the user itself
        if isinstance(user, ResolveInfo):
            user = user.context.user
        if settings.ROW_SECURITY and user.is_anonymous:
            return queryset.filter(id=-1)
        if settings.ROW_SECURITY:
            dist = UserDistrict.get_user_districts(user._u)
            return queryset.filter(
                health_facility__location_id__in=[l.location_id for l in dist]
            )
        return queryset

    @property
    def id_for_audit(self):
        return self.audit_user_id

    @property
    def username(self):
        return self.code

    def get_username(self):
        return self.code

    @property
    def is_staff(self):
        return False

    @property
    def is_superuser(self):
        return False

    def set_password(self, raw_password):
        raise NotImplementedError("Shouldn't set a password on an Officer")

    def check_password(self, raw_password):
        return False

    @property
    def officer_allowed_locations(self):
        """
        Returns uuid of all locations allowed for given officer
        """
        district = self.health_facility.location
        all_allowed_uuids = [district.parent.uuid, district.uuid]
        child_locations = location_models.Location.objects.filter(parent=district).values_list('uuid', flat=True)
        while child_locations:
            all_allowed_uuids.extend(child_locations)
            child_locations = location_models.Location.objects\
                .filter(parent__uuid__in=child_locations)\
                .values_list('uuid', flat=True)

        return location_models.Location.objects.filter(uuid__in=all_allowed_uuids)

    class Meta:
        managed = True
        db_table = 'tblClaimAdmin'


class Feedback(core_models.VersionedModel):
    id = models.AutoField(db_column='FeedbackID', primary_key=True)
    uuid = models.CharField(db_column='FeedbackUUID', max_length=36, default=uuid.uuid4, unique=True)
    claim = models.OneToOneField(
        "Claim", models.DO_NOTHING, db_column='ClaimID', blank=True, null=True, related_name="+")
    care_rendered = models.BooleanField(db_column='CareRendered', blank=True, null=True)
    payment_asked = models.BooleanField(db_column='PaymentAsked', blank=True, null=True)
    drug_prescribed = models.BooleanField(db_column='DrugPrescribed', blank=True, null=True)
    drug_received = models.BooleanField(db_column='DrugReceived', blank=True, null=True)
    asessment = models.SmallIntegerField(db_column='Asessment', blank=True, null=True)
    # No FK in database (so value may not be an existing officer.id !)
    officer_id = models.IntegerField(db_column='CHFOfficerCode', blank=True, null=True)
    feedback_date = fields.DateTimeField(db_column='FeedbackDate', blank=True, null=True)
    audit_user_id = models.IntegerField(db_column='AuditUserID')

    class Meta:
        managed = True
        db_table = 'tblFeedback'

    @classmethod
    def get_queryset(cls, queryset, user):
        queryset = cls.filter_queryset(queryset)
        # GraphQL calls with an info object while Rest calls with the user itself
        if isinstance(user, ResolveInfo):
            user = user.context.user
        if settings.ROW_SECURITY and user.is_anonymous:
            return queryset.filter(id=-1)
        if settings.ROW_SECURITY:
            dist = UserDistrict.get_user_districts(user._u)
            return queryset.filter(
                claim__health_facility__location_id__in=[l.location_id for l in dist]
            )
        return queryset


class FeedbackPrompt(core_models.VersionedModel):
    id = models.AutoField(db_column='FeedbackPromptID', primary_key=True)
    feedback_prompt_date = fields.DateField(db_column='FeedbackPromptDate', blank=True, null=True)
    claim_id = models.OneToOneField(
        "Claim", models.DO_NOTHING, db_column='ClaimID', blank=True, null=True, related_name="+")
    officer_id = models.IntegerField(db_column='OfficerID', blank=True, null=True)
    phone_number = models.CharField(db_column='PhoneNumber', max_length=36, unique=True)
    sms_status = models.IntegerField(db_column='SMSStatus', blank=True, null=True)
    validity_from = fields.DateTimeField(db_column='ValidityFrom', blank=True, null=True)
    validity_to = fields.DateTimeField(db_column='ValidityTo', blank=True, null=True)
    legacy_id = models.IntegerField(db_column='LegacyID', blank=True, null=True)
    audit_user_id = models.IntegerField(db_column='AuditUserID', blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'tblFeedbackPrompt'

    @classmethod
    def get_queryset(cls, queryset, user):
        queryset = cls.filter_queryset(queryset)
        # GraphQL calls with an info object while Rest calls with the user itself
        if isinstance(user, ResolveInfo):
            user = user.context.user
        if settings.ROW_SECURITY and user.is_anonymous:
            return queryset.filter(id=-1)
        if settings.ROW_SECURITY:
            dist = UserDistrict.get_user_districts(user._u)
            return queryset.filter(
                claim__health_facility__location_id__in=[l.location_id for l in dist]
            )
        return queryset


signal_claim_rejection = dispatch.Signal(providing_args=["claim"])


class Claim(core_models.VersionedModel, core_models.ExtendableModel):
    id = models.AutoField(db_column='ClaimID', primary_key=True)
    uuid = models.CharField(db_column='ClaimUUID',
                            max_length=36, default=uuid.uuid4, unique=True)
    category = models.CharField(
        db_column='ClaimCategory', max_length=1, blank=True, null=True)
    insuree = models.ForeignKey(
        insuree_models.Insuree, models.DO_NOTHING, db_column='InsureeID')
    # do not change max_length value - use setting from apps.py
    code = models.CharField(db_column='ClaimCode', max_length=50, unique=True)
    date_from = fields.DateField(db_column='DateFrom')
    date_to = fields.DateField(db_column='DateTo', blank=True, null=True)
    status = models.SmallIntegerField(db_column='ClaimStatus')
    adjuster = models.ForeignKey(
        core_models.InteractiveUser, models.DO_NOTHING,
        db_column='Adjuster', blank=True, null=True)
    adjustment = models.TextField(
        db_column='Adjustment', blank=True, null=True)
    claimed = models.DecimalField(
        db_column='Claimed',
        max_digits=18, decimal_places=2, blank=True, null=True)
    approved = models.DecimalField(
        db_column='Approved',
        max_digits=18, decimal_places=2, blank=True, null=True)
    reinsured = models.DecimalField(
        db_column='Reinsured',
        max_digits=18, decimal_places=2, blank=True, null=True)
    valuated = models.DecimalField(
        db_column='Valuated', max_digits=18, decimal_places=2, blank=True, null=True)
    date_claimed = fields.DateField(db_column='DateClaimed')
    date_processed = fields.DateField(
        db_column='DateProcessed', blank=True, null=True)
    # Django uses the feedback_id column to create the feedback column, which conflicts with the boolean field
    feedback_available = models.BooleanField(
        db_column='Feedback', default=False)
    feedback = models.OneToOneField(
        Feedback, models.DO_NOTHING,
        db_column='FeedbackID', blank=True, null=True, related_name="+")
    explanation = models.TextField(
        db_column='Explanation', blank=True, null=True)
    feedback_status = models.SmallIntegerField(
        db_column='FeedbackStatus', blank=True, null=True, default=1)
    review_status = models.SmallIntegerField(
        db_column='ReviewStatus', blank=True, null=True, default=1)
    approval_status = models.SmallIntegerField(
        db_column='ApprovalStatus', blank=True, null=True, default=1)
    rejection_reason = models.SmallIntegerField(
        db_column='RejectionReason', blank=True, null=True, default=0)

    batch_run = models.ForeignKey(claim_batch_models.BatchRun,
                                  models.DO_NOTHING, db_column='RunID', blank=True, null=True)
    audit_user_id = models.IntegerField(db_column='AuditUserID')
    validity_from_review = fields.DateTimeField(
        db_column='ValidityFromReview', blank=True, null=True)
    validity_to_review = fields.DateTimeField(
        db_column='ValidityToReview', blank=True, null=True)

    health_facility = models.ForeignKey(
        location_models.HealthFacility, models.DO_NOTHING, db_column='HFID')

    submit_stamp = fields.DateTimeField(
        db_column='SubmitStamp', blank=True, null=True)
    process_stamp = fields.DateTimeField(
        db_column='ProcessStamp', blank=True, null=True)
    remunerated = models.DecimalField(
        db_column='Remunerated', max_digits=18, decimal_places=2, blank=True, null=True)
    guarantee_id = models.CharField(
        db_column='GuaranteeId', max_length=50, blank=True, null=True)
    admin = models.ForeignKey(
        ClaimAdmin, models.DO_NOTHING, db_column='ClaimAdminId',
        blank=True, null=True)
    refer_from = models.ForeignKey(
        location_models.HealthFacility, models.DO_NOTHING, related_name='referFromHF',
        db_column='ReferFrom', blank=True, null=True)
    refer_to = models.ForeignKey(
        location_models.HealthFacility, models.DO_NOTHING, related_name='referToHF',
        db_column='ReferTo', blank=True, null=True)
    icd = models.ForeignKey(
        medical_models.Diagnosis, models.DO_NOTHING, db_column='ICDID',
        related_name="claim_icds")
    icd_1 = models.ForeignKey(
        medical_models.Diagnosis, models.DO_NOTHING, db_column='ICDID1',
        related_name="claim_icd1s",
        blank=True, null=True)
    icd_2 = models.ForeignKey(
        medical_models.Diagnosis, models.DO_NOTHING, db_column='ICDID2',
        related_name="claim_icd2s",
        blank=True, null=True)
    icd_3 = models.ForeignKey(
        medical_models.Diagnosis, models.DO_NOTHING, db_column='ICDID3',
        related_name="claim_icd3s",
        blank=True, null=True)
    icd_4 = models.ForeignKey(
        medical_models.Diagnosis, models.DO_NOTHING, db_column='ICDID4',
        related_name="claim_icd4s",
        blank=True, null=True)

    visit_type = models.CharField(
        db_column='VisitType', max_length=1, blank=True, null=True)
    audit_user_id_review = models.IntegerField(
        db_column='AuditUserIDReview', blank=True, null=True)
    audit_user_id_submit = models.IntegerField(
        db_column='AuditUserIDSubmit', blank=True, null=True)
    audit_user_id_process = models.IntegerField(
        db_column='AuditUserIDProcess', blank=True, null=True)

    # row_id = models.BinaryField(db_column='RowID', blank=True, null=True)

    class Meta:
        managed = True
        db_table = 'tblClaim'

    STATUS_REJECTED = 1
    STATUS_ENTERED = 2
    STATUS_CHECKED = 4
    STATUS_PROCESSED = 8
    STATUS_VALUATED = 16

    FEEDBACK_IDLE = 1
    FEEDBACK_NOT_SELECTED = 2
    FEEDBACK_SELECTED = 4
    FEEDBACK_DELIVERED = 8
    FEEDBACK_BYPASSED = 16

    REVIEW_IDLE = 1
    REVIEW_NOT_SELECTED = 2
    REVIEW_SELECTED = 4
    REVIEW_DELIVERED = 8
    REVIEW_BYPASSED = 16

    def reject(self, rejection_code):
        updated_items = self.items.filter(validity_to__isnull=True).update(
            rejection_reason=rejection_code)
        updated_services = self.services.filter(
            validity_to__isnull=True).update(rejection_reason=rejection_code)
        signal_claim_rejection.send(sender=self.__class__, claim=self)
        return updated_items + updated_services

    def save_history(self, **kwargs):
        prev_id = super(Claim, self).save_history()
        if prev_id:
            prev_items = []
            for item in self.items.all():
                prev_items.append(item.save_history())
            ClaimItem.objects.filter(
                id__in=prev_items).update(claim_id=prev_id)
            prev_services = []
            for service in self.services.all():
                prev_services.append(service.save_history())
            ClaimService.objects.filter(
                id__in=prev_services).update(claim_id=prev_id)
        return prev_id

    @classmethod
    def get_queryset(cls, queryset, user):
        queryset = Claim.filter_queryset(queryset)
        # GraphQL calls with an info object while Rest calls with the user itself
        if isinstance(user, ResolveInfo):
            user = user.context.user
        if settings.ROW_SECURITY and user.is_anonymous:
            return queryset.filter(id=-1)
        if settings.ROW_SECURITY:
            # TechnicalUsers don't have health_facility_id attribute
            if hasattr(user._u, 'health_facility_id') and user._u.health_facility_id:
                return queryset.filter(
                    health_facility_id=user._u.health_facility_id
                )
            else:
                if not isinstance(user._u, core_models.TechnicalUser):
                    dist = UserDistrict.get_user_districts(user._u)
                    return queryset.filter(
                        health_facility__location_id__in=dist.values_list("location_id", flat=True)
                    )
        return queryset


class ClaimAttachmentsCount(models.Model):
    claim = models.OneToOneField(Claim, primary_key=True, related_name='attachments_count', on_delete=models.DO_NOTHING)
    value = models.IntegerField(db_column='attachments_count')

    class Meta:
        managed = True
        db_table = 'claim_ClaimAttachmentsCountView'


class ClaimMutation(core_models.UUIDModel):
    claim = models.ForeignKey(Claim, models.DO_NOTHING,
                              related_name='mutations')
    mutation = models.ForeignKey(
        core_models.MutationLog, models.DO_NOTHING, related_name='claims')

    class Meta:
        managed = True
        db_table = "claim_ClaimMutation"


class ClaimDetailManager(models.Manager):

    def filter(self, *args, **kwargs):
        keys = [x for x in kwargs if "itemsvc" in x]
        for key in keys:
            new_key = key.replace("itemsvc", self.model.model_prefix)
            kwargs[new_key] = kwargs.pop(key)
        return super(ClaimDetailManager, self).filter(*args, **kwargs)


class ClaimDetail:
    STATUS_PASSED = 1
    STATUS_REJECTED = 2

    objects = ClaimDetailManager()

    @property
    def itemsvc(self):
        if hasattr(self, "item"):
            return self.item
        elif hasattr(self, "service"):
            return self.service
        else:
            raise Exception("ClaimDetail has neither item nor service")

    class Meta:
        abstract = True


class ClaimItem(core_models.VersionedModel, ClaimDetail, core_models.ExtendableModel):
    model_prefix = "item"
    id = models.AutoField(db_column='ClaimItemID', primary_key=True)
    claim = models.ForeignKey(Claim, models.DO_NOTHING,
                              db_column='ClaimID', related_name='items')
    item = models.ForeignKey(
        medical_models.Item, models.DO_NOTHING, db_column='ItemID')
    product = models.ForeignKey(product_models.Product,
                                models.DO_NOTHING, db_column='ProdID',
                                blank=True, null=True,
                                related_name="claim_items")
    status = models.SmallIntegerField(db_column='ClaimItemStatus')
    availability = models.BooleanField(db_column='Availability')
    qty_provided = models.DecimalField(
        db_column='QtyProvided', max_digits=18, decimal_places=2)
    qty_approved = models.DecimalField(
        db_column='QtyApproved', max_digits=18, decimal_places=2, blank=True, null=True)
    price_asked = models.DecimalField(
        db_column='PriceAsked', max_digits=18, decimal_places=2)
    price_adjusted = models.DecimalField(
        db_column='PriceAdjusted', max_digits=18, decimal_places=2, blank=True, null=True)
    price_approved = models.DecimalField(
        db_column='PriceApproved', max_digits=18, decimal_places=2, blank=True, null=True)
    price_valuated = models.DecimalField(
        db_column='PriceValuated', max_digits=18, decimal_places=2, blank=True, null=True)
    explanation = models.TextField(
        db_column='Explanation', blank=True, null=True)
    justification = models.TextField(
        db_column='Justification', blank=True, null=True)
    rejection_reason = models.SmallIntegerField(
        db_column='RejectionReason', blank=True, null=True)
    audit_user_id = models.IntegerField(db_column='AuditUserID')
    validity_from_review = fields.DateTimeField(
        db_column='ValidityFromReview', blank=True, null=True)
    validity_to_review = fields.DateTimeField(
        db_column='ValidityToReview', blank=True, null=True)
    audit_user_id_review = models.IntegerField(
        db_column='AuditUserIDReview', blank=True, null=True)
    limitation_value = models.DecimalField(
        db_column='LimitationValue', max_digits=18, decimal_places=2, blank=True, null=True)
    limitation = models.CharField(
        db_column='Limitation', max_length=1, blank=True, null=True)
    policy = models.ForeignKey(
        policy_models.Policy, models.DO_NOTHING, db_column='PolicyID', blank=True, null=True)
    remunerated_amount = models.DecimalField(
        db_column='RemuneratedAmount', max_digits=18, decimal_places=2, blank=True, null=True)
    deductable_amount = models.DecimalField(
        db_column='DeductableAmount', max_digits=18, decimal_places=2, blank=True, null=True)
    exceed_ceiling_amount = models.DecimalField(
        db_column='ExceedCeilingAmount', max_digits=18, decimal_places=2, blank=True, null=True)
    price_origin = models.CharField(
        db_column='PriceOrigin', max_length=1, blank=True, null=True)
    exceed_ceiling_amount_category = models.DecimalField(
        db_column='ExceedCeilingAmountCategory', max_digits=18, decimal_places=2, blank=True, null=True)
    objects = ClaimDetailManager()

    class Meta:
        managed = True
        db_table = 'tblClaimItems'


class ClaimAttachment(core_models.UUIDModel, core_models.UUIDVersionedModel):
    claim = models.ForeignKey(
        Claim, models.DO_NOTHING, related_name='attachments')
    type = models.TextField(blank=True, null=True)
    title = models.TextField(blank=True, null=True)
    date = fields.DateField(blank=True, default=TimeUtils.now)
    filename = models.TextField(blank=True, null=True)
    mime = models.TextField(blank=True, null=True)
    # frontend contributions may lead to externalized (nas) storage for documents
    url = models.TextField(blank=True, null=True)
    # Support of BinaryField is database-related: prefer to stick to b64-encoded
    document = models.TextField(blank=True, null=True)

    class Meta:
        managed = True
        db_table = "claim_ClaimAttachment"


class ClaimService(core_models.VersionedModel, ClaimDetail, core_models.ExtendableModel):
    model_prefix = "service"
    id = models.AutoField(db_column='ClaimServiceID', primary_key=True)
    claim = models.ForeignKey(
        Claim, models.DO_NOTHING, db_column='ClaimID', related_name='services')
    service = models.ForeignKey(
        medical_models.Service, models.DO_NOTHING, db_column='ServiceID')
    product = models.ForeignKey(product_models.Product,
                                models.DO_NOTHING, db_column='ProdID',
                                blank=True, null=True,
                                related_name="claim_services")
    status = models.SmallIntegerField(db_column='ClaimServiceStatus')
    qty_provided = models.DecimalField(
        db_column='QtyProvided', max_digits=18, decimal_places=2)
    qty_approved = models.DecimalField(
        db_column='QtyApproved', max_digits=18, decimal_places=2, blank=True, null=True)
    price_asked = models.DecimalField(
        db_column='PriceAsked', max_digits=18, decimal_places=2)
    price_adjusted = models.DecimalField(
        db_column='PriceAdjusted', max_digits=18, decimal_places=2, blank=True, null=True)
    price_approved = models.DecimalField(
        db_column='PriceApproved', max_digits=18, decimal_places=2, blank=True, null=True)
    price_valuated = models.DecimalField(
        db_column='PriceValuated', max_digits=18, decimal_places=2, blank=True, null=True)
    explanation = models.TextField(
        db_column='Explanation', blank=True, null=True)
    justification = models.TextField(
        db_column='Justification', blank=True, null=True)
    rejection_reason = models.SmallIntegerField(
        db_column='RejectionReason', blank=True, null=True)
    audit_user_id = models.IntegerField(db_column='AuditUserID')
    validity_from_review = fields.DateTimeField(
        db_column='ValidityFromReview', blank=True, null=True)
    validity_to_review = fields.DateTimeField(
        db_column='ValidityToReview', blank=True, null=True)
    audit_user_id_review = models.IntegerField(
        db_column='AuditUserIDReview', blank=True, null=True)
    limitation_value = models.DecimalField(
        db_column='LimitationValue', max_digits=18, decimal_places=2, blank=True, null=True)
    limitation = models.CharField(
        db_column='Limitation', max_length=1, blank=True, null=True)
    policy = models.ForeignKey(
        policy_models.Policy, models.DO_NOTHING, db_column='PolicyID', blank=True, null=True)
    remunerated_amount = models.DecimalField(
        db_column='RemuneratedAmount', max_digits=18, decimal_places=2, blank=True, null=True)
    deductable_amount = models.DecimalField(
        db_column='DeductableAmount', max_digits=18, decimal_places=2, blank=True, null=True)
    exceed_ceiling_amount = models.DecimalField(
        db_column='ExceedCeilingAmount', max_digits=18, decimal_places=2, blank=True, null=True)
    price_origin = models.CharField(
        db_column='PriceOrigin', max_length=1, blank=True, null=True)
    exceed_ceiling_amount_category = models.DecimalField(
        db_column='ExceedCeilingAmountCategory', max_digits=18, decimal_places=2, blank=True, null=True)
    objects = ClaimDetailManager()

    class Meta:
        managed = True
        db_table = 'tblClaimServices'

class ClaimServiceItem(models.Model):
    idCsi = models.AutoField(primary_key=True, db_column='idCsi')
    item = models.ForeignKey(medical_models.Item, models.DO_NOTHING, db_column='ItemID', related_name="claimItems")                           
    claimlinkedItem = models.ForeignKey( ClaimService,
                                          models.DO_NOTHING, db_column="ClaimServiceID",related_name='claimlinkedItem')
    qty_provided = models.IntegerField(db_column="qty_provided",
                                      blank=True, null=True)
    qty_displayed = models.IntegerField(db_column="qty_displayed",
                                      blank=True, null=True)
    pcpDate = models.DateTimeField(db_column="created_date", default=django_tz.now,
                                   blank=True, null=True)
    price_asked = models.DecimalField(db_column="price",
                                   max_digits=18, decimal_places=2, blank=True, null=True)
                                   
    class Meta:
        managed = True
        db_table = 'tblClaimServicesItems'

class ClaimServiceService(models.Model):
    idCss = models.AutoField(primary_key=True, db_column='idCss')
    service = models.ForeignKey(medical_models.Service, models.DO_NOTHING,
                              db_column='ServiceId', related_name='claimServices')
    claimlinkedService = models.ForeignKey( ClaimService,
                                          models.DO_NOTHING, db_column="claimlinkedService", related_name='claimlinkedService')
    qty_provided = models.IntegerField(db_column="qty_provided",
                                      blank=True, null=True)
    qty_displayed = models.IntegerField(db_column="qty_displayed",
                                      blank=True, null=True)
    pcpDate = models.DateTimeField(db_column="created_date", default=django_tz.now,
                                   blank=True, null=True)
    price_asked = models.DecimalField(db_column="price",
                                   max_digits=18, decimal_places=2, blank=True, null=True)
    
    class Meta:
        managed = True
        db_table = 'tblClaimServicesService'

class ClaimDedRem(core_models.VersionedModel):
    id = models.AutoField(db_column='ExpenditureID', primary_key=True)

    policy = models.ForeignKey('policy.Policy', models.DO_NOTHING, db_column='PolicyID', blank=True, null=True,
                               related_name='claim_ded_rems')
    insuree = models.ForeignKey('insuree.Insuree', models.DO_NOTHING, db_column='InsureeID', blank=True, null=True,
                                related_name='claim_ded_rems')
    claim = models.ForeignKey(to=Claim, db_column='ClaimID', db_index=True, related_name="dedrems",
                              on_delete=models.DO_NOTHING)
    ded_g = models.DecimalField(db_column='DedG', max_digits=18, decimal_places=2, blank=True, null=True)
    ded_op = models.DecimalField(db_column='DedOP', max_digits=18, decimal_places=2, blank=True, null=True)
    ded_ip = models.DecimalField(db_column='DedIP', max_digits=18, decimal_places=2, blank=True, null=True)
    rem_g = models.DecimalField(db_column='RemG', max_digits=18, decimal_places=2, blank=True, null=True)
    rem_op = models.DecimalField(db_column='RemOP', max_digits=18, decimal_places=2, blank=True, null=True)
    rem_ip = models.DecimalField(db_column='RemIP', max_digits=18, decimal_places=2, blank=True, null=True)
    rem_consult = models.DecimalField(db_column='RemConsult', max_digits=18, decimal_places=2, blank=True, null=True)
    rem_surgery = models.DecimalField(db_column='RemSurgery', max_digits=18, decimal_places=2, blank=True, null=True)
    rem_delivery = models.DecimalField(db_column='RemDelivery', max_digits=18, decimal_places=2, blank=True, null=True)
    rem_hospitalization = models.DecimalField(db_column='RemHospitalization', max_digits=18, decimal_places=2,
                                              blank=True, null=True)
    rem_antenatal = models.DecimalField(db_column='RemAntenatal', max_digits=18, decimal_places=2,
                                        blank=True, null=True)

    audit_user_id = models.IntegerField(db_column='AuditUserID')

    class Meta:
        managed = True
        db_table = 'tblClaimDedRem'
