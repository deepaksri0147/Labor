"""Data-template and prompt-template endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Principal, current_principal, enforce_tenant
from app.db.session import get_session
from app.models import orm
from app.schemas.dita import (
    DataTemplate,
    PromptTemplate,
    PromptTemplateRenderRequest,
    PromptTemplateRenderResponse,
)
from app.services import common
from app.services.validation import validate_against_schema

router = APIRouter(tags=["Templates"])
SEC = [{"bearerAuth": []}]


async def _load_asset(session, asset_id, kind, principal) -> orm.VersionedAsset:
    a = (await session.execute(
        select(orm.VersionedAsset).where(orm.VersionedAsset.asset_id == asset_id, orm.VersionedAsset.asset_kind == kind)
    )).scalar_one_or_none()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{kind} not found")
    enforce_tenant(principal, a.tenant_id)
    return a


async def _save_version(session, asset: orm.VersionedAsset) -> None:
    session.add(orm.AssetVersion(asset_id=asset.asset_id, asset_kind=asset.asset_kind,
                                 version=asset.version, spec=asset.spec))


# ---- data templates -------------------------------------------------------
@router.post("/data-templates", response_model=DataTemplate)
async def create_data_template(t: DataTemplate, principal: Principal = Depends(current_principal),
                               session: AsyncSession = Depends(get_session)) -> DataTemplate:
    enforce_tenant(principal, t.tenant_id)
    tid = t.template_id or common.new_id("dt")
    out = t.model_copy(update={"template_id": tid})
    asset = orm.VersionedAsset(asset_id=tid, asset_kind="data_template", tenant_id=t.tenant_id,
                               name=t.name, version=t.version, status=t.status, spec=out.model_dump(mode="json"))
    session.add(asset)
    await _save_version(session, asset)
    return out


@router.get("/data-templates/{template_id}", response_model=DataTemplate)
async def get_data_template(template_id: str, principal: Principal = Depends(current_principal),
                            session: AsyncSession = Depends(get_session)) -> DataTemplate:
    a = await _load_asset(session, template_id, "data_template", principal)
    return DataTemplate(**a.spec)


@router.put("/data-templates/{template_id}", response_model=DataTemplate)
async def update_data_template(template_id: str, t: DataTemplate,
                               if_match: str | None = Header(default=None),
                               principal: Principal = Depends(current_principal),
                               session: AsyncSession = Depends(get_session)) -> DataTemplate:
    a = await _load_asset(session, template_id, "data_template", principal)
    if if_match and if_match != a.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "version conflict")
    # new version if schema-relevant fields changed
    schema_changed = any(a.spec.get(k) != getattr(t, k) for k in ("input_schema", "output_schema", "required_variables"))
    new_version = str(int(float(a.version)) + 1) if schema_changed else a.version
    out = t.model_copy(update={"template_id": template_id, "version": new_version})
    a.spec = out.model_dump(mode="json"); a.version = new_version; a.status = t.status; a.name = t.name
    if schema_changed:
        await _save_version(session, a)
    return out


@router.get("/data-templates/{template_id}/versions")
async def data_template_versions(template_id: str, principal: Principal = Depends(current_principal),
                                 session: AsyncSession = Depends(get_session)) -> dict:
    await _load_asset(session, template_id, "data_template", principal)
    rows = (await session.execute(
        select(orm.AssetVersion.version, orm.AssetVersion.created_at).where(orm.AssetVersion.asset_id == template_id)
    )).all()
    return {"template_id": template_id, "versions": [{"version": v, "created_at": c.isoformat()} for v, c in rows]}


@router.post("/data-templates/{template_id}/validate-input")
async def validate_input(template_id: str, body: dict, principal: Principal = Depends(current_principal),
                         session: AsyncSession = Depends(get_session)) -> dict:
    a = await _load_asset(session, template_id, "data_template", principal)
    errors = validate_against_schema(body.get("data", {}), a.spec.get("input_schema", {}))
    return {"valid": not errors, "errors": [e.model_dump() for e in errors]}


@router.post("/data-templates/{template_id}/validate-output")
async def validate_output(template_id: str, body: dict, principal: Principal = Depends(current_principal),
                          session: AsyncSession = Depends(get_session)) -> dict:
    a = await _load_asset(session, template_id, "data_template", principal)
    errors = validate_against_schema(body.get("output", {}), a.spec.get("output_schema", {}))
    return {"valid": not errors, "errors": [e.model_dump() for e in errors]}


# ---- prompt templates -----------------------------------------------------
@router.post("/prompt-templates", response_model=PromptTemplate)
async def create_prompt_template(t: PromptTemplate, principal: Principal = Depends(current_principal),
                                 session: AsyncSession = Depends(get_session)) -> PromptTemplate:
    enforce_tenant(principal, t.tenant_id)
    pid = t.prompt_template_id or common.new_id("pt")
    out = t.model_copy(update={"prompt_template_id": pid})
    asset = orm.VersionedAsset(asset_id=pid, asset_kind="prompt_template", tenant_id=t.tenant_id,
                               name=t.name, version=t.version, status=t.status, spec=out.model_dump(mode="json"))
    session.add(asset)
    await _save_version(session, asset)
    return out


@router.get("/prompt-templates/{template_id}", response_model=PromptTemplate)
async def get_prompt_template(template_id: str, principal: Principal = Depends(current_principal),
                              session: AsyncSession = Depends(get_session)) -> PromptTemplate:
    a = await _load_asset(session, template_id, "prompt_template", principal)
    return PromptTemplate(**a.spec)


@router.put("/prompt-templates/{template_id}", response_model=PromptTemplate)
async def update_prompt_template(template_id: str, t: PromptTemplate,
                                 if_match: str | None = Header(default=None),
                                 principal: Principal = Depends(current_principal),
                                 session: AsyncSession = Depends(get_session)) -> PromptTemplate:
    a = await _load_asset(session, template_id, "prompt_template", principal)
    if if_match and if_match != a.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "version conflict")
    changed = any(a.spec.get(k) != getattr(t, k) for k in
                  ("prompt_body", "system_prompt", "input_schema", "output_schema", "variable_names",
                   "validation_schema_ref", "repair_template_id"))
    new_version = str(int(float(a.version)) + 1) if changed else a.version
    out = t.model_copy(update={"prompt_template_id": template_id, "version": new_version})
    a.spec = out.model_dump(mode="json"); a.version = new_version; a.status = t.status; a.name = t.name
    if changed:
        await _save_version(session, a)
    return out


@router.get("/prompt-templates/{template_id}/versions")
async def prompt_template_versions(template_id: str, principal: Principal = Depends(current_principal),
                                   session: AsyncSession = Depends(get_session)) -> dict:
    await _load_asset(session, template_id, "prompt_template", principal)
    rows = (await session.execute(
        select(orm.AssetVersion.version, orm.AssetVersion.created_at).where(orm.AssetVersion.asset_id == template_id)
    )).all()
    return {"template_id": template_id, "versions": [{"version": v, "created_at": c.isoformat()} for v, c in rows]}


@router.post("/prompt-templates/{template_id}/render", response_model=PromptTemplateRenderResponse)
async def render_prompt_template(template_id: str, req: PromptTemplateRenderRequest,
                                 principal: Principal = Depends(current_principal),
                                 session: AsyncSession = Depends(get_session)) -> PromptTemplateRenderResponse:
    a = await _load_asset(session, template_id, "prompt_template", principal)
    body = a.spec.get("prompt_body", "")
    names = a.spec.get("variable_names", [])
    missing = [n for n in names if n not in (req.data or {})]
    if req.strict_missing_variable_check and missing:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"missing variables: {missing}")
    used = {}
    for n in names:
        if n in (req.data or {}):
            body = body.replace("{{" + n + "}}", str(req.data[n]))
            used[n] = req.data[n]
    return PromptTemplateRenderResponse(rendered_prompt=body, missing_variables=missing, used_variables=used)


@router.post("/prompt-templates/{template_id}/archive")
async def archive_prompt_template(template_id: str, principal: Principal = Depends(current_principal),
                                  session: AsyncSession = Depends(get_session)) -> dict:
    a = await _load_asset(session, template_id, "prompt_template", principal)
    a.status = "deprecated"; a.spec = {**a.spec, "status": "deprecated"}
    return {"prompt_template_id": template_id, "status": "deprecated"}


@router.post("/prompt-templates/{template_id}/clone", response_model=PromptTemplate)
async def clone_prompt_template(template_id: str, principal: Principal = Depends(current_principal),
                                session: AsyncSession = Depends(get_session)) -> PromptTemplate:
    a = await _load_asset(session, template_id, "prompt_template", principal)
    new_id = common.new_id("pt")
    spec = {**a.spec, "prompt_template_id": new_id, "version": "1", "status": "draft",
            "name": a.spec.get("name", "") + " (clone)"}
    clone = orm.VersionedAsset(asset_id=new_id, asset_kind="prompt_template", tenant_id=a.tenant_id,
                               name=spec["name"], version="1", status="draft", spec=spec)
    session.add(clone)
    await _save_version(session, clone)
    return PromptTemplate(**spec)
