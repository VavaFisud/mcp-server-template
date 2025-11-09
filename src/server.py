from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

import uvicorn
from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response

from config import Settings, load_settings
from ecole_directe.exceptions import EcoleDirecteError, QCMRequired
from poke_notifier import PokeNotifier
from poller import UpdatePoller
from service import EcoleDirecteService
from state_store import StateStore


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ecole_directe_mcp")

settings = load_settings()
state_store = StateStore(settings.state_file)
service = EcoleDirecteService(settings, state_store)
notifier = PokeNotifier(settings)
poller = UpdatePoller(settings, service, notifier)

mcp = FastMCP("EcoleDirecte <> Poke")


def _format_note(note: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": note.get("id"),
        "subject": note.get("libelleMatiere") or note.get("codeMatiere"),
        "title": note.get("devoir"),
        "value": note.get("valeur"),
        "out_of": note.get("noteSur"),
        "date": note.get("date"),
        "recorded_at": note.get("dateSaisie"),
        "teacher": ", ".join(prof.get("nom") for prof in note.get("professeurs", [])) if note.get("professeurs") else None,
        "comment": note.get("commentaire"),
    }


def _format_message(message: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": message.get("id"),
        "subject": message.get("objet") or message.get("titre"),
        "from": message.get("auteur") or message.get("from"),
        "date": message.get("date"),
        "read": message.get("lu"),
        "urgent": message.get("urgent"),
        "summary": message.get("preview") or message.get("resume"),
    }


def _format_absence(absence: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": absence.get("id"),
        "type": absence.get("typeElement"),
        "label": absence.get("libelle"),
        "date": absence.get("date"),
        "display": absence.get("displayDate"),
        "justifie": absence.get("justifie"),
        "commentaire": absence.get("commentaire"),
    }


def _format_course(course: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": course.get("id"),
        "title": course.get("text") or course.get("matiere"),
        "start": course.get("start_date"),
        "end": course.get("end_date"),
        "room": course.get("salle"),
        "prof": course.get("prof"),
        "is_cancelled": course.get("isAnnule"),
    }


@mcp.tool(description="Afficher l'état courant de la connexion EcoleDirecte et des derniers rafraîchissements.")
def get_status() -> Dict[str, Any]:
    pending_qcm = service.pending_qcm()
    account = state_store.get_account_metadata()
    status = {
        "connected": bool(state_store.get_token()),
        "pending_qcm": bool(pending_qcm),
        "account": {
            "name": f"{account.get('prenom')} {account.get('nom')}" if account else None,
            "id": account.get("id") if account else None,
            "etablissement": account.get("nomEtablissement") if account else None,
        } if account else None,
        "last_sync": state_store.get("last_sync"),
    }
    if pending_qcm:
        status["qcm"] = {
            "question": pending_qcm["question"],
            "choices": [
                {"index": choice["index"], "value": choice["decoded"]}
                for choice in pending_qcm["choices"]
            ],
        }
    return status


@mcp.tool(description="Répondre au QCM de double authentification (fournir l'index ou le texte du choix).")
def respond_qcm(answer: str) -> Dict[str, Any]:
    try:
        choice: int | str
        if answer.isdigit():
            choice = int(answer)
        else:
            choice = answer
        credentials = service.client.submit_qcm_answer(choice)
        service.client.login(force=True)
        delta = service.sync()
        poller.dispatch_delta(delta)
        poller.start()  # Restart the poller
        return {"status": "ok", "fa_credentials": credentials}
    except ValueError as exc:
        return {"status": "error", "reason": str(exc)}
    except EcoleDirecteError as exc:
        return {"status": "error", "reason": str(exc)}


@mcp.tool(description="Forcer une synchronisation immédiate avec EcoleDirecte.")
def sync_now() -> Dict[str, Any]:
    try:
        delta = service.sync()
        return {
            "new_notes": [ _format_note(note) for note in delta.new_notes ],
            "new_messages": [ _format_message(msg) for msg in delta.new_messages ],
            "new_absences": [ _format_absence(absence) for absence in delta.new_absences ],
            "cancelled_courses": [ _format_course(course) for course in delta.cancelled_courses ],
            "timeline_events": delta.timeline_events,
        }
    except QCMRequired:
        poller.notify_qcm_prompt()
        return {"status": "qcm_required", "message": "Veuillez répondre au QCM via respond_qcm."}
    except EcoleDirecteError as exc:
        return {"status": "error", "message": str(exc)}


@mcp.tool(description="Afficher les dernières notes (optionnellement limitées).")
def list_notes(limit: int = 5) -> List[Dict[str, Any]]:
    notes = service.get_latest_notes()
    formatted = [_format_note(note) for note in notes]
    return formatted[:limit]


@mcp.tool(description="Afficher les derniers messages reçus.")
def list_messages(limit: int = 5) -> List[Dict[str, Any]]:
    messages = service.get_latest_messages()
    formatted = [_format_message(msg) for msg in messages]
    return formatted[:limit]


@mcp.tool(description="Afficher les absences et retards enregistrés.")
def list_absences() -> Dict[str, Any]:
    absences_payload = service.get_latest_absences()
    absences = absences_payload.get("absencesRetards", [])
    sanctions = absences_payload.get("sanctionsEncouragements", [])
    return {
        "absences_retards": [_format_absence(item) for item in absences],
        "sanctions": sanctions,
    }


@mcp.tool(description="Afficher l'emploi du temps à partir d'aujourd'hui.")
def list_schedule(days: int = 7, include_cancelled: bool = True) -> Dict[str, Any]:
    today = date.today()
    horizon = today + timedelta(days=days)
    try:
        schedule = service.client.fetch_schedule(today, horizon)
        service.update_schedule_cache(schedule)
    except QCMRequired:
        poller.notify_qcm_prompt()
        return {"status": "qcm_required", "courses": []}
    except EcoleDirecteError as exc:
        return {"status": "error", "message": str(exc), "courses": []}
    formatted = [_format_course(course) for course in schedule if include_cancelled or not course.get("isAnnule")]
    return {
        "window": {"start": today.isoformat(), "end": horizon.isoformat()},
        "courses": formatted,
    }


@mcp.tool(description="Purger l'état de la session EcoleDirecte (token, compte) pour forcer une nouvelle connexion.")
def reset_session() -> Dict[str, Any]:
    """Resets the session state, forcing a fresh login on the next request."""
    poller.stop()
    state_store.clear_session()
    poller.start()
    return {"status": "ok", "message": "Session purgée. Le poller a été redémarré."}


def start_background_tasks() -> None:
    try:
        delta = service.sync()
        poller.dispatch_delta(delta)
    except QCMRequired:
        logger.info("QCM requis lors du démarrage - en attente de réponse via respond_qcm.")
        poller.notify_qcm_prompt()
    except EcoleDirecteError as exc:
        logger.warning("Connexion ED impossible pour le moment: %s", exc)
    poller.start()


if __name__ == "__main__":
    start_background_tasks()
    port = int(os.environ.get("PORT", 8000))
    host = "0.0.0.0"
    logger.info("Starting MCP server on %s:%s", host, port)
    app = mcp.http_app(path="/mcp")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level="info",
    )
