"""Health endpoint and the OpenAPI contract."""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2
from pydantic import BaseModel

from papiq.adapters.inbound.rest import PREFIX, ApiContext, create_app, schemas
from papiq.composition.container import build_memory_container, build_services
from tests.builders import PASSWORD, SECRET_KEY
from tests.unit.adapters.rest.conftest import Api

PROBLEM = "application/problem+json"


async def test_health_is_ok_without_authentication(api: Api) -> None:
    response = await api.client.get(f"{PREFIX}/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"database": "ok", "object_store": "ok"}}


async def test_a_failing_check_makes_the_api_unavailable() -> None:
    container = build_memory_container()
    services = build_services(container)

    async def ok() -> None:
        pass

    async def broken() -> None:
        raise OSError("connection refused")

    app = create_app(
        ApiContext(
            auth=services.auth,
            users=services.users,
            drawers=services.drawers,
            master_data=services.master_data,
            pipeline=services.pipeline,
            documents=services.documents,
            rules=services.rules,
            rule_applications=services.rule_applications,
            webhooks=services.webhooks,
            webhook_delivery=services.webhook_delivery,
            event_bus=container.event_bus,
            health_checks={"database": broken, "object_store": ok},
            max_upload_size=1,
        )
    )
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://papiq"
    ) as client:
        response = await client.get(f"{PREFIX}/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "failed", "object_store": "ok"},
    }


async def test_a_failing_search_only_degrades_the_api() -> None:
    async def ok() -> None:
        pass

    async def broken() -> None:
        raise OSError("connection refused")

    async def health(checks: dict[str, Callable[[], Awaitable[None]]]) -> httpx2.Response:
        container = build_memory_container()
        services = build_services(container)
        app = create_app(
            ApiContext(
                auth=services.auth,
                users=services.users,
                drawers=services.drawers,
                master_data=services.master_data,
                pipeline=services.pipeline,
                documents=services.documents,
                rules=services.rules,
                rule_applications=services.rule_applications,
                webhooks=services.webhooks,
                webhook_delivery=services.webhook_delivery,
                event_bus=container.event_bus,
                health_checks=checks,
                max_upload_size=1,
                optional_checks=frozenset({"search"}),
            )
        )
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://papiq"
        ) as client:
            return await client.get(f"{PREFIX}/health")

    degraded = await health({"database": ok, "search": broken})
    assert degraded.status_code == 200
    assert degraded.json() == {
        "status": "degraded",
        "checks": {"database": "ok", "search": "failed"},
    }
    down = await health({"database": broken, "search": broken})
    assert (down.status_code, down.json()["status"]) == (503, "unavailable")


async def openapi(api: Api) -> dict[str, Any]:
    response = await api.client.get(f"{PREFIX}/openapi.json")
    assert response.status_code == 200
    schema: dict[str, Any] = response.json()
    return schema


