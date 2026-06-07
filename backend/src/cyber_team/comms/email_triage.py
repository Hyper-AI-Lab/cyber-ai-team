"""Inbound email triage and approval-backed reply drafting."""

from __future__ import annotations

import logging
from typing import Any

from cyber_team.clock import utc_now
from cyber_team.config import settings

logger = logging.getLogger(__name__)


class EmailTriageService:
    """Classify inbound email and prepare owner-approved replies."""

    def __init__(
        self,
        inbound_email_service,
        tool_registry,
        llm_gateway=None,
        audit_service=None,
        metrics_service=None,
    ) -> None:
        self._inbound_email = inbound_email_service
        self._tool_registry = tool_registry
        self._llm = llm_gateway
        self._audit = audit_service
        self._metrics = metrics_service

    async def triage_and_prepare_reply(
        self,
        message_id: str,
        requester: str = "owner",
    ) -> dict[str, Any] | None:
        message = await self._inbound_email.get_message(message_id)
        if not message:
            return None

        triage = await self._classify(message)
        draft = await self._draft_reply(message, triage)
        approval = await self._request_reply_approval(
            message=message,
            draft=draft,
            requester=requester,
        )

        metadata = {
            "triage": {
                **triage,
                "draft_reply": draft,
                "approval": approval,
                "processed_at": utc_now().isoformat() + "+00:00",
                "processor": "email_triage_v1",
            }
        }
        updated = await self._inbound_email.update_metadata(
            message_id,
            metadata,
            status="triaged",
        )
        if self._audit:
            await self._audit.record(
                event_type="inbound_email.triaged",
                actor=requester,
                actor_type="user",
                resource_type="inbound_email_message",
                resource_id=message_id,
                action="triage_and_prepare_reply",
                metadata={
                    "category": triage["category"],
                    "priority": triage["priority"],
                    "owner_role": triage["owner_role"],
                    "approval_id": approval.get("approval_id"),
                    "approval_state": approval.get("state"),
                },
            )
        if self._metrics:
            self._metrics.increment(
                "cyberteam_inbound_email_triage_total",
                {
                    "category": triage["category"],
                    "priority": triage["priority"],
                    "approval_state": approval.get("state", "unknown"),
                },
            )
        return {
            "message": updated,
            "triage": triage,
            "draft_reply": draft,
            "approval": approval,
        }

    async def _classify(self, message: dict[str, Any]) -> dict[str, Any]:
        heuristic = self._heuristic_classify(message)
        if not self._llm or not settings.mistral_api_key:
            return heuristic

        try:
            llm_result = await self._llm.invoke_json(
                system_prompt=(
                    "You are Cyber-Team's inbound business email triage operator. "
                    "Classify the email for a single-owner company OS. Return JSON with "
                    "category, priority, owner_role, summary, recommended_next_step, and signals. "
                    "Use conservative priorities and do not invent facts."
                ),
                user_message=self._message_context(message),
                agent_id="email-triage",
            )
        except Exception as exc:  # noqa: BLE001 - LLM fallback must preserve workflow.
            logger.warning("LLM email classification failed: %s", exc)
            return {
                **heuristic,
                "classification_source": "heuristic_fallback",
                "llm_error": str(exc),
            }

        category = self._clean_choice(
            llm_result.get("category"),
            {"finance", "legal", "support", "sales", "operations", "communications"},
            heuristic["category"],
        )
        priority = self._clean_choice(
            llm_result.get("priority"),
            {"low", "medium", "high"},
            heuristic["priority"],
        )
        owner_role = self._clean_text(llm_result.get("owner_role"), heuristic["owner_role"])
        summary = self._clean_text(llm_result.get("summary"), heuristic["summary"])
        next_step = self._clean_text(
            llm_result.get("recommended_next_step"),
            heuristic["recommended_next_step"],
        )
        signals = llm_result.get("signals")
        if not isinstance(signals, list):
            signals = heuristic["signals"]
        return {
            "category": category,
            "priority": priority,
            "owner_role": owner_role,
            "summary": summary,
            "recommended_next_step": next_step,
            "signals": [
                self._clean_text(item, "")
                for item in signals
                if self._clean_text(item, "")
            ][:8],
            "classification_source": "llm",
        }

    async def _draft_reply(self, message: dict[str, Any], triage: dict[str, Any]) -> dict[str, Any]:
        subject = self._reply_subject(message.get("subject") or "")
        fallback_body = self._fallback_reply_body(message, triage)
        body = fallback_body
        source = "template"

        if self._llm and settings.mistral_api_key:
            try:
                body = await self._llm.invoke(
                    system_prompt=(
                        "Draft a concise, professional email reply for the company owner "
                        "to approve. "
                        "Do not claim that work has already been completed. "
                        "Acknowledge receipt, state the next review step, and ask for any "
                        "missing context "
                        "only when genuinely useful."
                    ),
                    user_message=(
                        f"Triage:\n{triage}\n\nInbound email:\n{self._message_context(message)}"
                    ),
                    agent_id="email-reply-drafter",
                    temperature=0.4,
                    max_tokens=900,
                )
                body = body.strip() or fallback_body
                source = "llm"
            except Exception as exc:  # noqa: BLE001 - draft fallback must preserve workflow.
                logger.warning("LLM email reply draft failed: %s", exc)
                body = fallback_body
                source = "template_fallback"

        return {
            "to_address": message.get("from_address") or "",
            "subject": subject,
            "body": body,
            "source": source,
        }

    async def _request_reply_approval(
        self,
        message: dict[str, Any],
        draft: dict[str, Any],
        requester: str,
    ) -> dict[str, Any]:
        if not draft["to_address"]:
            return {
                "state": "blocked",
                "approval_id": None,
                "reason": "Inbound message has no sender address, so no reply can be addressed.",
            }

        result = await self._tool_registry.execute(
            "send_email",
            {
                "to_address": draft["to_address"],
                "subject": draft["subject"],
                "body": draft["body"],
                "cc": [],
                "idempotency_key": f"inbound-reply:{message['id']}:v1",
                "_actor": requester,
                "_actor_type": "user",
                "_source_type": "inbound_email_reply",
                "_conversation_id": message["id"],
            },
        )
        output = result.output if isinstance(result.output, dict) else {}
        if output.get("approval_required"):
            return {
                "state": "approval_required",
                "approval_id": output.get("approval_id"),
                "risk_level": output.get("risk_level"),
                "reason": output.get("reason"),
                "target": output.get("target"),
                "payload_summary": output.get("payload_summary"),
                "replay_instructions": output.get("replay_instructions"),
            }
        if output.get("blocked"):
            return {
                "state": "blocked",
                "approval_id": None,
                "reason": output.get("readiness_reason") or result.error,
                "readiness": output,
            }
        return {
            "state": "failed" if not result.success else "sent",
            "approval_id": None,
            "reason": result.error,
            "result": output or result.output,
        }

    @staticmethod
    def _heuristic_classify(message: dict[str, Any]) -> dict[str, Any]:
        text = " ".join(
            str(message.get(key) or "")
            for key in ("subject", "snippet", "text_body")
        ).lower()
        rules = [
            (
                "finance",
                "accountant",
                ["invoice", "payment", "billing", "receipt", "refund", "tax", "bank"],
            ),
            (
                "legal",
                "legal advisor",
                ["contract", "legal", "terms", "privacy", "notice", "compliance", "gdpr"],
            ),
            (
                "support",
                "customer support manager",
                ["help", "support", "issue", "bug", "error", "login", "problem"],
            ),
            (
                "sales",
                "sales manager",
                ["pricing", "demo", "proposal", "quote", "lead", "partnership", "buy"],
            ),
            (
                "operations",
                "operations manager",
                ["schedule", "meeting", "delivery", "deadline", "status", "task"],
            ),
        ]
        category = "communications"
        owner_role = "communications manager"
        signals: list[str] = []
        for candidate, role, keywords in rules:
            hits = [keyword for keyword in keywords if keyword in text]
            if hits:
                category = candidate
                owner_role = role
                signals = hits[:5]
                break

        priority = "low"
        priority_terms = {
            "high": ["urgent", "asap", "immediately", "critical", "complaint", "legal notice"],
            "medium": ["soon", "deadline", "follow up", "payment", "contract", "issue"],
        }
        for candidate, keywords in priority_terms.items():
            if any(keyword in text for keyword in keywords):
                priority = candidate
                break

        subject = message.get("subject") or "(no subject)"
        sender = message.get("from_address") or "unknown sender"
        snippet = message.get("snippet") or "No readable body was captured."
        return {
            "category": category,
            "priority": priority,
            "owner_role": owner_role,
            "summary": f"{sender} sent {subject}: {snippet[:220]}",
            "recommended_next_step": (
                "Review the message and approve, edit, or reject the prepared reply."
            ),
            "signals": signals or ["general inbound email"],
            "classification_source": "heuristic",
        }

    @staticmethod
    def _fallback_reply_body(message: dict[str, Any], triage: dict[str, Any]) -> str:
        sender = message.get("from_address") or "there"
        greeting = "Hello,"
        if "@" in sender:
            local_part = sender.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
            if local_part and not local_part.startswith("no"):
                greeting = f"Hello {local_part.title()},"
        return (
            f"{greeting}\n\n"
            "Thank you for your message. We received it and will review it carefully.\n\n"
            f"Based on the current triage, this looks like a {triage['priority']} priority "
            f"{triage['category']} request. The next step is for the appropriate owner-side "
            "specialist to review the details and follow up with a specific response.\n\n"
            "Best,\n"
            "Cyber-Team"
        )

    @staticmethod
    def _reply_subject(subject: str) -> str:
        cleaned = subject.strip() or "Your message"
        if cleaned.lower().startswith("re:"):
            return cleaned
        return f"Re: {cleaned}"

    @staticmethod
    def _message_context(message: dict[str, Any]) -> str:
        body = message.get("text_body") or message.get("snippet") or ""
        return (
            f"From: {message.get('from_address') or 'unknown'}\n"
            f"To: {', '.join(message.get('to_addresses') or [])}\n"
            f"Subject: {message.get('subject') or '(no subject)'}\n"
            f"Body:\n{body[:6000]}"
        )

    @staticmethod
    def _clean_choice(value: Any, allowed: set[str], fallback: str) -> str:
        if not isinstance(value, str):
            return fallback
        normalized = value.strip().lower()
        return normalized if normalized in allowed else fallback

    @staticmethod
    def _clean_text(value: Any, fallback: str) -> str:
        if not isinstance(value, str):
            return fallback
        cleaned = " ".join(value.strip().split())
        return cleaned or fallback
