from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .auth import current_user_from_jwt
from .config import Settings, get_settings
from .database import get_db
from .db_models import CopilotProposal, User
from .models import AgentChatRequest, AgentChatResponse, AgentToolEvent
from .observability import get_logger
from .provider_credentials import (
    public_gemini_execution_credential_status,
    resolve_gemini_execution_credential,
)
from .tenancy import primary_org_for_user

router = APIRouter(prefix="/api/agent", tags=["agent"])
CurrentUser = Annotated[User, Depends(current_user_from_jwt)]
DbSession = Annotated[Session, Depends(get_db)]
log = get_logger(__name__)


class _LazyGoogleGenAI:
    def __getattr__(self, name: str) -> Any:
        from google import genai as real_genai

        globals()["genai"] = real_genai
        return getattr(real_genai, name)


class _LazyGoogleGenAITypes:
    def __getattr__(self, name: str) -> Any:
        from google.genai import types as real_types

        globals()["types"] = real_types
        return getattr(real_types, name)


genai: Any = _LazyGoogleGenAI()
types: Any = _LazyGoogleGenAITypes()


def _as_content(role: str, text: str) -> types.Content:
    return types.Content(role=role, parts=[types.Part.from_text(text=text)])


class GeminiMarketplaceAgent:
    def __init__(
        self,
        settings: Settings,
        owner_user_id: int | None = None,
        gemini_api_key: str | None = None,
        gemini_model: str | None = None,
        db: Session | None = None,
        org_id: int | None = None,
    ):
        self.settings = settings
        self.owner_user_id = owner_user_id
        self.db = db
        self.org_id = org_id
        self.gemini_model = (gemini_model or settings.gemini_model).strip() or settings.gemini_model
        if gemini_api_key:
            self.client = genai.Client(api_key=gemini_api_key)
        elif settings.gemini_api_key:
            self.client = genai.Client(api_key=settings.gemini_api_key)
        elif settings.gemini_use_vertexai:
            if not settings.gemini_vertex_project:
                raise HTTPException(
                    status_code=409,
                    detail="GEMINI_VERTEX_PROJECT is required when GEMINI_USE_VERTEXAI=true.",
                )
            self.client = genai.Client(
                vertexai=True,
                project=settings.gemini_vertex_project,
                location=settings.gemini_vertex_location,
            )
        else:
            raise HTTPException(
                status_code=409,
                detail="Configure GEMINI_API_KEY or GEMINI_USE_VERTEXAI=true for agent chat.",
            )

    def _system_instruction(self, authorized_artifact_id: str | None = None) -> str:
        return (
            "You are the TTM autonomous paper-trading copilot. "
            "Keep answers concise and operational. "
            "The product has three trading modes: Mode 1 runs autonomous paper/testnet "
            "analysis and simulated execution with no real money; Mode 2 creates proposals "
            "and waits for human approval; Mode 3 is real autonomous trading and stays locked "
            "unless organization flags, manual approval, hard limits, and audit controls are active. "
            "Never suggest artifact purchases, checkout creation, delivery packets, or paid work-unit flows. "
            "Never claim that a real wallet transaction was submitted. "
            "Use tools only to inspect bot status and the proposal queue. "
            "If a tool returns an error, explain the blocker plainly."
        )

    def bot_status(self) -> dict[str, Any]:
        return {
            "runtime_mode": self.settings.runtime_mode,
            "order_submission_enabled": False,
            "mainnet_order_submission_enabled": False,
            "modes": {
                "paper_auto": "enabled for simulated execution only",
                "human_in_the_loop": "enabled through copilot proposal approval",
                "real_autonomous": "locked by default",
            },
        }

    def list_proposals(self) -> dict[str, Any]:
        if self.db is None or self.org_id is None:
            return {"proposals": [], "message": "No organization context is attached."}
        rows = (
            self.db.query(CopilotProposal)
            .filter(CopilotProposal.org_id == self.org_id)
            .order_by(CopilotProposal.created_at.desc(), CopilotProposal.id.desc())
            .limit(8)
            .all()
        )
        return {
            "proposals": [
                {
                    "id": row.id,
                    "token_address": row.token_address,
                    "chain": row.chain,
                    "symbol": row.symbol,
                    "score": row.score,
                    "mode": row.mode,
                    "status": row.status,
                    "rationale": row.rationale,
                }
                for row in rows
            ],
        }

    def _dispatch_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        authorized_artifact_id: str | None = None,
        authorized_payment_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        tools: dict[str, Callable[..., dict[str, Any]]] = {
            "bot_status": self.bot_status,
            "list_proposals": self.list_proposals,
        }
        tool = tools.get(name)
        if not tool:
            return {
                "error": (
                    f"Unknown or blocked tool: {name}. This SaaS agent only supports "
                    "paper-trading status and proposal inspection."
                )
            }
        try:
            return tool(**args)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            return {"error": detail, "status_code": exc.status_code}
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            log.warning("agent_tool_failed", tool=name, error=str(exc))
            return {"error": str(exc)}

    def respond(self, request: AgentChatRequest) -> AgentChatResponse:
        authorized_artifact_id = request.authorized_artifact_id
        authorized_payment_ids: set[str] = set()
        contents: list[types.Content] = [
            _as_content("user" if turn.role == "user" else "model", turn.text)
            for turn in request.history
        ]
        contents.append(_as_content("user", request.message))

        events: list[AgentToolEvent] = []
        config = types.GenerateContentConfig(
            system_instruction=self._system_instruction(authorized_artifact_id),
            tools=[
                self.bot_status,
                self.list_proposals,
            ],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
        )

        for _ in range(6):
            response = self.client.models.generate_content(
                model=self.gemini_model,
                contents=contents,
                config=config,
            )
            function_calls = response.function_calls or []
            if not function_calls:
                return AgentChatResponse(
                    reply=response.text or "Copilot step completed.",
                    events=events,
                    orders=[],
                    settlement_auth_mode=self.settings.settlement_auth_mode,
                    autonomous_payments_enabled=self.settings.autonomous_payments_enabled,
                )

            model_content = response.candidates[0].content if response.candidates else None
            if model_content:
                contents.append(model_content)

            function_response_parts: list[types.Part] = []
            for call in function_calls:
                name = call.name or "unknown_tool"
                args = dict(call.args or {})
                result = self._dispatch_tool(
                    name,
                    args,
                    authorized_artifact_id=authorized_artifact_id,
                    authorized_payment_ids=authorized_payment_ids,
                )
                events.append(
                    AgentToolEvent(
                        id=call.id or None,
                        name=name,
                        args=args,
                        response=result,
                    )
                )
                function_response_parts.append(
                    types.Part.from_function_response(name=name, response=result)
                )

            contents.append(types.Content(role="tool", parts=function_response_parts))

        raise HTTPException(status_code=502, detail="Agent exceeded the tool-call budget.")


@router.post("/chat", response_model=AgentChatResponse)
def chat_with_agent(request: AgentChatRequest, user: CurrentUser, db: DbSession):
    settings = get_settings()
    org = primary_org_for_user(user, db)
    execution_credential = resolve_gemini_execution_credential(db, org.id, settings)
    credential_status = public_gemini_execution_credential_status(execution_credential)
    db.commit()
    agent = GeminiMarketplaceAgent(
        settings,
        owner_user_id=user.id,
        gemini_api_key=execution_credential.api_key,
        gemini_model=execution_credential.model,
        db=db,
        org_id=org.id,
    )
    response = agent.respond(request)
    response.gemini = credential_status
    return response


@router.get("/status")
def agent_status(user: CurrentUser, db: DbSession) -> dict[str, object]:
    settings = get_settings()
    org = primary_org_for_user(user, db)
    execution_credential = resolve_gemini_execution_credential(db, org.id, settings)
    db.commit()
    return {
        "org_id": org.id,
        "gemini": public_gemini_execution_credential_status(execution_credential),
    }