async def test_openapi_lists_every_endpoint(api: Api) -> None:
    paths = (await openapi(api))["paths"]
    master_data = {
        (f"{PREFIX}/{kind}{suffix}", method)
        for kind in ("contacts", "document-types", "tags", "fields")
        for suffix, method in [
            ("", "get"),
            ("", "post"),
            ("/{id}", "get"),
            ("/{id}", "patch"),
            ("/{id}", "delete"),
        ]
    }
    assert {(path, method) for path, item in paths.items() for method in item} == {
        (f"{PREFIX}/auth/login", "post"),
        (f"{PREFIX}/auth/logout", "post"),
        (f"{PREFIX}/auth/me", "get"),
        (f"{PREFIX}/auth/password", "post"),
        (f"{PREFIX}/auth/sessions", "delete"),
        (f"{PREFIX}/auth/totp", "post"),
        (f"{PREFIX}/auth/totp/confirm", "post"),
        (f"{PREFIX}/auth/totp/disable", "post"),
        (f"{PREFIX}/auth/totp/recovery-codes", "post"),
        (f"{PREFIX}/auth/tokens", "get"),
        (f"{PREFIX}/auth/tokens", "post"),
        (f"{PREFIX}/auth/tokens/{{id}}", "delete"),
        (f"{PREFIX}/auth/oidc", "get"),
        (f"{PREFIX}/auth/oidc/login", "get"),
        (f"{PREFIX}/auth/oidc/callback", "get"),
        (f"{PREFIX}/auth/oidc/link", "post"),
        (f"{PREFIX}/auth/oidc/link", "delete"),
        (f"{PREFIX}/users", "get"),
        (f"{PREFIX}/users", "post"),
        (f"{PREFIX}/users/{{id}}", "get"),
        (f"{PREFIX}/users/{{id}}", "patch"),
        (f"{PREFIX}/users/{{id}}", "delete"),
        (f"{PREFIX}/users/{{id}}/password", "post"),
        (f"{PREFIX}/users/{{id}}/totp", "delete"),
        (f"{PREFIX}/users/{{id}}/oidc", "delete"),
        *master_data,
        (f"{PREFIX}/drawers", "get"),
        (f"{PREFIX}/drawers", "post"),
        (f"{PREFIX}/drawers/{{id}}", "get"),
        (f"{PREFIX}/drawers/{{id}}", "patch"),
        (f"{PREFIX}/drawers/{{id}}", "delete"),
        (f"{PREFIX}/drawers/{{id}}/shares/{{user_id}}", "put"),
        (f"{PREFIX}/drawers/{{id}}/shares/{{user_id}}", "delete"),
        (f"{PREFIX}/rules", "get"),
        (f"{PREFIX}/rules", "post"),
        (f"{PREFIX}/rules/{{id}}", "get"),
        (f"{PREFIX}/rules/{{id}}", "put"),
        (f"{PREFIX}/rules/{{id}}", "patch"),
        (f"{PREFIX}/rules/{{id}}", "delete"),
        (f"{PREFIX}/rules/{{id}}/versions", "get"),
        (f"{PREFIX}/rules/{{id}}/versions/{{number}}", "get"),
        (f"{PREFIX}/rules/{{id}}/apply/preview", "post"),
        (f"{PREFIX}/rules/{{id}}/apply", "post"),
        (f"{PREFIX}/rule-applications/{{id}}", "get"),
        (f"{PREFIX}/webhooks", "get"),
        (f"{PREFIX}/webhooks", "post"),
        (f"{PREFIX}/webhooks/{{id}}", "get"),
        (f"{PREFIX}/webhooks/{{id}}", "patch"),
        (f"{PREFIX}/webhooks/{{id}}", "delete"),
        (f"{PREFIX}/webhooks/{{id}}/secret", "post"),
        (f"{PREFIX}/webhooks/{{id}}/deliveries", "get"),
        (f"{PREFIX}/webhooks/{{id}}/test", "post"),
        (f"{PREFIX}/documents", "get"),
        (f"{PREFIX}/documents", "post"),
        (f"{PREFIX}/documents/search", "get"),
        (f"{PREFIX}/search/reindex", "post"),
        (f"{PREFIX}/documents/{{id}}", "get"),
        (f"{PREFIX}/documents/{{id}}", "patch"),
        (f"{PREFIX}/documents/{{id}}", "delete"),
        (f"{PREFIX}/documents/{{id}}/move", "post"),
        (f"{PREFIX}/documents/{{id}}/original", "get"),
        (f"{PREFIX}/documents/{{id}}/archive", "get"),
        (f"{PREFIX}/documents/{{id}}/preview", "get"),
        (f"{PREFIX}/documents/{{id}}/log", "get"),
        (f"{PREFIX}/documents/{{id}}/retry", "post"),
        (f"{PREFIX}/documents/{{id}}/reprocess", "post"),
        (f"{PREFIX}/documents/{{id}}/review", "get"),
        (f"{PREFIX}/documents/{{id}}/confirm", "post"),
        (f"{PREFIX}/documents/{{id}}/dry-run", "post"),
        (f"{PREFIX}/inbox", "get"),
        (f"{PREFIX}/events", "get"),
        (f"{PREFIX}/health", "get"),
    }


async def test_errors_are_documented_as_problems_with_examples(api: Api) -> None:
    schema = await openapi(api)
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            assert operation.get("summary"), (path, method)
            for status, response in operation["responses"].items():
                if not status.startswith(("4", "5")) or path.endswith("/health"):
                    continue
                content = response["content"]
                assert list(content) == [PROBLEM], (path, method, status)
                assert content[PROBLEM]["schema"] == {"$ref": "#/components/schemas/Problem"}
                assert content[PROBLEM]["example"]["status"] == int(status)
    upload = schema["paths"][f"{PREFIX}/documents"]["post"]
    statuses = {"202", "400", "401", "403", "404", "409", "413", "415", "422", "500"}
    assert set(upload["responses"]) == statuses
    problem = schema["components"]["schemas"]["Problem"]["properties"]
    assert {"type", "title", "status", "detail", "existing_document_id"} <= set(problem)
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "HTTPValidationError" not in str(schema["paths"])


async def test_the_upload_body_is_multipart(api: Api) -> None:
    body = (await openapi(api))["paths"][f"{PREFIX}/documents"]["post"]["requestBody"]
    form = body["content"]["multipart/form-data"]["schema"]
    assert form["required"] == ["file"]
    assert form["properties"]["file"] == {
        "type": "string",
        "format": "binary",
        "description": "PDF, JPEG, PNG or TIFF; recognised by content.",
    }
    assert form["properties"]["drawer_id"]["format"] == "uuid"


