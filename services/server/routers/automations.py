"""Automations router — scheduled task CRUD + manual run endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from typing import Any

router = APIRouter(prefix="/v1/automations", tags=["automations"])


def _manager(request: Request):
    return request.app.state.manager


@router.get("")
def automations_list(request: Request) -> dict[str, Any]:
    return _manager(request).list_automations()


@router.post("")
async def automations_create(request: Request, body: dict) -> dict[str, Any]:
    return _manager(request).create_automation(body or {})


@router.get("/{task_id}")
def automation_get(task_id: str, request: Request) -> dict[str, Any]:
    return _manager(request).get_automation(task_id)


@router.patch("/{task_id}")
async def automation_update(task_id: str, request: Request, body: dict) -> dict[str, Any]:
    return _manager(request).update_automation(task_id, body or {})


@router.delete("/{task_id}")
def automation_delete(task_id: str, request: Request) -> dict[str, Any]:
    return _manager(request).delete_automation(task_id)


@router.post("/{task_id}/seen")
def automations_seen(task_id: str, request: Request) -> dict[str, Any]:
    return _manager(request).mark_automation_seen(task_id)


@router.post("/{task_id}/run")
def automation_run(task_id: str, request: Request) -> dict[str, Any]:
    return _manager(request).prepare_manual_run(task_id)


@router.post("/{task_id}/runs/{run_id}/finalize")
def automation_run_finalize(task_id: str, run_id: str, request: Request) -> dict[str, Any]:
    return _manager(request).finalize_manual_run(task_id, run_id)