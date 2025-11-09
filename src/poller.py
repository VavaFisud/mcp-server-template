from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from config import Settings
from ecole_directe.exceptions import QCMRequired, EcoleDirecteError
from poke_notifier import PokeNotifier
from service import EcoleDirecteService, SyncDelta


logger = logging.getLogger(__name__)


class UpdatePoller:
    """Background worker that periodically syncs data and pushes alerts to Poke."""

    def __init__(
        self,
        settings: Settings,
        service: EcoleDirecteService,
        notifier: PokeNotifier,
    ) -> None:
        self.settings = settings
        self.service = service
        self.notifier = notifier
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Background poller started (interval=%ss)", self.settings.poll_interval_seconds)

    def stop(self) -> None:
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=5)
        self._thread = None
        logger.info("Background poller stopped")

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                delta = self.service.sync()
                if delta.has_updates():
                    self._dispatch(delta)
            except QCMRequired as qcm_exc:
                logger.warning("QCM requis, arrêt du poller. Répondez via l'outil `respond_qcm` pour continuer.")
                self._notify_qcm()
                break  # Stop the loop
            except EcoleDirecteError as exc:
                logger.error("Erreur EcoleDirecte: %s", exc, exc_info=True)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Erreur inattendue dans le poller: %s", exc)
            finally:
                self._stop_event.wait(self.settings.poll_interval_seconds)

    def _notify_qcm(self) -> None:
        pending = self.service.pending_qcm()
        if not pending:
            return
        choices = "\n".join(
            f"{choice['index']}. {choice['decoded']}"
            for choice in pending["choices"]
        )
        message = (
            "EcoleDirecte demande une validation QCM.\n"
            f"Question: {pending['question']}\n"
            f"Choix :\n{choices}\n"
            "Répondez via l'outil `respond_qcm`."
        )
        try:
            self.notifier.send(
                message,
                context={"type": "qcm", "question": pending["question"]},
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Impossible d'envoyer l'alerte QCM à Poke: %s", exc)

    def notify_qcm_prompt(self) -> None:
        """Expose QCM alert logic for manual triggers."""
        self._notify_qcm()

    def dispatch_delta(self, delta: SyncDelta) -> None:
        """Send notifications for a precomputed delta (used at startup)."""
        if delta.has_updates():
            self._dispatch(delta)

    def _dispatch(self, delta: SyncDelta) -> None:
        for note in delta.new_notes:
            libelle = note.get("libelleMatiere") or note.get("codeMatiere", "Matière")
            title = note.get("devoir", "Nouvelle note")
            valeur = note.get("valeur")
            sur = note.get("noteSur")
            message = f"Nouvelle note en {libelle}: {title} -> {valeur}/{sur}"
            self._safe_notify(message, {"type": "note", "noteId": note.get("id")})

        for item in delta.new_messages:
            objet = item.get("objet") or item.get("titre") or "Nouveau message"
            auteur = item.get("auteur") or item.get("from") or ""
            message = f"Nouveau message '{objet}' reçu {('de ' + auteur) if auteur else ''}".strip()
            self._safe_notify(message, {"type": "message", "messageId": item.get("id")})

        for absence in delta.new_absences:
            label = absence.get("libelle", absence.get("typeElement", "absence"))
            display = absence.get("displayDate") or absence.get("date")
            message = f"Vie scolaire: {label} ({display})"
            self._safe_notify(message, {"type": "absence", "absenceId": absence.get("id")})

        for course in delta.cancelled_courses:
            name = course.get("text") or course.get("matiere", "Cours")
            start = course.get("start_date")
            message = f"Cours annulé: {name} ({start})"
            self._safe_notify(message, {"type": "schedule", "courseId": course.get("id")})

    def _safe_notify(self, text: str, context: Optional[dict] = None) -> None:
        try:
            self.notifier.send(text, context=context)
        except Exception as exc:  # noqa: BLE001
            logger.error("Echec d'envoi de notification Poke: %s", exc)