async def test_schemas_of_status_log_and_events(api: Api) -> None:
    schema = await openapi(api)
    schemas = schema["components"]["schemas"]
    assert {"DocumentDetails", "DocumentAccepted", "LogEntry", "EventMessage", "Health"} <= set(
        schemas
    )
    reprocess = schemas["ReprocessRequest"]["properties"]["from_step"]
    step = schemas[reprocess["$ref"].rsplit("/", 1)[1]]
    assert "receive" not in step["enum"]
    resume = schemas["ConfirmRequest"]["properties"]["resume_at"]
    resume_at = schemas[resume["$ref"].rsplit("/", 1)[1]]
    assert resume_at["enum"] == ["extract_fields", "apply_rules"]
    events = schema["paths"][f"{PREFIX}/events"]["get"]["responses"]["200"]["content"]
    stream = events["text/event-stream"]
    assert stream["schema"]["type"] == "string"
    data = stream["itemSchema"]["properties"]["data"]
    assert data["contentSchema"] == {"$ref": "#/components/schemas/EventMessage"}
    assert "$ref" not in stream["itemSchema"]


async def test_every_operation_declares_its_security(api: Api) -> None:
    schema = await openapi(api)
    schemes = schema["components"]["securitySchemes"]
    assert schemes["session"]["type"] == "apiKey" and schemes["session"]["in"] == "cookie"
    assert schemes["token"] == {
        "type": "http",
        "scheme": "bearer",
        "description": schemes["token"]["description"],
    }
    allowed: list[list[dict[str, list[str]]]] = [
        [],
        [{"session": []}],
        [{"session": []}, {"token": []}],
    ]
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            security = operation["security"]
            assert security in allowed, (path, method)
            responses = operation["responses"]
            if security:
                assert "401" in responses, (path, method)
            if security == [{"session": []}]:
                assert "403" in responses, (path, method)
            assert "500" in responses or path.endswith("/health"), (path, method)
            assert operation.get("summary"), (path, method)


async def test_bodies_and_answers_are_described(api: Api) -> None:
    schema = await openapi(api)
    components = schema["components"]["schemas"]
    for name in ("LoginRequest", "SessionOut", "Me", "TokenCreated", "DocumentDetails"):
        assert name in components, name
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            for status, response in operation["responses"].items():
                assert response.get("description"), (path, method, status)
    login = schema["paths"][f"{PREFIX}/auth/login"]["post"]
    assert set(login["responses"]) >= {"200", "401", "413", "415", "422", "429", "500"}
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if "requestBody" in operation:
                assert "413" in operation["responses"], (path, method)
    assert login["requestBody"]["content"]["application/json"]


async def test_refusals_are_described(api: Api) -> None:
    """Every protected change may be refused (a `read` token, no CSRF header): 403."""
    schema = await openapi(api)
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            if operation["security"] and method != "get":
                assert "403" in operation["responses"], (path, method)
    me = schema["paths"][f"{PREFIX}/auth/tokens"]["get"]
    assert "403" in me["responses"]  # session only


async def test_request_bodies_have_examples(api: Api) -> None:
    schema = await openapi(api)
    components = schema["components"]["schemas"]
    for path, item in schema["paths"].items():
        for method, operation in item.items():
            content = operation.get("requestBody", {}).get("content", {})
            if "application/json" in content:
                name = content["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]
                assert components[name].get("examples"), (path, method, name)


def test_examples_are_valid_bodies() -> None:
    models = [
        model
        for model in vars(schemas).values()
        if isinstance(model, type) and issubclass(model, BaseModel) and model is not BaseModel
    ]
    for model in models:
        extra = model.model_config.get("json_schema_extra")
        examples = extra.get("examples", []) if isinstance(extra, dict) else []
        assert isinstance(examples, list), model
        for example in examples:
            model.model_validate(example)


async def test_no_secrets_in_the_contract(api: Api) -> None:
    """Examples are placeholders; nothing of the test configuration leaks into the document."""
    text = json.dumps(await openapi(api))
    for secret in (PASSWORD, SECRET_KEY):
        assert secret not in text
    assert not re.search(r"papiq_[A-Za-z0-9_-]{20,}", text)
    assert "BEGIN" not in text and "PRIVATE" not in text
    assert (
        "password"
        not in json.dumps(
            [
                example
                for item in json.loads(text)["paths"].values()
                for operation in item.values()
                for response in operation["responses"].values()
                for content in response.get("content", {}).values()
                for example in [content.get("example")]
                if example
            ]
        ).lower()
    )
