"""Health endpoint and the OpenAPI contract."""

from typing import Any

import httpx

from papiq.adapters.inbound.rest import PREFIX, ApiContext, create_app
from papiq.composition.container import build_memory_container, build_services
from tests.unit.adapters.rest.conftest import Api

PROBLEM = "application/problem+json"


async def test_health_is_ok_without_authentication(api: Api) -> None:
    del api.app.dependency_overrides[next(iter(api.app.dependency_overrides))]
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
            pipeline=services.pipeline,
            documents=services.documents,
            event_bus=container.event_bus,
            health_checks={"database": broken, "object_store": ok},
            max_upload_size=1,
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://papiq"
    ) as client:
        response = await client.get(f"{PREFIX}/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "checks": {"database": "failed", "object_store": "ok"},
    }


async def openapi(api: Api) -> dict[str, Any]:
    response = await api.client.get(f"{PREFIX}/openapi.json")
    assert response.status_code == 200
    schema: dict[str, Any] = response.json()
    return schema


async def test_openapi_lists_every_endpoint(api: Api) -> None:
    paths = (await openapi(api))["paths"]
    assert {(path, method) for path, item in paths.items() for method in item} == {
        (f"{PREFIX}/documents", "post"),
        (f"{PREFIX}/documents/{{id}}", "get"),
        (f"{PREFIX}/documents/{{id}}/log", "get"),
        (f"{PREFIX}/documents/{{id}}/retry", "post"),
        (f"{PREFIX}/documents/{{id}}/reprocess", "post"),
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
    assert {"DocumentStatus", "DocumentAccepted", "LogEntry", "EventMessage", "Health"} <= set(
        schemas
    )
    reprocess = schemas["ReprocessRequest"]["properties"]["from_step"]
    step = schemas[reprocess["$ref"].rsplit("/", 1)[1]]
    assert "receive" not in step["enum"]
    events = schema["paths"][f"{PREFIX}/events"]["get"]["responses"]["200"]["content"]
    stream = events["text/event-stream"]
    assert stream["schema"]["type"] == "string"
    data = stream["itemSchema"]["properties"]["data"]
    assert data["contentSchema"] == {"$ref": "#/components/schemas/EventMessage"}
    assert "$ref" not in stream["itemSchema"]
