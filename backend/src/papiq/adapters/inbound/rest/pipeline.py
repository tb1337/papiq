"""`GET /pipeline`: the graph of nodes a document goes through; `POST /pipeline/reparse`."""

from fastapi import APIRouter

from papiq.adapters.inbound.rest.auth import PROTECTED, CurrentUser
from papiq.adapters.inbound.rest.context import Context
from papiq.adapters.inbound.rest.problems import problem_responses
from papiq.adapters.inbound.rest.schemas.pipeline import (
    GraphNodeOut,
    PipelineOut,
    ReparseOut,
    VisionOut,
)
from papiq.core.domain.pipeline import NODES

router = APIRouter(tags=["pipeline"], dependencies=PROTECTED)


@router.get(
    "/pipeline",
    summary="The processing graph",
    description=(
        "The nodes a document goes through and what each one needs, in graph order: every "
        "node after the nodes it needs. Nodes that do not need each other run side by side. "
        "The graph is fixed for a version of Papiq; `processing.nodes` of a document names the "
        "state of each node. `vision` names the vision language model documents can be read "
        "with, if one is configured; `logo_fetch` says whether Papiq fetches the logos of "
        "contacts from their websites."
    ),
    response_model=PipelineOut,
    responses=problem_responses(401),
)
async def pipeline(user: CurrentUser, context: Context) -> PipelineOut:
    vision = context.pipeline.vision
    return PipelineOut(
        nodes=[GraphNodeOut(node=node, requires=sorted(node.requires)) for node in NODES],
        vision=None if vision is None else VisionOut(model=vision.model, endpoint=vision.endpoint),
        logo_fetch=context.logos.fetching,
    )


@router.post(
    "/pipeline/reparse",
    status_code=202,
    summary="Read the text of every document again",
    description=(
        "Admins only. After an update of the parser (0.2 reads the text as it is set instead "
        "of Docling's Markdown): the worker reads the text of every document again whose text "
        "was read in its current run and that is not being processed. Nodes, lane and metadata "
        "stay as they are; the processing log gets the new reading (`input.reparse`), the "
        "search index takes up the new text (with embeddings, one per document). Train the "
        "classifier afterwards (`POST /classifier/train`). A document queued already is not "
        "queued twice."
    ),
    response_model=ReparseOut,
    responses=problem_responses(401, 403),
)
async def reparse(user: CurrentUser, context: Context) -> ReparseOut:
    return ReparseOut(queued=await context.pipeline.request_reparse(user))
