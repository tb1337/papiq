"""Webhook delivery (egress): the HTTP sender. The rest — signing, rights, retries and the
delivery log — is the core's `webhooks` service.

Webhooks belong to a user and only report events for documents that user may see.
Payloads are thin (event type, time, event id, document id); receivers fetch details through
the API with their own token.
"""

from papiq.adapters.outbound.webhooks.sender import HttpWebhookSender

__all__ = ["HttpWebhookSender"]
