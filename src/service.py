from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from threading import RLock
from typing import Any, Dict, List, Optional

from config import Settings
from ecole_directe.client import EcoleDirecteClient
from state_store import StateStore


@dataclass
class SyncDelta:
    new_notes: List[Dict[str, Any]] = field(default_factory=list)
    new_messages: List[Dict[str, Any]] = field(default_factory=list)
    new_absences: List[Dict[str, Any]] = field(default_factory=list)
    cancelled_courses: List[Dict[str, Any]] = field(default_factory=list)
    timeline_events: List[Dict[str, Any]] = field(default_factory=list)

    def has_updates(self) -> bool:
        return any(
            (
                self.new_notes,
                self.new_messages,
                self.new_absences,
                self.cancelled_courses,
                self.timeline_events,
            )
        )


class EcoleDirecteService:
    """High-level orchestration around the low-level API client."""

    def __init__(self, settings: Settings, store: StateStore) -> None:
        self.settings = settings
        self.store = store
        self.client = EcoleDirecteClient(settings, store)
        self._lock = RLock()
        self._latest: Dict[str, Any] = {
            "notes": {},
            "messages": {},
            "absences": {},
            "schedule": [],
            "timeline": [],
        }

    def ensure_ready(self) -> Dict[str, Any]:
        return self.client.ensure_authenticated()

    def _diff_items(self, key: str, items: List[Dict[str, Any]], id_attr: str, snapshot_value_attr: str) -> List[Dict[str, Any]]:
        if not items:
            return []
        previous = self.store.get_snapshot(key)
        snapshot = {}
        new_items: List[Dict[str, Any]] = []
        for item in items:
            identifier_raw = item.get(id_attr)
            if identifier_raw is None:
                continue
            identifier = str(identifier_raw)
            snapshot[identifier] = item.get(snapshot_value_attr) or datetime.utcnow().isoformat()
            if identifier not in previous:
                new_items.append(item)
        self.store.update_snapshot(key, snapshot)
        return new_items

    def _store_latest(self, category: str, content: Any) -> None:
        with self._lock:
            self._latest[category] = content
            self.store.mark_last_sync(category, datetime.utcnow().isoformat())

    def _get_latest(self, category: str, default: Any) -> Any:
        with self._lock:
            return self._latest.get(category, default) or default

    def get_latest_notes(self) -> List[Dict[str, Any]]:
        notes_data = self._get_latest("notes", {})
        return notes_data.get("notes", [])

    def get_latest_messages(self) -> List[Dict[str, Any]]:
        messages_data = self._get_latest("messages", {})
        if isinstance(messages_data, dict):
            for key in ("messages", "listeMessages", "liste"):
                if key in messages_data:
                    return messages_data[key]
        return []

    def get_latest_absences(self) -> Dict[str, Any]:
        return self._get_latest("absences", {})

    def get_latest_schedule(self) -> List[Dict[str, Any]]:
        return self._get_latest("schedule", [])

    def get_latest_timeline(self) -> List[Dict[str, Any]]:
        return self._get_latest("timeline", [])

    def sync(self) -> SyncDelta:
        account = self.ensure_ready()
        note_payload = self.client.fetch_notes()
        self._store_latest("notes", note_payload)
        notes = note_payload.get("notes", [])
        new_notes = self._diff_items("notes", notes, "id", "dateSaisie")

        messages_payload = self.client.fetch_messages()
        self._store_latest("messages", messages_payload)
        messages = self.get_latest_messages()
        new_messages = self._diff_items("messages", messages, "id", "date")

        vie_scolaire = self.client.fetch_vie_scolaire()
        self._store_latest("absences", vie_scolaire)
        absences = vie_scolaire.get("absencesRetards", [])
        new_absences = self._diff_items("absences", absences, "id", "date")

        today = date.today()
        horizon = today + timedelta(days=self.settings.schedule_window_days)
        schedule = self.client.fetch_schedule(today, horizon)
        self._store_latest("schedule", schedule)
        previous_schedule = self.store.get_snapshot("schedule")
        schedule_snapshot = {}
        cancelled_courses = []
        for course in schedule:
            course_id = str(course.get("id"))
            is_cancelled = bool(course.get("isAnnule"))
            schedule_snapshot[course_id] = is_cancelled
            if is_cancelled and not previous_schedule.get(course_id, False):
                cancelled_courses.append(course)
        self.store.update_snapshot("schedule", schedule_snapshot)

        timeline = self.client.fetch_timeline(days=self.settings.timeline_window_days)
        self._store_latest("timeline", timeline)
        timeline_diff = self._diff_items(
            "timeline",
            [
                {
                    **event,
                    "hash": f"{event.get('date')}|{event.get('typeElement')}|{event.get('idElement')}",
                }
                for event in timeline
            ],
            "hash",
            "date",
        )
        for event in timeline_diff:
            event.pop("hash", None)

        return SyncDelta(
            new_notes=new_notes,
            new_messages=new_messages,
            new_absences=new_absences,
            cancelled_courses=cancelled_courses,
            timeline_events=timeline_diff,
        )

    def pending_qcm(self) -> Optional[Dict[str, Any]]:
        return self.store.get_pending_qcm()

    def update_schedule_cache(self, schedule: List[Dict[str, Any]]) -> None:
        self._store_latest("schedule", schedule)
