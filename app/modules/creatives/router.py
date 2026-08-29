from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from app.core.deps import CurrentUser, DbSession, require_product_access
from app.models.creative_asset import CreativeAsset
from app.models.creative_concept import CreativeConcept
from app.models.enums import ContentStatus, ProductRole
from app.modules.audit.service import AuditService
from app.modules.creatives.queue import ArqPoolDep
from app.modules.creatives.schemas import (
    AddContentCommentRequest,
    ContentCommentPublic,
    CreativeAssetPublic,
    CreativeConceptPublic,
    GenerateCreativesRequest,
    GenerationJobPublic,
    RejectConceptRequest,
    ScheduleConceptRequest,
)
from app.modules.creatives.service import CreativeService
from app.modules.storage.base import StorageService
from app.modules.storage.provider import get_storage_service

router = APIRouter(tags=["creatives"])


def get_creative_service(session: DbSession, arq_pool: ArqPoolDep) -> CreativeService:
    return CreativeService(session=session, audit=AuditService(session), arq_pool=arq_pool)


CreativeServiceDep = Annotated[CreativeService, Depends(get_creative_service)]
StorageServiceDep = Annotated[StorageService, Depends(get_storage_service)]


def _concept_to_public(
    concept: CreativeConcept, assets: list[CreativeAsset]
) -> CreativeConceptPublic:
    public = CreativeConceptPublic.model_validate(concept)
    public.assets = [CreativeAssetPublic.model_validate(a) for a in assets]
    return public


@router.post(
    "/api/v1/products/{product_id}/creatives/generate",
    response_model=GenerationJobPublic,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(require_product_access(ProductRole.product_manager, ProductRole.creator))
    ],
)
async def generate_creatives(
    product_id: str,
    payload: GenerateCreativesRequest,
    current_user: CurrentUser,
    service: CreativeServiceDep,
) -> GenerationJobPublic:
    job = await service.request_generation(product_id, current_user, payload)
    return GenerationJobPublic.model_validate(job)


@router.get(
    "/api/v1/products/{product_id}/creatives/jobs/{job_id}",
    response_model=GenerationJobPublic,
    dependencies=[Depends(require_product_access())],
)
async def get_generation_job(
    product_id: str, job_id: str, service: CreativeServiceDep
) -> GenerationJobPublic:
    job = await service.get_job(product_id, job_id)
    return GenerationJobPublic.model_validate(job)


@router.post(
    "/api/v1/products/{product_id}/creatives/jobs/{job_id}/render-assets",
    response_model=GenerationJobPublic,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(require_product_access(ProductRole.product_manager, ProductRole.creator))
    ],
)
async def render_creative_assets(
    product_id: str, job_id: str, current_user: CurrentUser, service: CreativeServiceDep
) -> GenerationJobPublic:
    job = await service.render_assets(product_id, job_id, current_user)
    return GenerationJobPublic.model_validate(job)


@router.get(
    "/api/v1/products/{product_id}/creatives",
    response_model=list[CreativeConceptPublic],
    dependencies=[Depends(require_product_access())],
)
async def list_creative_concepts(
    product_id: str,
    service: CreativeServiceDep,
    job_id: Annotated[str | None, Query()] = None,
    status: Annotated[ContentStatus | None, Query()] = None,
) -> list[CreativeConceptPublic]:
    concepts = await service.list_concepts(product_id, job_id=job_id, status=status)
    assets_by_concept = await service.assets_by_concept_id([c.id for c in concepts])
    return [_concept_to_public(c, assets_by_concept.get(c.id, [])) for c in concepts]


@router.get(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}",
    response_model=CreativeConceptPublic,
    dependencies=[Depends(require_product_access())],
)
async def get_creative_concept(
    product_id: str, concept_id: str, service: CreativeServiceDep
) -> CreativeConceptPublic:
    concept = await service.get_concept(product_id, concept_id)
    assets_by_concept = await service.assets_by_concept_id([concept.id])
    return _concept_to_public(concept, assets_by_concept.get(concept.id, []))


