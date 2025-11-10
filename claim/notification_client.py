import logging
from claim.notification_templates import ClaimNotificationKeys, ClaimNotificationTemplates
from policy_notification.notification_gateways.abstract_sms_gateway import NotificationGatewayAbs
from policy_notification.utils import get_notification_providers
from core.models import User
from claim.models import Claim
from core.models.user import UserRole, User, InteractiveUser, Role

logger = logging.getLogger(__name__)

class ClaimNotificationClient:
    def __init__(self, provider):
        self.provider = provider
        self.templates = ClaimNotificationTemplates()

    def get_eligible_users(self, preauth, key):
        if key in [ClaimNotificationKeys.ON_SUBMITTED, ClaimNotificationKeys.ON_APPROVED, ClaimNotificationKeys.ON_REJECTED]:
            if preauth.admin and preauth.admin.phone:
                return [preauth.admin]
        if key in [ClaimNotificationKeys.ON_ADMIN_FOSA_VALIDATION, ClaimNotificationKeys.ON_PENDING]:
            MEDICAL_OFFICER_ROLE_NAME = "Medical Officer"

            role = Role.objects.get(name=MEDICAL_OFFICER_ROLE_NAME)
            user_roles = UserRole.objects.filter(role=role).select_related('user')

            users = []
            for ur in user_roles:
                users.append(ur.user._u)

            return users
        
        return []

    def get_context(self, preauth , key):
        context = {"code": preauth.code}
        return context

    def send_notification(self, preauth, user, key):
        template_attr = self.templates.template_for_key(key)
        if not template_attr:
            logger.error(f"Template {key} not found")
            return None

        context = self.get_context(preauth, key, user)
        phone = user.phone
        if not phone:
            logger.warning(f"The user {user} doesn't have a phone number")
            return None

        message = template_attr
        try:
            message = message % context
        except KeyError as e:
            logger.warning(f"Key {e} not found in template {key}. The message can't be formatted")

        return self.provider.send_notification(message, family_number=phone)

    def send_preauthorization_notifications(self, preauth, key):
        users = self.get_eligible_users(preauth, key)
        results = []
        for user in users:
            result = self.send_notification(preauth, user, key)
            if result:
                results.append((user, result))
        return results
    
class ClaimNotificationSender:
    @classmethod
    def send_preauthorization_notifications(self, preauth, key):
        providers= get_notification_providers()
        for provider in providers:
            client=ClaimNotificationClient(provider=provider())
            client.send_preauthorization_notifications(preauth, key)