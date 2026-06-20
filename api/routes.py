"""API routes. Handlers are thin: validate -> call service -> return schema."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import authenticate
from api.dependencies import bind_request_ids
from api.models import (
    AnalyzeRequest,
    HealthResponse,
    ReadyResponse,
    RunResponse,
    VersionResponse,
)
from core.version import NAME, SYSTEM_CERTIFIED, VERSION
from schemas.output import ApiResponse
from services import career_service

router = APIRouter()


def _api_response(state: dict) -> ApiResponse:
    resp = state.get("api_response")
    if resp is None:
        raise HTTPException(status_code=500, detail="pipeline produced no api_response")
    return resp


@router.post("/analyze", response_model=ApiResponse, tags=["analysis"])
async def analyze(req: AnalyzeRequest, _ids=Depends(bind_request_ids),
                  _auth=Depends(authenticate)) -> ApiResponse:
    state = career_service.analyze_profile(req.thread_id, req.profile_path, req.limit)
    return _api_response(state)


@router.post("/resume/{thread_id}", response_model=ApiResponse, tags=["analysis"])
async def resume(thread_id: str, _ids=Depends(bind_request_ids),
                 _auth=Depends(authenticate)) -> ApiResponse:
    state = career_service.resume_run(thread_id)
    return _api_response(state)


@router.get("/runs/{thread_id}", response_model=RunResponse, tags=["analysis"])
async def get_run(thread_id: str, _ids=Depends(bind_request_ids)) -> RunResponse:
    state = career_service.get_run(thread_id)
    if state is None:
        return RunResponse(thread_id=thread_id, found=False, result=None)
    return RunResponse(thread_id=thread_id, found=True, result=state.get("api_response"))


@router.get("/health", response_model=HealthResponse, tags=["ops"])
async def health() -> HealthResponse:
    return HealthResponse(**career_service.health_check())


@router.get("/ready", response_model=ReadyResponse, tags=["ops"])
async def ready() -> ReadyResponse:
    return ReadyResponse(**career_service.ready_check())


@router.get("/version", response_model=VersionResponse, tags=["ops"])
async def version() -> VersionResponse:
    return VersionResponse(name=NAME, version=VERSION, system_certified=SYSTEM_CERTIFIED)
