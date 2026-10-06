"""Webhook delivery (egress): subscribes to the event bus and sends signed (HMAC-SHA256)
HTTP requests with retries and a delivery log.

Webhooks belong to a user and only report events for documents that user may see.
Payloads are thin (event type, time, event id, document id); receivers fetch details through
the API with their own token.
"""
