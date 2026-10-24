"""Coordinator service (spec section 4.1) — buyer and primary user-facing agent.

Owns the authoritative event plan, the approval workflow and the budget.
Business rules are deterministic; the optional ASI1 LLM only extracts intent,
explains computed facts (numeric-guarded) and is never allowed to approve,
spend, reserve or send.

All methods return :class:`TurnResult`; the uAgents handler layer sends the
messages. This module has NO uAgents import, so the entire coordination logic
is testable without the network.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Optional

from shared.cards import (
    build_package_carousel,
    build_review_card,
)
from shared.enums import (
    ActionType,
    ApprovalStatus,
    EventStatus,
    ExternalEffect,
    IneligibilityReason,
    InvitationStatus,
    PaymentMode,
    PaymentStatus,
    ReservationStatus,
)
from shared.errors import (
    BudgetExceededError,
    CapacityExceededError,
    DuplicateOperationError,
    EventPilotError,
    ExpiredError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from shared.inventory import ATESTFET, DEMO_CATALOG
from shared.logging_config import get_logger, set_correlation_id
from shared.messages import (
    InvitationDraftRequest,
    InvitationDraftResponse,
    PaymentStatusRequest,
    PaymentStatusResponse,
    QuoteRequest,
    QuoteResponse,
    ReservationRequest,
    ReservationResponse,
    ReservationSupersedeRequest,
    ReservationSupersedeResponse,
    RsvpCountRequest,
    RsvpCountResponse,
    RsvpSubmitRequest,
    RsvpSubmitResponse,
    SendInvitationRequest,
    SendInvitationResponse,
)
from shared.models import EligibilityCheck, OptionView
from shared.runtime import Outbound, TurnResult
from shared.time_utils import (
    elapsed_ms,
    format_local,
    iso,
    is_expired,
    parse_iso,
    utc_now,
)
from shared.validation import (
    build_allowed_numbers,
    canonical_hash,
    format_fet,
    format_inr,
    normalize_equipment,
    parse_attendees,
    parse_budget_paise,
    parse_event_date,
    validate_paise,
)
from storage.database import new_id

logger = get_logger("coordinator")

GREETING = (
    "WorkshopPilot here. I turn a workshop brief into an approved, budget-checked plan: "
    "quotes from a supplier agent, capacity/budget checks in deterministic code, one exact "
    "approval per action, labelled payment evidence, invitation approval, RSVP tracking and "
    "re-planning when attendance changes.\n"
    "NOTE: supplier inventory, prices and reservations are DEMO DATA; no real venue is booked. "
    "Testnet deposits are not INR.\n"
    "Try: 'Organise a robotics workshop for 60 people in Jaipur on 15 November 2026. Keep "
    "expenses under 20,000 rupees.'\n"
    "Commands: status | checklist | timeline | help | reset my demo"
)

HELP_TEXT = (
    "What I can do:\n"
    "- Plan an event from a free-text brief (I will ask for anything missing).\n"
    "- Get supplier quotes and show which packages fit capacity and budget.\n"
    "- Approve ONE exact package with a code (e.g. 'approve A-7F3KX').\n"
    "- Reserve it and settle the deposit (simulated by default, labelled as such).\n"
    "- Draft invitations and require approval before any send.\n"
    "- Track RSVPs (link or chat code) and report counts.\n"
    "- Re-plan when attendance changes, inside the original budget.\n"
    "Commands: status, checklist, timeline, help, reset my demo."
)

RE_REPLAN = re.compile(
    r"attendance\s+(?:is\s+)?(?:now\s+|changed\s+to\s+|increased\s+to\s+|updated\s+to\s+)?(\d{1,5})",
    re.IGNORECASE,
)
RE_APPROVE = re.compile(r"^\s*approve(?:\s+(?:code\s+)?([A-Z0-9\-]{2,16}))?\s*\.?\s*$", re.IGNORECASE)
RE_REJECT = re.compile(r"^\s*reject(?:\s+(?:code\s+)?([A-Z0-9\-]{2,16}))?\s*\.?\s*$", re.IGNORECASE)
RE_OPTION = re.compile(r"^\s*(?:option\s*|package\s*|#)?(\d{1,2})\s*$", re.IGNORECASE)
RE_SELECT = re.compile(
    r"\b(?:choose|select|pick|go\s+with|book|take|i\s+want)\b(.{0,60})", re.IGNORECASE
)
RE_RSVP = re.compile(r"^\s*rsvp\s+([A-Z0-9]{6})\s*(yes|no)?\s*\.?\s*$", re.IGNORECASE)
RE_APPROVAL_CODE = re.compile(r"\b(A-[A-Z2-9]{5})\b")
RE_APPROVE_WORDS = re.compile(
    r"\b(approve|approved|approval|confirm|confirmed|tap|tapped|click|clicked|yes)\b",
    re.IGNORECASE,
)
RE_REJECT_WORDS = re.compile(
    r"\b(reject|rejected|cancel|cancelled|canceled|decline|declined|no)\b", re.IGNORECASE
)
RE_CITY = re.compile(r"\bin\s+([A-Za-z][A-Za-z ]{1,30}?)(?:\s+on\b|\s+for\b|,|$|\.)")
RE_TITLE = re.compile(
    r"(?:organise|organize|host|plan|set\s*up|arrange)\s+(?:a|an|the)?\s*"
    r"([a-z0-9][a-z0-9 ]{2,48}?(?:workshop|hackathon|bootcamp|seminar|meetup|event|session))",
    re.IGNORECASE,
)
RE_EQUIPMENT = re.compile(
    r"(?:with|needing|requiring|equipment(?:\s+requirements)?[:\s]+)\s+"
    r"(?![a-z0-9 ,/_-]*(?:budget|rupee|rupees|inr|rs\b|\u20b9))"
    r"([a-z0-9][a-z0-9 ,/_-]{2,80})",
    re.IGNORECASE,
)


class CoordinatorService:
    def __init__(
        self,
        repo,
        approval_service,
        quote_service,
        replanning_service,
        checklist_service,
        audit_service,
        settings,
        *,
        chain=None,
        llm=None,
        now_fn: Callable[[], datetime] = utc_now,
        agent_reply_timeout_seconds: float | None = None,
    ):
        self.repo = repo
        self.approvals = approval_service
        self.quotes = quote_service
        self.replanning = replanning_service
        self.checklists = checklist_service
        self.audit = audit_service
        self.settings = settings
        self.chain = chain
        self.llm = llm
        self.now_fn = now_fn
        self.timeout_seconds = (
            agent_reply_timeout_seconds
            if agent_reply_timeout_seconds is not None
            else settings.agent_reply_timeout_seconds
        )
        self._rate_hits: dict[str, list[float]] = {}

    # ==================================================================
    # Entry points
    # ==================================================================

    def handle_user_text(self, sender: str, text: str) -> TurnResult:
        set_correlation_id(new_id("COR"))
        result = TurnResult()
        text = (text or "").strip()
        try:
            self._rate_limit(sender)
        except RateLimitError:
            result.add("You are sending messages very quickly. Please wait a moment and try again.")
            return result

        notices = self._sweep_timeouts()
        for notice in notices:
            result.add(notice)

        # ASI:One delivers card clicks as a JSON object in TextContent (direct
        # @mention) or as prose (planner-mediated). Handle the JSON shape first.
        card_selection = _card_selection_from_text(text)
        if card_selection is not None:
            return self.handle_card_selection(sender, card_selection)

        lowered = text.lower()
        normalized = lowered.rstrip(" ?!.").strip()
        if not text or normalized in (
            "hi",
            "hello",
            "hey",
            "start",
            "what can you do",
            "what do you do",
            "who are you",
        ):
            self._greeting(result)
            return result
        if "reset" in lowered and "demo" in lowered:
            self._reset(sender, result)
            return result
        if lowered in ("help", "?", "commands") or "help me" in lowered:
            result.add(HELP_TEXT)
            return result
        if lowered in ("status", "summary", "where are we", "report"):
            self._status(sender, result)
            return result
        if "checklist" in lowered:
            self._checklist(sender, result)
            return result
        if "timeline" in lowered or lowered in ("audit", "audit trail", "history"):
            self._timeline(sender, result)
            return result
        if "how many" in lowered or "rsvp count" in lowered or "registrations" in lowered:
            self._query_rsvp_count(sender, result)
            return result

        rsvp_match = RE_RSVP.match(text)
        if rsvp_match:
            self._submit_rsvp(sender, rsvp_match.group(1).upper(), rsvp_match.group(2) or "yes", result)
            return result

        replan_match = RE_REPLAN.search(text)
        if replan_match:
            self._handle_replan(sender, int(replan_match.group(1)), result)
            return result

        approve_match = RE_APPROVE.match(text)
        if approve_match:
            self._handle_approval_command(sender, approve_match.group(1), reject=False, result=result)
            return result
        reject_match = RE_REJECT.match(text)
        if reject_match:
            self._handle_approval_command(sender, reject_match.group(1), reject=True, result=result)
            return result

        # Planner-mediated card clicks arrive as prose that names the approval
        # code (the card button label carries it). Only act when the text also
        # contains an approval/rejection verb, so a code mentioned casually is
        # not treated as consent.
        prose_code = RE_APPROVAL_CODE.search(text)
        if prose_code:
            if RE_REJECT_WORDS.search(lowered):
                self._handle_approval_command(
                    sender, prose_code.group(1), reject=True, result=result
                )
                return result
            if RE_APPROVE_WORDS.search(lowered):
                self._handle_approval_command(
                    sender, prose_code.group(1), reject=False, result=result
                )
                return result

        selection = self._parse_selection(sender, text)
        if selection is not None:
            self._handle_selection(sender, result=result, **selection)
            return result

        self._ingest_brief(sender, text, result)
        return result

    def handle_card_response(self, sender: str, metadata: dict[str, str]) -> TurnResult:
        """Parse an inbound Interactive Card response block."""
        set_correlation_id(new_id("COR"))
        result = TurnResult()
        import json

        from shared.cards import META_CANCELLED, META_SELECTION, META_TEXT

        if metadata.get(META_CANCELLED) in ("true", "True", True):
            result.add("Card cancelled — nothing was approved. Tell me what you'd like next.")
            return result
        selection_raw = metadata.get(META_SELECTION)
        if selection_raw:
            try:
                selection = json.loads(selection_raw) if isinstance(selection_raw, str) else selection_raw
            except json.JSONDecodeError:
                result.add("The card selection could not be parsed; please answer in text instead.")
                return result
            if not isinstance(selection, dict):
                result.add("Unexpected card selection format; please answer in text instead.")
                return result
            return self.handle_card_selection(sender, selection)
        text = metadata.get(META_TEXT) or ""
        if text:
            return self.handle_user_text(sender, text)
        result.add("I received an empty card response; please answer in text.")
        return result

    def handle_card_selection(self, sender: str, selection: dict) -> TurnResult:
        """Execute one parsed card selection (CTA or form submission).

        The card is never trusted for money or state: every id is re-validated
        against the stored plan/approval before anything happens.
        """
        set_correlation_id(new_id("COR"))
        result = TurnResult()
        if selection.get("cancelled") in (True, "true", "True"):
            result.add("Card cancelled — nothing was approved. Tell me what you'd like next.")
            return result
        selection = _unwrap_selection(selection)
        if selection is None:
            result.add("That card response could not be read; please answer in text instead.")
            return result
        kind = str(selection.get("type") or selection.get("action") or "").strip().lower()

        if kind == "select_package":
            self._handle_selection(
                sender,
                package_id=selection.get("package_id"),
                quote_id=selection.get("quote_id"),
                result=result,
            )
            return result
        if kind in ("approve_package", "approve_invitation", "approve"):
            code = (
                selection.get("approval_code")
                or selection.get("approval_id")
                or selection.get("code")
            )
            self._handle_approval_command(sender, code, reject=False, result=result)
            return result
        if kind in ("reject", "reject_package", "reject_invitation", "cancel"):
            code = (
                selection.get("approval_code")
                or selection.get("approval_id")
                or selection.get("code")
            )
            self._handle_approval_command(sender, code, reject=True, result=result)
            return result
        if kind in ("submit_event_form", "form_submit", "submit_form"):
            fields = selection.get("fields") or selection.get("form") or selection.get("values")
            if not isinstance(fields, dict):
                fields = {k: v for k, v in selection.items() if k not in ("type", "action")}
            self._ingest_brief(sender, _fields_to_text(fields), result, submitted_via_form=True)
            return result
        if kind == "back_to_news":  # pragma: no cover - foreign card safety net
            result.add("That card belongs to a different agent.")
            return result
        if not kind and any(k in selection for k in _FORM_FIELD_NAMES):
            # A form response whose type/action key was dropped by the client.
            self._ingest_brief(sender, _fields_to_text(selection), result, submitted_via_form=True)
            return result
        result.add("That card action is not recognised; please answer in text.")
        return result

    # ==================================================================
    # Supplier / outreach message handling
    # ==================================================================

    def handle_supplier_message(self, msg: Any) -> TurnResult:
        result = TurnResult()
        if isinstance(msg, QuoteResponse):
            self._on_quote_response(msg, result)
        elif isinstance(msg, ReservationResponse):
            self._on_reservation_response(msg, result)
        elif isinstance(msg, ReservationSupersedeResponse):
            self._on_supersede_response(msg, result)
        elif isinstance(msg, PaymentStatusResponse):
            self._on_payment_response(msg, result)
        else:
            logger.warning("coordinator received unsupported supplier message %s", type(msg).__name__)
        return result

    def handle_outreach_message(self, msg: Any) -> TurnResult:
        result = TurnResult()
        if isinstance(msg, InvitationDraftResponse):
            self._on_draft_response(msg, result)
        elif isinstance(msg, SendInvitationResponse):
            self._on_send_response(msg, result)
        elif isinstance(msg, RsvpCountResponse):
            self._on_count_response(msg, result)
        elif isinstance(msg, RsvpSubmitResponse):
            self._on_rsvp_submit_response(msg, result)
        else:
            logger.warning("coordinator received unsupported outreach message %s", type(msg).__name__)
        return result

    # ==================================================================
    # Brief ingestion
    # ==================================================================

    def _ingest_brief(self, sender: str, text: str, result: TurnResult, *, submitted_via_form: bool = False) -> None:
        brief = self._parse_brief(text)
        if not _has_any(brief):
            # Nothing in the text looks like an event request: respond gracefully
            # instead of interrogating the user about an unintended "brief".
            result.add(
                "I didn't catch an event request in that message. I plan workshops from a brief "
                "like: 'Organise a robotics workshop for 60 people in Jaipur on 15 November 2026 "
                "with a budget under 20,000 rupees.'\n"
                "Commands: status | checklist | timeline | help | reset my demo"
            )
            self.audit.record(
                actor="coordinator",
                action="unrecognised_input",
                entity_type="event",
                result="no_brief_signals",
            )
            return
        if self.llm is not None and brief.missing_mandatory():
            merged = self._llm_fill(text, brief)
            if merged is not None:
                brief = merged
        missing = brief.missing_mandatory()
        if missing:
            from shared.cards import build_form_card

            card = build_form_card(
                title="Complete your event brief",
                submit_label="Submit brief",
                submit_selection={"type": "submit_event_form"},
            )
            pretty = ", ".join(missing)
            result.add(
                f"I still need: {pretty}.\nYou can fill the form card below or just answer in text.\n"
                "Nothing is spent or sent until you approve an exact action.",
                card,
            )
            self.audit.record(
                actor="coordinator",
                action="brief_incomplete",
                entity_type="event",
                result=f"missing:{pretty}",
                metadata={"via": "form" if submitted_via_form else "text"},
            )
            return
        errors = self._validate_brief(brief)
        if errors:
            result.add("The brief has problems I cannot accept:\n- " + "\n- ".join(errors))
            return
        self._create_event_and_request_quotes(sender, brief, result)

    def _parse_brief(self, text: str) -> Any:
        from shared.models import BriefDraft

        attendees = parse_attendees(text)
        budget = parse_budget_paise(text)
        date_iso = parse_event_date(text)
        city_match = RE_CITY.search(text)
        city = city_match.group(1).strip(" .") if city_match else None
        title_match = RE_TITLE.search(text)
        equipment = []
        eq_match = RE_EQUIPMENT.search(text)
        if eq_match:
            equipment = normalize_equipment(
                re.split(r",| and ", eq_match.group(1))
            )
        return BriefDraft(
            title=(title_match.group(1).strip().title() if title_match else None),
            event_type="workshop" if title_match and "workshop" in title_match.group(1).lower() else None,
            date=date_iso,
            city=city,
            expected_attendees=attendees,
            budget_inr_paise=budget,
            equipment_requirements=equipment,
            raw_text=text,
        )

    def _llm_fill(self, text: str, brief):
        """ASI1 fills ONLY the gaps deterministic parsing left; every value is
        re-validated. On any failure we keep the deterministic brief."""
        try:
            raw = self.llm.extract_brief_json(text, iso(self.now_fn()))
        except EventPilotError:
            return None
        if not isinstance(raw, dict):
            return None
        from shared.models import BriefDraft

        attendees = raw.get("expected_attendees")
        budget = raw.get("budget_inr")
        date_raw = raw.get("date")
        city = raw.get("city")
        title = raw.get("title")
        equipment = raw.get("equipment_requirements") or []
        try:
            attendees_val = int(attendees) if attendees is not None and not isinstance(attendees, bool) else None
            if attendees_val is not None and not (1 <= attendees_val <= 10000):
                attendees_val = None
            budget_val = int(budget) * 100 if budget is not None and not isinstance(budget, bool) else None
            if budget_val is not None and budget_val < 0:
                budget_val = None
        except (TypeError, ValueError):
            return None
        date_val = None
        if isinstance(date_raw, str):
            date_val = parse_event_date(date_raw) or (
                date_raw if re.match(r"^\d{4}-\d{2}-\d{2}$", date_raw) else None
            )
        return BriefDraft(
            title=(brief.title or (str(title)[:80] if title else None)),
            event_type=brief.event_type or ("workshop" if title and "workshop" in str(title).lower() else None),
            date=brief.date or date_val,
            city=brief.city or (str(city).strip()[:60] if city else None),
            expected_attendees=brief.expected_attendees or attendees_val,
            budget_inr_paise=brief.budget_inr_paise if brief.budget_inr_paise is not None else budget_val,
            equipment_requirements=brief.equipment_requirements
            or normalize_equipment([str(e) for e in equipment][:6]),
            raw_text=brief.raw_text,
        )

    @staticmethod
    def _validate_brief(brief) -> list[str]:
        errors: list[str] = []
        if brief.expected_attendees is not None and not (1 <= brief.expected_attendees <= 10000):
            errors.append("attendee count must be between 1 and 10000")
        if brief.budget_inr_paise is not None and brief.budget_inr_paise <= 0:
            errors.append("budget must be a positive amount")
        if brief.date is not None:
            try:
                datetime.strptime(brief.date, "%Y-%m-%d")
            except ValueError:
                errors.append("date must be a real calendar date (YYYY-MM-DD)")
        if not errors and brief.date:
            event_dt = datetime.strptime(brief.date, "%Y-%m-%d")
            if event_dt < datetime(2020, 1, 1):
                errors.append("the event date looks too far in the past")
        return errors

    def _create_event_and_request_quotes(self, sender: str, brief, result: TurnResult) -> None:
        event = self.repo.create_event(
            owner_sender=sender,
            title=brief.title or "Workshop",
            event_type=brief.event_type or "workshop",
            date=brief.date,
            timezone=brief.timezone or "Asia/Kolkata",
            city=brief.city,
            expected_attendees=brief.expected_attendees,
            budget_inr_paise=brief.budget_inr_paise,
            equipment_requirements=brief.equipment_requirements,
        )
        self.audit.record(
            actor="organizer",
            action="event_created",
            entity_type="event",
            entity_id=event["event_id"],
            event_id=event["event_id"],
            metadata={
                "attendees": event["expected_attendees"],
                "budget_inr": format_inr(event["budget_inr_paise"]),
                "city": event["city"],
                "date": event["date"],
            },
        )
        self._request_quotes(event, result, note=None)
        result.add(
            f"Event {event['event_id']} created: {event['title']}, {event['expected_attendees']} "
            f"attendees in {event['city']} on {event['date']} (plan v{event['current_plan_version']}, "
            f"budget {format_inr(event['budget_inr_paise'])}).\nRequesting quotes from the supplier agent..."
        )

    def _request_quotes(self, event: dict, result: TurnResult, *, note: Optional[str]) -> None:
        request_id = new_id("REQ")
        self.repo.record_outbound(
            request_id, "supplier", "quote_request", event["event_id"], event["owner_sender"]
        )
        request = QuoteRequest(
            request_id=request_id,
            correlation_id=request_id,
            event_id=event["event_id"],
            plan_version=event["current_plan_version"],
            requested_at=iso(self.now_fn()),
            attendee_count=event["expected_attendees"],
            equipment_requirements=event["equipment_requirements"],
            amendment_of_reservation_id=note,
        )
        result.send("supplier", request)

    # ==================================================================
    # Quote responses -> options
    # ==================================================================

    def _on_quote_response(self, msg: QuoteResponse, result: TurnResult) -> None:
        outbound = self.repo.get_outbound(msg.request_id)
        event = self.repo.get_event_any_owner(msg.event_id)
        if event is None:
            self.audit.record(
                actor="coordinator",
                action="quote_response_rejected",
                entity_type="quote",
                entity_id=msg.request_id,
                result="unknown_event",
            )
            return
        if outbound is None:
            # late/duplicate response for an already-completed request
            self.audit.record(
                actor="coordinator",
                action="quote_response_reconciled",
                entity_type="quote",
                entity_id=msg.request_id,
                result="no_pending_request",
                event_id=event["event_id"],
            )
            return
        if msg.status != "success":
            reason = msg.error.message if msg.error else "supplier rejected the request"
            self.repo.complete_outbound(msg.request_id)
            self.audit.record(
                actor="coordinator",
                action="quote_response_failed",
                entity_type="quote",
                entity_id=msg.request_id,
                result=msg.status,
                event_id=event["event_id"],
                metadata={"reason": reason[:100]},
            )
            result.add(
                f"The supplier agent could not provide quotes: {reason}\n"
                "Nothing happened as a result. You can ask me to try again."
            )
            return
        try:
            normalized = self.quotes.validate_response(
                msg.dict(),
                event=event,
                expected_request_id=msg.request_id,
                attendee_count=event["expected_attendees"],
                plan_version=event["current_plan_version"],
            )
        except EventPilotError as err:
            self.repo.complete_outbound(msg.request_id)
            self.audit.record(
                actor="coordinator",
                action="quote_response_invalid",
                entity_type="quote",
                entity_id=msg.request_id,
                result=err.code,
                event_id=event["event_id"],
                metadata={"reason": str(err)[:100]},
            )
            result.add(
                f"I rejected the supplier response ({err.code}): {err}\n"
                "The stored plan is unchanged; you can ask me to request quotes again."
            )
            return
        self.quotes.persist_quotes(normalized)
        self.repo.complete_outbound(msg.request_id)

        sent_at = parse_iso(outbound["created_at"])
        took_ms = elapsed_ms(sent_at, self.now_fn())
        checks = self._compute_checks(event)
        option_views = self._option_views(checks)
        self.repo.save_plan(
            event["event_id"],
            event["current_plan_version"],
            event["expected_attendees"],
            [view.dict() for view in option_views],
        )
        self.repo.update_event(event["event_id"], event["owner_sender"], status=EventStatus.QUOTED.value)
        self.audit.record(
            actor="supplier",
            action="quotes_received",
            entity_type="quote",
            entity_id=normalized[0]["quote_id"] if normalized else "-",
            result=f"count={len(normalized)}",
            event_id=event["event_id"],
            metadata={"elapsed_ms": took_ms, "plan_version": event["current_plan_version"]},
        )
        trace = (
            f"Supplier agent -> {len(normalized)} package quote(s) "
            f"(Q-{normalized[0]['quote_id'][2:] if normalized else '-'}) in {took_ms} ms"
            if normalized
            else f"Supplier agent -> no quotable packages in {took_ms} ms"
        )
        narration = plan_narration_text(event, checks)
        result.add(trace)
        result.add(narration, self._carousel_card(event, option_views))

    def _compute_checks(self, event: dict) -> list[EligibilityCheck]:
        from services.planning import evaluate_packages

        packages = [p.dict() for p in DEMO_CATALOG.values()]
        quotes = {
            q["package_id"]: q
            for q in self.repo.quotes_for_event(event["event_id"], event["current_plan_version"])
            if q["status"] == "active"
        }
        return evaluate_packages(
            attendee_count=event["expected_attendees"],
            budget_inr_paise=event["budget_inr_paise"],
            required_equipment=event["equipment_requirements"],
            packages=packages,
            quotes_by_package=quotes,
        )

    def _option_views(self, checks: list[EligibilityCheck]) -> list[OptionView]:
        views = []
        for idx, check in enumerate(checks, start=1):
            views.append(
                OptionView(
                    position=idx,
                    package_id=check.package_id,
                    package_name=check.package_name,
                    quote_id=check.quote_id or "",
                    capacity=check.capacity,
                    price_inr_paise=check.price_inr_paise,
                    deposit_fet_base=check.deposit_fet_base,
                    eligible=check.eligible,
                    reasons=check.reasons,
                    headroom_inr_paise=check.headroom_inr_paise,
                    expires_at="",
                )
            )
        return views

    def _carousel_card(self, event: dict, option_views: list[OptionView]) -> dict | None:
        if not option_views:
            return None
        options = []
        for view in option_views:
            options.append(
                {
                    "package_id": view.package_id,
                    "quote_id": view.quote_id or "-",
                    "name": view.package_name,
                    "capacity": view.capacity,
                    "price_inr_paise": view.price_inr_paise,
                    "deposit_fet_base": view.deposit_fet_base,
                    "eligible": view.eligible,
                    "reasons": view.reasons,
                }
            )
        try:
            return build_package_carousel(
                title=f"Package options for {event['expected_attendees']} attendees (plan v{event['current_plan_version']})",
                options=options,
            )
        except EventPilotError as err:
            logger.warning("carousel card failed validation: %s", err)
            return None

    # ==================================================================
    # Selection and approvals
    # ==================================================================

    def _parse_selection(self, sender: str, text: str) -> Optional[dict]:
        lowered = text.lower()
        option_match = RE_OPTION.match(text)
        if option_match:
            return {"option_number": int(option_match.group(1))}
        select_match = RE_SELECT.search(text)
        target = (select_match.group(1) if select_match else lowered).strip()
        if not target:
            return None
        compact = re.sub(r"[^a-z0-9]", "", target.lower())
        for package in DEMO_CATALOG.values():
            name_compact = re.sub(r"[^a-z0-9]", "", package.name.lower())
            key_compact = package.package_id.replace("wp-", "").replace("-", "")
            if name_compact and (name_compact in compact or key_compact in compact):
                return {"package_id": package.package_id}
        return None

    def _handle_selection(
        self,
        sender: str,
        *,
        package_id: str | None = None,
        quote_id: str | None = None,
        option_number: int | None = None,
        result: TurnResult,
    ) -> None:
        event = self.repo.latest_event_for_owner(sender)
        if event is None:
            result.add("There is no event yet. Tell me about your workshop first.")
            return
        plan = self.repo.get_plan(event["event_id"], event["current_plan_version"])
        if not plan or not plan.get("options"):
            result.add("I don't have current options yet — say 'status' or ask me to fetch quotes again.")
            return
        views = [OptionView(**opt) for opt in plan["options"]]
        checks = [
            EligibilityCheck(**view.dict(exclude={"position", "expires_at"}))
            for view in views
        ]

        chosen: Optional[EligibilityCheck] = None
        if option_number is not None:
            for idx, check in enumerate(checks, start=1):
                if idx == option_number:
                    chosen = check
                    break
            if chosen is None:
                result.add(f"There is no option {option_number} in the current plan.")
                return
            package_id, quote_id = chosen.package_id, chosen.quote_id
        if package_id is None:
            result.add("Which package? Name it (e.g. 'choose Standard Lab') or reply with the option number.")
            return

        try:
            from services.planning import quote_expiry_guard, validate_selection

            chosen = validate_selection(checks, package_id, quote_id=quote_id)
            quote = self.repo.get_quote(chosen.quote_id, event["event_id"])
            quote_expiry_guard(quote)
            active = self._active_reservation(event["event_id"])
            amend_ref = quote.get("amendment_of_reservation_id")
            if active is not None and not amend_ref:
                raise ValidationError(
                    "this event already has an active reservation; use a change of attendance "
                    "(e.g. 'attendance is now 75') to re-plan instead of approving a second package"
                )
            if amend_ref and (active is None or active["reservation_id"] != amend_ref):
                raise ValidationError(
                    "this amendment quote is stale: the reservation it amends is no longer the "
                    "active one. Make the change again (e.g. 'attendance is now 75') to get "
                    "fresh amendment quotes."
                )
        except EventPilotError as err:
            self.audit.record(
                actor="organizer",
                action="selection_rejected",
                entity_type="quote",
                entity_id=package_id,
                result=err.code,
                event_id=event["event_id"],
                metadata={"reason": str(err)[:100]},
            )
            result.add(f"Selection rejected: {err}")
            return

        approval_amount = None
        if quote.get("amendment_of_reservation_id"):
            # Authorization covers the INR delta: committed expense is REPLACED.
            committed = self.repo.spend_totals(event["event_id"])["committed"]
            approval_amount = max(0, quote["price_inr_paise"] - committed)
        approval = self.approvals.create_package_approval(
            event=event,
            quote={**quote, "recipient_wallet": self.settings.supplier_wallet_address},
            amount_inr_paise=approval_amount,
        )
        self.repo.add_spend_entry(
            event["event_id"],
            "proposed",
            approval["amount_inr_paise"],
            ref=approval["approval_id"],
            note=f"package {quote['package_id']}",
        )
        self.audit.record(
            actor="coordinator",
            action="approval_requested",
            entity_type="approval",
            entity_id=approval["approval_id"],
            result="pending",
            event_id=event["event_id"],
            metadata={
                "code": approval["code"],
                "package": quote["package_id"],
                "amount_inr": format_inr(approval["amount_inr_paise"]),
                "deposit": format_fet(quote["deposit_fet_base"]),
                "plan_version": event["current_plan_version"],
            },
        )
        rows = [
            ("Package", quote["package_name"]),
            ("Capacity", f"{quote['capacity']} people"),
            ("Event expense", format_inr(quote["price_inr_paise"])),
            ("Reservation deposit (testnet, not INR)", format_fet(quote["deposit_fet_base"])),
            ("Plan version", f"v{event['current_plan_version']}"),
            ("Quote", quote["quote_id"]),
            ("Approval code", approval["code"]),
            ("Code valid until", format_local(approval["expires_at"], event["timezone"])),
        ]
        card = build_review_card(
            title="Review before approving (exact, single-use)",
            rows=rows,
            approve_label=f"Approve {approval['code']}",
            approve_selection={"type": "approve_package", "approval_code": approval["code"]},
            reject_selection={"type": "reject", "approval_code": approval["code"]},
        )
        result.add(
            "To reserve this exact package, approve the code:\n"
            f"  approve {approval['code']}\n"
            f"This binds plan v{event['current_plan_version']}, quote {quote['quote_id']}, "
            f"{format_inr(quote['price_inr_paise'])} expense and a {format_fet(quote['deposit_fet_base'])} "
            f"simulated deposit. Nothing is spent until you approve.",
            card,
        )

    def _handle_approval_command(self, sender: str, code: Optional[str], *, reject: bool, result: TurnResult) -> None:
        pending = self.repo.pending_approvals_for_owner(sender)
        target = None
        if code:
            code = code.strip().upper()
            for approval in pending:
                if approval["code"] == code or approval["approval_id"] == code:
                    target = approval
                    break
            if target is None:
                # Distinguish "unknown code" from "known but no longer usable".
                known = self.repo.get_approval_by_code(code, sender)
                if known is not None:
                    result.add(
                        f"Approval {code} is {known['status']} and can no longer be used "
                        f"(bound to plan v{known['plan_version']}, consumed at "
                        f"{known.get('consumed_at') or 'n/a'}). Pending codes: "
                        + (", ".join(a["code"] for a in pending) or "none")
                    )
                else:
                    result.add(
                        f"No approval matches '{code}'. Pending codes: "
                        + (", ".join(a["code"] for a in pending) or "none")
                    )
                return
        else:
            if reject:
                result.add("Tell me which approval to reject, e.g. 'reject A-7F3KX'.")
                return
            if len(pending) == 0:
                result.add("There is nothing pending approval right now.")
                return
            if len(pending) > 1:
                result.add(
                    "More than one action is pending; a bare 'yes' is not enough. "
                    "Approve a specific code: " + ", ".join(a["code"] for a in pending)
                )
                return
            target = pending[0]

        event = self.repo.get_event(target["event_id"], sender)
        if reject:
            self.repo.update_approval_status(target["approval_id"], ApprovalStatus.REJECTED.value)
            self._reverse_proposed(event["event_id"], target)
            self.audit.record(
                actor="organizer",
                action="approval_rejected",
                entity_type="approval",
                entity_id=target["approval_id"],
                result="rejected",
                event_id=event["event_id"],
                metadata={"code": target["code"]},
            )
            result.add(f"Approval {target['code']} rejected. Nothing was spent or sent.")
            return

        try:
            action_type = ActionType(target["action_type"])
            approval = self.approvals.consume(
                code_or_id=target["approval_id"],
                owner_sender=sender,
                event=event,
                expected_action=action_type,
            )
        except EventPilotError as err:
            self.audit.record(
                actor="coordinator",
                action="approval_consumption_failed",
                entity_type="approval",
                entity_id=target["approval_id"],
                result=err.code,
                event_id=event["event_id"],
                metadata={"code": target["code"], "reason": str(err)[:100]},
            )
            result.add(f"Approval refused: {err}")
            return

        self.audit.record(
            actor="organizer",
            action="approval_consumed",
            entity_type="approval",
            entity_id=approval["approval_id"],
            result="ok",
            event_id=event["event_id"],
            metadata={"code": approval["code"], "action": approval["action_type"]},
        )
        if action_type == ActionType.RESERVE_PACKAGE:
            self._start_reservation(event, approval, result)
        else:
            self._start_invitation_send(event, approval, result)

    def _start_reservation(self, event: dict, approval: dict, result: TurnResult) -> None:
        payload = approval["payload"]
        request_id = new_id("REQ")
        action_id = f"ACT-{approval['approval_id']}"
        self.repo.record_outbound(
            request_id, "supplier", "reservation_request", event["event_id"], event["owner_sender"]
        )
        request = ReservationRequest(
            request_id=request_id,
            correlation_id=request_id,
            event_id=event["event_id"],
            plan_version=event["current_plan_version"],
            requested_at=iso(self.now_fn()),
            quote_id=payload["quote_id"],
            package_id=payload["package_id"],
            attendee_count=payload["attendee_count"],
            action_id=action_id,
            approval_id=approval["approval_id"],
            amendment_of_reservation_id=payload.get("amendment_of_reservation_id"),
        )
        result.send("supplier", request)
        result.add(
            f"Approval {approval['code']} accepted. Reserving package {payload['package_id']} "
            f"against quote {payload['quote_id']} (action {action_id} recorded before anything moves)..."
        )

    # ==================================================================
    # Reservation + payment
    # ==================================================================

    def _on_reservation_response(self, msg: ReservationResponse, result: TurnResult) -> None:
        outbound = self.repo.get_outbound(msg.request_id)
        event = self.repo.get_event_any_owner(msg.event_id)
        if event is None or outbound is None:
            self.audit.record(
                actor="coordinator",
                action="reservation_response_unmatched",
                entity_type="reservation",
                entity_id=msg.reservation_id,
                result="no_pending_request",
            )
            return
        if msg.status != "success":
            self.repo.complete_outbound(msg.request_id)
            reason = msg.error.message if msg.error else "reservation failed"
            self.audit.record(
                actor="supplier",
                action="reservation_failed",
                entity_type="reservation",
                entity_id=msg.reservation_id or "-",
                result=msg.status,
                event_id=event["event_id"],
                metadata={"reason": reason[:100]},
            )
            result.add(
                f"The supplier could not reserve the package: {reason}\n"
                "Nothing was charged. You can pick another option or request fresh quotes."
            )
            return
        self.repo.complete_outbound(msg.request_id)
        ts = iso(self.now_fn())
        reservation = {
            "reservation_id": msg.reservation_id,
            "event_id": event["event_id"],
            "plan_version": event["current_plan_version"],
            "quote_id": msg.payment_requirement.quote_id if msg.payment_requirement else "",
            "package_id": (msg.payment_requirement.quote_id and self._package_for_quote(event, msg.payment_requirement.quote_id)) or "",
            "status": msg.reservation_status or ReservationStatus.RESERVED.value,
            "payment_mode": self.settings.payment_mode,
            "action_id": f"ACT-{outbound['request_id']}",
            "created_at": ts,
            "updated_at": ts,
        }
        self.repo.save_reservation(reservation)
        # An amendment replaces the previous package: mirror the supplier's
        # supersede decision so coordinator state stays truthful.
        for other in self.repo.reservations_for_event(event["event_id"]):
            if (
                other["reservation_id"] != msg.reservation_id
                and other["status"]
                in ("reserved", "payment_pending", "paid", "confirmed")
            ):
                self.repo.update_reservation(
                    other["reservation_id"], status=ReservationStatus.SUPERSEDED.value
                )
                self.audit.record(
                    actor="coordinator",
                    action="reservation_superseded_locally",
                    entity_type="reservation",
                    entity_id=other["reservation_id"],
                    result="superseded",
                    event_id=event["event_id"],
                )
        self.repo.update_event(
            event["event_id"], event["owner_sender"], status=EventStatus.EXECUTING.value
        )
        self.audit.record(
            actor="supplier",
            action="reservation_created",
            entity_type="reservation",
            entity_id=msg.reservation_id,
            result=msg.reservation_status,
            event_id=event["event_id"],
            metadata={"replayed": bool(msg.replayed), "demo_inventory": True},
        )
        if msg.replayed:
            result.add(
                f"(Idempotent replay) reservation {msg.reservation_id} already existed; "
                "no duplicate reservation was created."
            )
        self._start_payment(event, reservation, msg.payment_requirement, result)

    def _package_for_quote(self, event: dict, quote_id: str) -> str:
        try:
            return self.repo.get_quote(quote_id, event["event_id"])["package_id"]
        except NotFoundError:
            return ""

    def _start_payment(self, event: dict, reservation: dict, requirement, result: TurnResult) -> None:
        if requirement is None:
            result.add("The supplier did not return payment terms; the reservation stays unpaid.")
            return
        payment_id = new_id("P")
        ts = iso(self.now_fn())
        quote = self._safe_quote(event, requirement.quote_id)
        amount_inr = quote["price_inr_paise"] if quote else 0
        payment = {
            "payment_id": payment_id,
            "reservation_id": reservation["reservation_id"],
            "quote_id": requirement.quote_id,
            "event_id": event["event_id"],
            "expected_amount_fet_base": requirement.expected_amount_fet_base,
            "asset": requirement.asset,
            "network": requirement.network,
            "recipient_address": requirement.recipient_address,
            "transaction_id": None,
            "verification_status": PaymentStatus.CREATED.value,
            "payment_mode": self.settings.payment_mode,
            "amount_inr_paise": amount_inr,
            "created_at": ts,
            "verified_at": None,
        }
        self.repo.save_payment(payment)

        if self.settings.payment_mode == PaymentMode.TESTNET.value and not self.settings.payment_rpc_url:
            self.repo.update_payment(
                payment_id, verification_status=PaymentStatus.UNKNOWN.value
            )
            self.audit.record(
                actor="coordinator",
                action="payment_blocked",
                entity_type="payment",
                entity_id=payment_id,
                result="testnet_not_configured",
                event_id=event["event_id"],
            )
            result.add(
                "PAYMENT_MODE=testnet is selected but PAYMENT_RPC_URL/wallet integration is not "
                "configured, so I will NOT attempt or claim any payment. The reservation remains "
                "unpaid. See payments/README.md for the enablement checklist."
            )
            return

        # Persist intent BEFORE the irreversible step (idempotency-first).
        self.repo.update_payment(payment_id, verification_status=PaymentStatus.PENDING_VERIFICATION.value)
        if requirement.expected_amount_fet_base == 0:
            # Zero-delta amendment (same package re-confirmed): no transfer needed.
            tx_id = "SIMULATED-ZERO-DELTA"
            self.repo.update_payment(payment_id, transaction_id=tx_id)
        else:
            try:
                transfer = self.chain.pay(
                    from_address=self.settings.coordinator_wallet_address,
                    to_address=requirement.recipient_address,
                    amount_attofet=requirement.expected_amount_fet_base,
                    event_id=event["event_id"],
                    quote_ref=requirement.quote_id,
                    memo=f"reservation {reservation['reservation_id']}",
                    max_transfer_attofet=self._cap("max_transfer_fet"),
                    per_event_max_attofet=self._cap("per_event_max_fet"),
                )
            except EventPilotError as err:
                self.repo.update_payment(payment_id, verification_status=PaymentStatus.FAILED.value)
                self.audit.record(
                    actor="coordinator",
                    action="payment_failed",
                    entity_type="payment",
                    entity_id=payment_id,
                    result=err.code,
                    event_id=event["event_id"],
                    metadata={"reason": str(err)[:100]},
                )
                result.add(f"The deposit transfer failed before settlement: {err}\nNothing was verified.")
                return
            self.repo.update_payment(
                payment_id, transaction_id=transfer["tx_id"]
            )
            tx_id = transfer["tx_id"]
        request_id = new_id("REQ")
        self.repo.record_outbound(
            request_id, "supplier", "payment_status_request", event["event_id"], event["owner_sender"]
        )
        request = PaymentStatusRequest(
            request_id=request_id,
            correlation_id=request_id,
            event_id=event["event_id"],
            plan_version=event["current_plan_version"],
            requested_at=ts,
            reservation_id=reservation["reservation_id"],
            quote_id=requirement.quote_id,
            payment_id=payment_id,
            transaction_id=tx_id,
            amount_fet_base=requirement.expected_amount_fet_base,
            asset=requirement.asset,
            network=requirement.network,
            recipient_address=requirement.recipient_address,
            action_id=f"ACT-{payment_id}",
        )
        result.send("supplier", request)
        result.add(
            f"Reservation {reservation['reservation_id']} recorded. Deposit "
            f"{format_fet(requirement.expected_amount_fet_base)} settled from the coordinator's demo "
            f"wallet (tx {tx_id}) — asking the supplier to verify the evidence "
            "independently before anything is marked paid..."
        )

    def _cap(self, name: str) -> int:
        from decimal import Decimal

        return int((Decimal(str(getattr(self.settings, name))) * ATESTFET).to_integral_value())

    def _safe_quote(self, event: dict, quote_id: str) -> Optional[dict]:
        try:
            return self.repo.get_quote(quote_id, event["event_id"])
        except NotFoundError:
            return None

    def _on_payment_response(self, msg: PaymentStatusResponse, result: TurnResult) -> None:
        outbound = self.repo.get_outbound(msg.request_id)
        payment = self.repo.get_payment(msg.payment_id)
        if payment is None:
            self.audit.record(
                actor="coordinator",
                action="payment_response_unmatched",
                entity_type="payment",
                entity_id=msg.payment_id,
                result="unknown_payment",
            )
            return
        event = self.repo.get_event_any_owner(payment["event_id"])
        self.repo.complete_outbound(msg.request_id) if outbound else None
        status = msg.verification_status
        ts = iso(self.now_fn())
        updates: dict = {"verification_status": status}
        if status in (PaymentStatus.VERIFIED.value, PaymentStatus.VERIFIED_SIMULATED.value):
            updates["verified_at"] = ts
        self.repo.update_payment(payment["payment_id"], **updates)

        reservation = self.repo.get_reservation(payment["reservation_id"])
        if status in (PaymentStatus.VERIFIED.value, PaymentStatus.VERIFIED_SIMULATED.value):
            if reservation and reservation["status"] != ReservationStatus.CONFIRMED.value:
                self.repo.update_reservation(
                    reservation["reservation_id"], status=ReservationStatus.CONFIRMED.value
                )
            totals = self.repo.spend_totals(event["event_id"])
            if totals["committed"]:
                self.repo.add_spend_entry(
                    event["event_id"],
                    "committed_reversal",
                    totals["committed"],
                    ref=payment["payment_id"],
                    note="package replaced",
                )
            self.repo.add_spend_entry(
                event["event_id"],
                "committed",
                payment.get("amount_inr_paise") or 0,
                ref=payment["payment_id"],
                note=f"quote {payment['quote_id']}",
            )
            if totals["proposed"]:
                self.repo.add_spend_entry(
                    event["event_id"],
                    "proposed_reversal",
                    max(0, totals["proposed"]),
                    ref=payment["payment_id"],
                    note="proposal settled",
                )
            self.repo.update_event(
                event["event_id"], event["owner_sender"], status=EventStatus.READY.value
            )
            self.audit.record(
                actor="supplier",
                action="payment_verified",
                entity_type="payment",
                entity_id=payment["payment_id"],
                result=status,
                event_id=event["event_id"],
                metadata={"simulated": status == PaymentStatus.VERIFIED_SIMULATED.value},
            )
            label = (
                "SIMULATED — NO ON-CHAIN PAYMENT"
                if status == PaymentStatus.VERIFIED_SIMULATED.value
                else "verified on-chain"
            )
            result.add(
                f"Payment {payment['payment_id']} verified by the supplier ({label}). "
                f"Committed expense is now {format_inr(self.repo.spend_totals(event['event_id'])['committed'])} "
                f"of the {format_inr(event['budget_inr_paise'])} budget. "
                "Reservation is confirmed against demo inventory — that is not a real-world booking."
            )
            self._request_invitation_draft(event, result, note="")
            return
        if status == "unknown":
            self.audit.record(
                actor="supplier",
                action="payment_unknown",
                entity_type="payment",
                entity_id=payment["payment_id"],
                result="unknown",
                event_id=event["event_id"],
                metadata={"reason": msg.reason[:100]},
            )
            result.add(
                "The supplier could not yet determine the settlement outcome "
                f"({msg.reason}). The payment is marked UNKNOWN; I will not retry blindly — "
                "say 'status' to re-check, or ask me to reconcile with fresh evidence."
            )
            return
        # rejected / failed
        if reservation:
            self.repo.update_reservation(
                reservation["reservation_id"], status=ReservationStatus.PAYMENT_FAILED.value
            )
        self.audit.record(
            actor="supplier",
            action="payment_rejected",
            entity_type="payment",
            entity_id=payment["payment_id"],
            result=status,
            event_id=event["event_id"],
            metadata={"reason": msg.reason[:100]},
        )
        result.add(
            f"Payment {payment['payment_id']} was REJECTED by the supplier: {msg.reason}\n"
            "The reservation was not paid. Nothing else happened."
        )

    # ==================================================================
    # Invitations
    # ==================================================================

    def _request_invitation_draft(self, event: dict, result: TurnResult, *, note: str) -> None:
        request_id = new_id("REQ")
        self.repo.record_outbound(
            request_id, "outreach", "invitation_draft_request", event["event_id"], event["owner_sender"]
        )
        request = InvitationDraftRequest(
            request_id=request_id,
            correlation_id=request_id,
            event_id=event["event_id"],
            plan_version=event["current_plan_version"],
            requested_at=iso(self.now_fn()),
            title=event["title"],
            event_type=event.get("event_type") or "workshop",
            date=event["date"],
            timezone=event.get("timezone") or "Asia/Kolkata",
            city=event["city"],
            expected_attendees=event["expected_attendees"],
            note=note,
        )
        result.send("outreach", request)
        result.add("Requesting an invitation draft from the outreach agent (drafting is safe; sending needs your approval)...")

    def _on_draft_response(self, msg: InvitationDraftResponse, result: TurnResult) -> None:
        outbound = self.repo.get_outbound(msg.request_id)
        event = self.repo.get_event_any_owner(msg.event_id)
        if event is None or outbound is None:
            self.audit.record(
                actor="coordinator",
                action="draft_response_unmatched",
                entity_type="invitation",
                entity_id=msg.invitation_id,
                result="no_pending_request",
            )
            return
        self.repo.complete_outbound(msg.request_id)
        if msg.status != "success":
            reason = msg.error.message if msg.error else "drafting failed"
            result.add(f"The outreach agent could not draft an invitation: {reason}")
            return
        self.repo.save_invitation_record(
            {
                "invitation_id": msg.invitation_id,
                "event_id": msg.event_id,
                "plan_version": event["current_plan_version"],
                "draft_version": msg.draft_version,
                "subject": msg.subject,
                "body_preview": msg.body[:280],
                "content_hash": msg.content_hash,
                "recipient_set_hash": msg.recipient_set_hash,
                "recipient_count": msg.recipient_count,
                "status": "draft",
                "mode": self.settings.email_provider,
                "created_at": iso(self.now_fn()),
            }
        )
        if msg.superseded_invitation_id:
            self.repo.update_invitation_record(
                msg.superseded_invitation_id, status=InvitationStatus.STALE.value
            )
        approval = self.approvals.create_invitation_approval(
            event=event,
            invitation_id=msg.invitation_id,
            draft_version=msg.draft_version,
            content_hash=msg.content_hash,
            recipient_set_hash=msg.recipient_set_hash,
            recipient_count=msg.recipient_count,
        )
        self.audit.record(
            actor="outreach",
            action="invitation_drafted",
            entity_type="invitation",
            entity_id=msg.invitation_id,
            result=f"v{msg.draft_version}",
            event_id=event["event_id"],
            metadata={"recipients": msg.recipient_count, "code": approval["code"]},
        )
        rows = [
            ("Subject", msg.subject),
            ("Recipients", f"{msg.recipient_count} allowlisted test recipient(s) — addresses stay private"),
            ("Draft version", f"v{msg.draft_version}"),
            ("Content hash", msg.content_hash[:16] + "..."),
            ("Approval code", approval["code"]),
        ]
        card = build_review_card(
            title="Approve sending this invitation (exact draft, exact recipients)",
            rows=rows,
            approve_label=f"Approve {approval['code']}",
            approve_selection={"type": "approve_invitation", "approval_code": approval["code"]},
            reject_selection={"type": "reject", "approval_code": approval["code"]},
        )
        result.add(
            f"Invitation draft v{msg.draft_version}:\n\"{msg.subject}\"\n{msg.body[:400]}\n\n"
            f"Approve sending with:  approve {approval['code']}\n"
            "(Emails only go to the configured allowlisted test recipients.)",
            card,
        )

    def _start_invitation_send(self, event: dict, approval: dict, result: TurnResult) -> None:
        payload = approval["payload"]
        request_id = new_id("REQ")
        action_id = f"ACT-{approval['approval_id']}"
        self.repo.record_outbound(
            request_id, "outreach", "send_invitation_request", event["event_id"], event["owner_sender"]
        )
        request = SendInvitationRequest(
            request_id=request_id,
            correlation_id=request_id,
            event_id=event["event_id"],
            plan_version=event["current_plan_version"],
            requested_at=iso(self.now_fn()),
            invitation_id=payload["invitation_id"],
            draft_version=payload["draft_version"],
            content_hash=payload["content_hash"],
            recipient_set_hash=payload["recipient_set_hash"],
            approval_id=approval["approval_id"],
            action_id=action_id,
        )
        result.send("outreach", request)
        result.add(f"Approval {approval['code']} accepted — asking the outreach agent to send (once, idempotently)...")

    def _on_send_response(self, msg: SendInvitationResponse, result: TurnResult) -> None:
        outbound = self.repo.get_outbound(msg.request_id)
        if outbound is None:
            self.audit.record(
                actor="coordinator",
                action="send_response_unmatched",
                entity_type="invitation",
                entity_id=msg.invitation_id,
                result="no_pending_request",
            )
            return
        self.repo.complete_outbound(msg.request_id)
        self.repo.update_invitation_record(
            msg.invitation_id,
            status=msg.invitation_status,
            provider_message_ids=msg.provider_message_ids,
            sent_at=iso(self.now_fn())
            if msg.invitation_status in ("sent", "logged")
            else None,
        )
        self.audit.record(
            actor="outreach",
            action="invitation_send_completed",
            entity_type="invitation",
            entity_id=msg.invitation_id,
            result=msg.invitation_status,
            event_id=msg.event_id,
            metadata={"uncertain": msg.uncertain},
        )
        if msg.invitation_status == "sent":
            result.add(
                "Invitation sent through the configured provider. RSVP links and codes are in "
                "the email; attendees can also RSVP in chat. Ask 'how many have confirmed?' anytime."
            )
        elif msg.invitation_status == "logged":
            codes = [
                (line.recipient_ref, line.rsvp_code)
                for line in msg.results
                if line.rsvp_code
            ]
            text = (
                "EMAIL MODE = console (development): the invitation was written to the agent "
                "logs, NOT delivered. In this mode nothing left the machine. Configure SMTP "
                "to deliver for real."
            )
            if codes:
                text += (
                    "\n\nFor this local demo, use the RSVP codes that would have been emailed "
                    "(single-use, bound to this event):\n"
                    + "\n".join(f"  {ref} -> RSVP {code} yes" for ref, code in codes)
                    + "\nDuplicate submissions never inflate the count."
                )
            result.add(text)
        elif msg.invitation_status == "unknown":
            result.add(
                "The provider could not confirm delivery (uncertain outcome). The invitation is "
                "marked UNKNOWN and will NOT be resent automatically; reconcile before retrying."
            )
        else:
            reason = "; ".join(r.status for r in msg.results) or "send failed"
            result.add(f"The invitation send FAILED ({reason}). Nothing was marked as sent.")

    # ==================================================================
    # RSVP
    # ==================================================================

    def _query_rsvp_count(self, sender: str, result: TurnResult) -> None:
        event = self.repo.latest_event_for_owner(sender)
        if event is None:
            result.add("There is no event yet — tell me about your workshop first.")
            return
        request_id = new_id("REQ")
        self.repo.record_outbound(
            request_id, "outreach", "rsvp_count_request", event["event_id"], event["owner_sender"]
        )
        result.send(
            "outreach",
            RsvpCountRequest(
                request_id=request_id,
                correlation_id=request_id,
                event_id=event["event_id"],
            ),
        )
        cached = self.repo.get_rsvp_count(event["event_id"])
        result.add(
            f"Checking with the outreach agent (last known count: {cached if cached is not None else 0})..."
        )

    def _submit_rsvp(self, sender: str, code: str, decision: str, result: TurnResult) -> None:
        request_id = new_id("REQ")
        self.repo.record_outbound(request_id, "outreach", "rsvp_submit_request", "-", sender)
        result.send(
            "outreach",
            RsvpSubmitRequest(
                request_id=request_id,
                correlation_id=request_id,
                rsvp_code=code,
                decision=decision,
                attendee_reference="",
            ),
        )
        result.add(f"Validating RSVP code {code} with the outreach agent...")

    def _on_count_response(self, msg: RsvpCountResponse, result: TurnResult) -> None:
        outbound = self.repo.get_outbound(msg.request_id)
        if outbound is None:
            return
        self.repo.complete_outbound(msg.request_id)
        self.repo.set_rsvp_count(msg.event_id, msg.confirmed_count)
        self.audit.record(
            actor="outreach",
            action="rsvp_count_reported",
            entity_type="rsvp",
            entity_id=msg.event_id,
            result=f"count={msg.confirmed_count}",
            event_id=msg.event_id,
        )
        result.add(
            f"RSVP count for {msg.event_id}: {msg.confirmed_count} confirmed "
            "(source: outreach agent records; seeded numbers are demo data)."
        )

    def _on_rsvp_submit_response(self, msg: RsvpSubmitResponse, result: TurnResult) -> None:
        outbound = self.repo.get_outbound(msg.request_id)
        if outbound is None:
            return
        self.repo.complete_outbound(msg.request_id)
        if msg.status != "success":
            reason = msg.error.message if msg.error else "RSVP rejected"
            result.add(f"RSVP refused: {reason}")
            return
        if msg.already_registered:
            result.add(
                f"That RSVP code was already used. Current count: {msg.confirmed_count} "
                "(duplicate submissions never inflate the count)."
            )
            return
        self.repo.set_rsvp_count(msg.event_id, msg.confirmed_count)
        self.audit.record(
            actor="outreach",
            action="rsvp_recorded",
            entity_type="rsvp",
            entity_id=msg.event_id,
            result=f"count={msg.confirmed_count}",
            event_id=msg.event_id,
            metadata={"via": "chat_code"},
        )
        result.add(f"RSVP recorded. {msg.event_id} now has {msg.confirmed_count} confirmed attendee(s).")

    # ==================================================================
    # Replanning
    # ==================================================================

    def _handle_replan(self, sender: str, new_count: int, result: TurnResult) -> None:
        event = self.repo.latest_event_for_owner(sender)
        if event is None:
            result.add("There is no event to re-plan yet.")
            return
        try:
            outcome = self.replanning.plan_change(event, new_count)
        except EventPilotError as err:
            result.add(f"I can't apply that change: {err}")
            return
        event = self.repo.update_event(
            event["event_id"],
            sender,
            expected_attendees=new_count,
            current_plan_version=outcome.new_plan_version,
            status=EventStatus.QUOTED.value,
        )
        self.repo.save_plan(event["event_id"], outcome.new_plan_version, new_count, [])
        self.audit.record(
            actor="organizer",
            action="replan_requested",
            entity_type="plan",
            entity_id=f"{event['event_id']}#v{outcome.new_plan_version}",
            result="ok",
            event_id=event["event_id"],
            metadata={
                "attendees": f"{outcome.old_attendees}->{new_count}",
                "invalidated_approvals": outcome.invalidated_approvals,
            },
        )
        amendment_note = outcome.amendment_of_reservation_id
        self._request_quotes(event, result, note=amendment_note)
        if outcome.supersede_required:
            request_id = new_id("REQ")
            action_id = f"ACT-SUP-{request_id}"
            self.repo.record_outbound(
                request_id, "supplier", "supersede_request", event["event_id"], sender
            )
            result.send(
                "supplier",
                ReservationSupersedeRequest(
                    request_id=request_id,
                    correlation_id=request_id,
                    event_id=event["event_id"],
                    plan_version=outcome.new_plan_version,
                    reservation_id=outcome.supersede_reservation_id,
                    action_id=action_id,
                    reason="attendance changed",
                ),
            )
        for line in outcome.explanation:
            result.add(line)
        result.add("Requesting fresh supplier quotes for the new headcount; nothing is auto-reserved, paid or sent.")

    def _on_supersede_response(self, msg: ReservationSupersedeResponse, result: TurnResult) -> None:
        outbound = self.repo.get_outbound(msg.request_id)
        if outbound is None:
            return
        self.repo.complete_outbound(msg.request_id)
        if msg.status == "success":
            reservation = self.repo.get_reservation(msg.reservation_id)
            if reservation:
                self.repo.update_reservation(msg.reservation_id, status=ReservationStatus.SUPERSEDED.value)
            self.audit.record(
                actor="supplier",
                action="reservation_superseded",
                entity_type="reservation",
                entity_id=msg.reservation_id,
                result="ok",
                event_id=msg.event_id,
            )
            result.add(f"Reservation {msg.reservation_id} marked superseded by the supplier (it was never paid).")

    # ==================================================================
    # Reports
    # ==================================================================

    def _status(self, sender: str, result: TurnResult) -> None:
        events = self.repo.list_events_for_owner(sender)
        if not events:
            result.add(GREETING)
            return
        event = events[0]
        lines = [
            f"Status for {event['event_id']} — {event['title']} (plan v{event['current_plan_version']}, "
            f"{event['status']}):",
            f"- {event['expected_attendees']} attendees in {event['city']} on {event['date']} "
            f"({event['timezone']}), budget {format_inr(event['budget_inr_paise'])}",
        ]
        totals = self.repo.spend_totals(event["event_id"])
        remaining = event["budget_inr_paise"] - totals["committed"] - max(0, totals["proposed"])
        lines.append(
            f"- Spend: committed {format_inr(totals['committed'])}, proposed "
            f"{format_inr(max(0, totals['proposed']))}, remaining {format_inr(max(0, remaining))} "
            f"(budget is a hard cap)"
        )
        reservation = self._active_reservation(event["event_id"])
        if reservation:
            payment_rows = self.repo.payments_for_reservation(reservation["reservation_id"])
            pay = payment_rows[-1] if payment_rows else None
            pay_line = (
                f"payment {pay['payment_id']} -> {pay['verification_status']} "
                f"({'SIMULATED — NO ON-CHAIN PAYMENT' if pay['verification_status'] == 'verified_simulated' else pay.get('payment_mode')})"
                if pay
                else "no payment attempt"
            )
            lines.append(f"- Reservation {reservation['reservation_id']} ({reservation['status']}), {pay_line}")
        else:
            lines.append("- No active reservation")
        invitations = self.repo.invitations_for_event(event["event_id"])
        if invitations:
            inv = invitations[-1]
            lines.append(f"- Invitation {inv['invitation_id']} v{inv['draft_version']}: {inv['status']} ({inv.get('mode')} mode)")
        else:
            lines.append("- No invitation drafted yet")
        count = self.repo.get_rsvp_count(event["event_id"])
        lines.append(f"- RSVPs confirmed: {count if count is not None else 0} (demo data)")
        pending = self.repo.pending_outbound()
        pending = [p for p in pending if p["owner_sender"] in ("", sender)]
        if pending:
            lines.append(
                "- Pending with other agents: "
                + ", ".join(f"{p['kind']} ({p['request_id']})" for p in pending)
                + " — honest pending state; nothing is assumed done"
            )
        result.add("\n".join(lines))

    def _checklist(self, sender: str, result: TurnResult) -> None:
        event = self.repo.latest_event_for_owner(sender)
        if event is None:
            result.add("There is no event yet.")
            return
        result.add(self.checklists.narration(event))

    def _timeline(self, sender: str, result: TurnResult) -> None:
        event = self.repo.latest_event_for_owner(sender)
        if event is None:
            result.add("There is no event yet.")
            return
        text = self.audit.timeline_narration(event["event_id"])
        if self.llm is not None:
            summary = self.llm.summarize_timeline(text)
            if summary:
                text = summary + "\n\n" + text
        result.add(text)

    def _reset(self, sender: str, result: TurnResult) -> None:
        removed = self.repo.reset_owner(sender)
        self.audit.record(
            actor="organizer",
            action="demo_reset",
            entity_type="event",
            entity_id="-",
            result=f"events_removed={removed}",
        )
        result.add(
            f"Reset done: removed {removed} event(s) owned by you. Configuration and other "
            "organizers' data were not touched."
        )

    def _greeting(self, result: TurnResult) -> None:
        result.add(GREETING)

    # ==================================================================
    # Helpers
    # ==================================================================

    def _rate_limit(self, sender: str) -> None:
        import time as _time

        now = _time.monotonic()
        window = self._rate_hits.setdefault(sender, [])
        while window and now - window[0] > 60:
            window.pop(0)
        if len(window) >= self.settings.rate_limit_per_minute:
            raise RateLimitError("rate limit exceeded")
        window.append(now)

    def _sweep_timeouts(self) -> list[str]:
        """Mark timed-out outbound requests failed and honest-notify."""
        notices: list[str] = []
        now = self.now_fn()
        for request in self.repo.pending_outbound():
            created = parse_iso(request["created_at"])
            if (now - created).total_seconds() > self.timeout_seconds:
                self.repo.fail_outbound(request["request_id"])
                if request["kind"] == "payment_status_request":
                    self._mark_pending_payments_unknown(request["event_id"])
                notices.append(
                    f"No reply from the {request['role']} agent for {request['kind']} "
                    f"{request['request_id']} within {self.timeout_seconds:.0f}s — marked FAILED. "
                    "Nothing was assumed to have succeeded."
                )
                self.audit.record(
                    actor="coordinator",
                    action="agent_timeout",
                    entity_type="request",
                    entity_id=request["request_id"],
                    result="failed",
                    event_id=request["event_id"] if request["event_id"] != "-" else None,
                    metadata={"role": request["role"], "kind": request["kind"]},
                )
        return notices

    def _mark_pending_payments_unknown(self, event_id: str) -> None:
        for reservation in self.repo.reservations_for_event(event_id):
            for payment in self.repo.payments_for_reservation(reservation["reservation_id"]):
                if payment["verification_status"] == PaymentStatus.PENDING_VERIFICATION.value:
                    self.repo.update_payment(
                        payment["payment_id"], verification_status=PaymentStatus.UNKNOWN.value
                    )

    def _active_reservation(self, event_id: str) -> Optional[dict]:
        for reservation in self.repo.reservations_for_event(event_id):
            if reservation["status"] in ("reserved", "payment_pending", "paid", "confirmed"):
                return reservation
        return None

    def _reverse_proposed(self, event_id: str, approval: dict) -> None:
        amount = approval.get("amount_inr_paise") or 0
        if amount:
            self.repo.add_spend_entry(
                event_id, "proposed_reversal", amount, ref=approval["approval_id"], note="rejected"
            )


def plan_narration_text(event: dict, checks: list[EligibilityCheck]) -> str:
    from services.planning import plan_narration

    base = plan_narration(
        checks,
        attendee_count=event["expected_attendees"],
        budget_inr_paise=event["budget_inr_paise"],
    )
    return base


def _has_any(brief) -> bool:
    return any(
        [
            brief.title,
            brief.city,
            brief.date,
            brief.expected_attendees,
            brief.budget_inr_paise is not None,
        ]
    )


_FORM_FIELD_NAMES = ("title", "city", "date", "attendees", "budget_inr", "equipment")


def _unwrap_selection(selection: dict) -> Optional[dict]:
    """Normalise the shapes ASI:One can deliver for a card interaction."""
    if not isinstance(selection, dict):
        return None
    inner = selection.get("selection")
    if isinstance(inner, str):
        import json

        try:
            inner = json.loads(inner)
        except json.JSONDecodeError:
            inner = None
    if isinstance(inner, dict):
        return inner
    data = selection.get("data")
    if isinstance(data, dict) and isinstance(data.get("selection"), dict):
        return data["selection"]
    return selection


def _card_selection_from_text(text: str) -> Optional[dict]:
    """Return a card selection carried as a JSON text block, if any.

    ASI:One sends card clicks as a follow-up ``TextContent``: a flat JSON
    object for direct @mentions, or prose through the planner. Only the JSON
    shape is handled here; prose is handled by the command parsers.
    """
    import json

    stripped = (text or "").strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    unwrapped = _unwrap_selection(data)
    if unwrapped is None:
        return None
    if any(
        key in unwrapped
        for key in (
            "type",
            "action",
            "approval_code",
            "approval_id",
            "cancelled",
            "fields",
            "form",
            "values",
            *_FORM_FIELD_NAMES,
        )
    ):
        return unwrapped
    return None


def _fields_to_text(fields: dict) -> str:
    """Turn submitted form fields into one brief sentence for the parsers."""
    if not isinstance(fields, dict):
        return ""

    def _value(key: str) -> str:
        value = fields.get(key)
        if value is None or isinstance(value, bool):
            return ""
        if isinstance(value, list):
            return ", ".join(str(item) for item in value if str(item).strip())
        return str(value).strip()

    parts = []
    if _value("title"):
        parts.append(f"organise a {_value('title')}")
    if _value("city"):
        parts.append(f"in {_value('city')}")
    if _value("date"):
        parts.append(f"on {_value('date')}")
    if _value("attendees"):
        parts.append(f"for {_value('attendees')} people")
    if _value("budget_inr"):
        parts.append(f"with a budget of {_value('budget_inr')} rupees")
    if _value("equipment"):
        parts.append(f"with {_value('equipment')}")
    return " ".join(parts)
