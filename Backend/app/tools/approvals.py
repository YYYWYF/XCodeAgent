from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from threading import Lock
from typing import Any, Dict, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field


APPROVAL_TTL_SECONDS = 5 * 60


class ApprovalGrant(BaseModel):
    id: str = Field(min_length=1)
    token: str = Field(min_length=1)


@dataclass
class PendingApproval:
    id: str
    token: str
    tool: str
    operation_key: str
    title: str
    description: str
    subject: str
    risk: Dict[str, Any]
    details: Optional[str]
    created_at: datetime
    expires_at: datetime
    status: str = "pending"
    consumed: bool = False


@dataclass(frozen=True)
class ApprovedOperation:
    tool: str
    operation_key: str
    approved_at: datetime


class ApprovalStore:
    def __init__(self) -> None:
        self._approvals: Dict[str, PendingApproval] = {}
        self._approved_operations: Dict[str, ApprovedOperation] = {}
        self._lock = Lock()

    def request(
        self,
        *,
        tool: str,
        operation_key: str,
        title: str,
        description: str,
        subject: str,
        risk: Dict[str, Any],
        details: Optional[str] = None,
    ) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        self._prune(now)
        approval = PendingApproval(
            id=token_urlsafe(12),
            token=token_urlsafe(24),
            tool=tool,
            operation_key=operation_key,
            title=title,
            description=description,
            subject=subject,
            risk=risk,
            details=details,
            created_at=now,
            expires_at=now + timedelta(seconds=APPROVAL_TTL_SECONDS),
        )
        with self._lock:
            self._approvals[approval.id] = approval
        return self._public_payload(approval)

    def approve(self, approval_id: str, *, scope: str = "once") -> Dict[str, Any]:
        approval = self._get(approval_id)
        if approval.status != "pending":
            self._fail(409, f"Approval is already {approval.status}.")
        approval.status = "approved"
        normalized_scope = "operation" if scope == "operation" else "once"
        if normalized_scope == "operation":
            with self._lock:
                self._approved_operations[self._operation_rule_key(approval.tool, approval.operation_key)] = (
                    ApprovedOperation(
                        tool=approval.tool,
                        operation_key=approval.operation_key,
                        approved_at=datetime.now(timezone.utc),
                    )
                )
        return {
            "id": approval.id,
            "tool": approval.tool,
            "status": approval.status,
            "scope": normalized_scope,
            "token": approval.token,
            "expires_at": approval.expires_at.isoformat(),
        }

    def reject(self, approval_id: str, *, reason: Optional[str] = None) -> Dict[str, Any]:
        approval = self._get(approval_id)
        if approval.status != "pending":
            self._fail(409, f"Approval is already {approval.status}.")
        approval.status = "rejected"
        return {
            "id": approval.id,
            "tool": approval.tool,
            "status": approval.status,
            "reason": reason,
            "expires_at": approval.expires_at.isoformat(),
        }

    def consume(self, *, tool: str, operation_key: str, grant: ApprovalGrant) -> None:
        approval = self._get(grant.id)
        if approval.tool != tool or approval.operation_key != operation_key:
            self._fail(403, "Approval does not match this operation.")
        if approval.status != "approved":
            self._fail(403, "Approval has not been granted.")
        if approval.consumed:
            self._fail(403, "Approval has already been used.")
        if approval.token != grant.token:
            self._fail(403, "Approval token is invalid.")
        approval.consumed = True

    def consume_approved_once(self, *, tool: str, operation_key: str) -> bool:
        now = datetime.now(timezone.utc)
        self._prune(now)
        with self._lock:
            approvals = [
                approval
                for approval in self._approvals.values()
                if (
                    approval.tool == tool
                    and approval.operation_key == operation_key
                    and approval.status == "approved"
                    and not approval.consumed
                    and approval.expires_at > now
                )
            ]
            if not approvals:
                return False
            approvals.sort(key=lambda approval: approval.created_at)
            approvals[0].consumed = True
            return True

    def is_operation_approved(self, *, tool: str, operation_key: str) -> bool:
        with self._lock:
            return self._operation_rule_key(tool, operation_key) in self._approved_operations

    def _get(self, approval_id: str) -> PendingApproval:
        now = datetime.now(timezone.utc)
        self._prune(now)
        with self._lock:
            approval = self._approvals.get(approval_id)
        if approval is None:
            self._fail(404, "Approval request was not found or has expired.")
        if approval.expires_at <= now:
            self._fail(410, "Approval request has expired.")
        return approval

    def _prune(self, now: datetime) -> None:
        with self._lock:
            expired_ids = [
                approval_id
                for approval_id, approval in self._approvals.items()
                if approval.expires_at <= now or approval.consumed
            ]
            for approval_id in expired_ids:
                self._approvals.pop(approval_id, None)

    @staticmethod
    def _operation_rule_key(tool: str, operation_key: str) -> str:
        return f"{tool}:{operation_key}"

    @staticmethod
    def _public_payload(approval: PendingApproval) -> Dict[str, Any]:
        return {
            "id": approval.id,
            "tool": approval.tool,
            "title": approval.title,
            "description": approval.description,
            "subject": approval.subject,
            "risk": approval.risk,
            "details": approval.details,
            "status": approval.status,
            "created_at": approval.created_at.isoformat(),
            "expires_at": approval.expires_at.isoformat(),
        }

    @staticmethod
    def _fail(status_code: int, message: str) -> None:
        raise HTTPException(status_code=status_code, detail=message)


def operation_fingerprint(tool: str, payload: Dict[str, Any]) -> str:
    raw = json.dumps({"tool": tool, "payload": payload}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


approval_store = ApprovalStore()
