from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional


class StateStore:
    """Simple JSON-backed persistence layer (tokens, cached snapshots, etc.)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        self._state: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self._path.exists():
            return {
                "snapshots": {
                    "notes": {},
                    "messages": {},
                    "absences": {},
                    "schedule": {},
                    "timeline": {},
                },
                "last_sync": {},
            }
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            data.setdefault("snapshots", {})
            data.setdefault("last_sync", {})
            for key in ("notes", "messages", "absences", "schedule", "timeline"):
                data["snapshots"].setdefault(key, {})
            return data
        except json.JSONDecodeError:
            return {
                "snapshots": {
                    "notes": {},
                    "messages": {},
                    "absences": {},
                    "schedule": {},
                    "timeline": {},
                },
                "last_sync": {},
            }

    def _persist(self) -> None:
        self._path.write_text(json.dumps(self._state, indent=2, ensure_ascii=False), encoding="utf-8")

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._state[key] = value
            self._persist()

    # Convenience helpers -------------------------------------------------
    def get_token(self) -> Optional[str]:
        return self.get("token")

    def set_token(self, token: str) -> None:
        self.set("token", token)

    def get_cn_cv(self) -> Optional[Dict[str, str]]:
        return self.get("fa_credentials")

    def set_cn_cv(self, cn: str, cv: str) -> None:
        with self._lock:
            self._state["fa_credentials"] = {"cn": cn, "cv": cv}
            self._persist()

    def get_snapshot(self, key: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state["snapshots"].get(key, {}))

    def update_snapshot(self, key: str, data: Dict[str, Any]) -> None:
        with self._lock:
            self._state["snapshots"][key] = data
            self._persist()

    def mark_last_sync(self, key: str, timestamp_iso: str) -> None:
        with self._lock:
            self._state["last_sync"][key] = timestamp_iso
            self._persist()

    def get_last_sync(self, key: str) -> Optional[str]:
        with self._lock:
            return self._state["last_sync"].get(key)

    def set_pending_qcm(self, payload: Optional[Dict[str, Any]]) -> None:
        with self._lock:
            if payload is None:
                self._state.pop("pending_qcm", None)
            else:
                self._state["pending_qcm"] = payload
            self._persist()

    def get_pending_qcm(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._state.get("pending_qcm")

    def set_account_metadata(self, account: Dict[str, Any]) -> None:
        with self._lock:
            self._state["account"] = account
            self._persist()

    def get_account_metadata(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._state.get("account")