@router.post(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve",
    response_model=CreativeConceptPublic,
    dependencies=[
        Depends(require_product_access(ProductRole.product_manager, ProductRole.creator))
    ],
)
async def approve_creative_concept(
    product_id: str, concept_id: str, current_user: CurrentUser, service: CreativeServiceDep
) -> CreativeConceptPublic:
    concept = await service.approve_concept(product_id, concept_id, current_user)
    return CreativeConceptPublic.model_validate(concept)


@router.post(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}/reject",
    response_model=CreativeConceptPublic,
    dependencies=[
        Depends(require_product_access(ProductRole.product_manager, ProductRole.creator))
    ],
)
async def reject_creative_concept(
    product_id: str,
    concept_id: str,
    payload: RejectConceptRequest,
    current_user: CurrentUser,
    service: CreativeServiceDep,
) -> CreativeConceptPublic:
    concept = await service.reject_concept(product_id, concept_id, current_user, payload.reason)
    return CreativeConceptPublic.model_validate(concept)


@router.post(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}/submit-for-review",
    response_model=CreativeConceptPublic,
    dependencies=[Depends(require_product_access())],
)
async def submit_concept_for_review(
    product_id: str, concept_id: str, current_user: CurrentUser, service: CreativeServiceDep
) -> CreativeConceptPublic:
    concept = await service.submit_for_review(product_id, concept_id, current_user)
    return CreativeConceptPublic.model_validate(concept)


@router.post(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}/schedule",
    response_model=CreativeConceptPublic,
    dependencies=[
        Depends(require_product_access(ProductRole.product_manager, ProductRole.creator))
    ],
)
async def schedule_creative_concept(
    product_id: str,
    concept_id: str,
    payload: ScheduleConceptRequest,
    current_user: CurrentUser,
    service: CreativeServiceDep,
) -> CreativeConceptPublic:
    concept = await service.schedule_concept(
        product_id, concept_id, current_user, payload.scheduled_at
    )
    return CreativeConceptPublic.model_validate(concept)


@router.post(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}/publish",
    response_model=CreativeConceptPublic,
    dependencies=[
        Depends(require_product_access(ProductRole.product_manager, ProductRole.creator))
    ],
)
async def publish_creative_concept(
    product_id: str, concept_id: str, current_user: CurrentUser, service: CreativeServiceDep
) -> CreativeConceptPublic:
    concept = await service.publish_concept(product_id, concept_id, current_user)
    return CreativeConceptPublic.model_validate(concept)


@router.post(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}/approve-and-publish",
    response_model=CreativeConceptPublic,
    dependencies=[
        Depends(require_product_access(ProductRole.product_manager, ProductRole.creator))
    ],
)
async def approve_and_publish_creative_concept(
    product_id: str, concept_id: str, current_user: CurrentUser, service: CreativeServiceDep
) -> CreativeConceptPublic:
    concept = await service.approve_and_publish_concept(product_id, concept_id, current_user)
    return CreativeConceptPublic.model_validate(concept)


@router.get(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}/comments",
    response_model=list[ContentCommentPublic],
    dependencies=[Depends(require_product_access())],
)
async def list_creative_concept_comments(
    product_id: str, concept_id: str, service: CreativeServiceDep
) -> list[ContentCommentPublic]:
    comments = await service.list_comments(product_id, concept_id)
    return [ContentCommentPublic.model_validate(c) for c in comments]


@router.post(
    "/api/v1/products/{product_id}/creatives/concepts/{concept_id}/comments",
    response_model=ContentCommentPublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_product_access())],
)
async def add_creative_concept_comment(
    product_id: str,
    concept_id: str,
    payload: AddContentCommentRequest,
    current_user: CurrentUser,
    service: CreativeServiceDep,
) -> ContentCommentPublic:
    comment = await service.add_comment(product_id, concept_id, current_user, payload.body)
    return ContentCommentPublic.model_validate(comment)


@router.get(
    "/api/v1/products/{product_id}/creatives/assets/{asset_id}/download",
    dependencies=[Depends(require_product_access())],
)
async def download_creative_asset(
    product_id: str, asset_id: str, service: CreativeServiceDep, storage: StorageServiceDep
) -> Response:
    asset = await service.get_asset_for_download(product_id, asset_id)
    data = await storage.read(asset.storage_key)
    return Response(content=data, media_type=asset.mime_type or "application/octet-stream")
