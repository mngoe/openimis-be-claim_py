from enum import Enum
from django.utils.translation import gettext as _

class ClaimNotificationKeys(str, Enum):
    ON_SUBMITTED = "claim_notification.sms_on_submitted"
    ON_ADMIN_FOSA_VALIDATION = "claim_notification.on_admin_validation"
    ON_APPROVED = "claim_notification.sms_on_approved"
    ON_REJECTED = "claim_notification.sms_on_rejected"
    ON_PENDING = "claim_notification.sms_on_pending"

class ClaimNotificationTemplates:
    def template_for_key(self, key: ClaimNotificationKeys):
        return _(key.value)
