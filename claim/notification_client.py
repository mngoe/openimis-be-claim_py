import logging
from django.core.mail import send_mail
from django.conf import settings
from claim.notification_templates import ClaimNotificationKeys, ClaimNotificationTemplates
from core.models.user import UserRole, User, InteractiveUser, Role

logger = logging.getLogger(__name__)

class ClaimNotificationClient:
    def __init__(self):
        self.templates = ClaimNotificationTemplates()

    def get_eligible_users(self, preauth, key):
        """Récupère les utilisateurs éligibles pour recevoir la notification"""
        if key in [ClaimNotificationKeys.ON_SUBMITTED, ClaimNotificationKeys.ON_APPROVED, ClaimNotificationKeys.ON_REJECTED]:
            if preauth.admin and preauth.admin.email_id:
                return [preauth.admin]
        
        if key in [ClaimNotificationKeys.ON_ADMIN_FOSA_VALIDATION, ClaimNotificationKeys.ON_PENDING]:
            MEDICAL_OFFICER_ROLE_NAME = "Medical Officer"
            
            try:
                role = Role.objects.get(name=MEDICAL_OFFICER_ROLE_NAME)
                user_roles = UserRole.objects.filter(role=role).select_related('user')
                
                users = []
                for ur in user_roles:
                    if ur.user and ur.user.email:  # Vérifie que l'email existe
                        users.append(ur.user)
                
                return users
            except Role.DoesNotExist:
                logger.error(f"Role '{MEDICAL_OFFICER_ROLE_NAME}' not found")
                return []
        
        return []

    def get_context(self, preauth, key, user):
        """Prépare le contexte pour le template d'email"""
        context = {
            "code": preauth.code_pre_authorization,
            "user_name": user.username if hasattr(user, 'username') else "Utilisateur",
            "claim_code": preauth.code,
            "status": self.get_status_label(key)
        }
        return context

    def get_status_label(self, key):
        """Retourne le libellé du statut selon la clé"""
        status_labels = {
            ClaimNotificationKeys.ON_SUBMITTED: "Soumise",
            ClaimNotificationKeys.ON_APPROVED: "Approuvée",
            ClaimNotificationKeys.ON_REJECTED: "Rejetée",
            ClaimNotificationKeys.ON_ADMIN_FOSA_VALIDATION: "En attente de validation FOSA",
            ClaimNotificationKeys.ON_PENDING: "En attente"
        }
        return status_labels.get(key, "Mise à jour")

    def get_email_subject(self, key):
        """Génère le sujet de l'email selon la clé de notification"""
        subjects = {
            ClaimNotificationKeys.ON_SUBMITTED: "Nouvelle demande de préautorisation soumise",
            ClaimNotificationKeys.ON_APPROVED: "Préautorisation approuvée",
            ClaimNotificationKeys.ON_REJECTED: "Préautorisation rejetée",
            ClaimNotificationKeys.ON_ADMIN_FOSA_VALIDATION: "Préautorisation en attente de validation",
            ClaimNotificationKeys.ON_PENDING: "Préautorisation en attente de traitement"
        }
        return subjects.get(key, "Notification OpenIMIS")

    def send_notification(self, preauth, user, key):
        """Envoie un email de notification"""
        template_attr = self.templates.template_for_key(key)
        if not template_attr:
            logger.error(f"Template {key} not found")
            return None

        email = user.email
        if not email:
            logger.warning(f"The user {user} doesn't have an email address")
            return None

        context = self.get_context(preauth, key, user)
        
        # Construction du message
        try:
            message = template_attr % context
        except KeyError as e:
            logger.warning(f"Key {e} not found in template {key}. The message can't be formatted")
            message = template_attr

        # Sujet de l'email
        subject = self.get_email_subject(key)
        
        # Message HTML optionnel (plus professionnel)
        html_message = f"""
        <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 5px;">
                    <h2 style="color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
                        OpenIMIS - Notification
                    </h2>
                    <div style="margin: 20px 0;">
                        <p>{message}</p>
                    </div>
                    <div style="margin-top: 30px; padding-top: 20px; border-top: 1px solid #eee; font-size: 12px; color: #666;">
                        <p>Ceci est un message automatique, merci de ne pas répondre à cet email.</p>
                    </div>
                </div>
            </body>
        </html>
        """

        try:
            # Envoi de l'email
            send_mail(
                subject=subject,
                message=message,  # Version texte brut
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@openimis.org'),
                recipient_list=[email],
                html_message=html_message,  # Version HTML
                fail_silently=False,
            )
            logger.info(f"Email sent successfully to {email} for claim {preauth.code}")
            return {"success": True, "email": email}
        
        except Exception as e:
            logger.error(f"Failed to send email to {email}: {str(e)}")
            return {"success": False, "email": email, "error": str(e)}

    def send_preauthorization_notifications(self, preauth, key):
        """Envoie les notifications à tous les utilisateurs éligibles"""
        users = self.get_eligible_users(preauth, key)
        
        if not users:
            logger.warning(f"No eligible users found for notification key: {key}")
            return []
        
        results = []
        for user in users:
            result = self.send_notification(preauth, user, key)
            if result:
                results.append((user, result))
        
        return results

    
class ClaimNotificationSender:
    @classmethod
    def send_preauthorization_notifications(cls, preauth, key):
        """Point d'entrée principal pour envoyer les notifications"""
        logger.info(f"Sending email notifications for claim {preauth.code} with key {key}")
        
        client = ClaimNotificationClient()
        results = client.send_preauthorization_notifications(preauth, key)
        
        # Log des résultats
        success_count = sum(1 for _, r in results if r.get('success'))
        logger.info(f"Sent {success_count}/{len(results)} email notifications successfully")
        
        return results