from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import Settings
from state_store import StateStore

from .exceptions import (
    ApiRequestFailed,
    AuthenticationFailed,
    QCMRequired,
    SessionExpired,
)


class EcoleDirecteClient:
    BASE_URL = "https://api.ecoledirecte.com"
    API_VERSION = "4.75.0"

    def __init__(self, settings: Settings, state: StateStore) -> None:
        self.settings = settings
        self.state = state
        self._client = httpx.Client(
            timeout=settings.http_timeout_seconds,
            headers={
                "User-Agent": settings.ecole_directe_user_agent,
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        )
        self._token: Optional[str] = state.get_token()
        self._account: Optional[Dict[str, Any]] = state.get_account_metadata()
        self._gtk_cookie: Optional[str] = None

    # ------------------------------------------------------------------ #
    # Helpers
    def _build_url(self, path: str, method: str = "get", extra_query: Optional[Dict[str, Any]] = None) -> str:
        query_params: Dict[str, Any] = {"verbe": method.lower()}
        if extra_query:
            for key, value in extra_query.items():
                if value is not None:
                    query_params[key] = value
        query = httpx.QueryParams(query_params)
        return f"{self.BASE_URL}{path}?{query}"

    def _build_headers(self, include_token: bool = True, token_override: Optional[str] = None) -> Dict[str, str]:
        headers = dict(self._client.headers)
        if include_token:
            token = token_override or self._token
            if token:
                headers["X-Token"] = token
        return headers

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def _post(
        self,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        method: str = "get",
        include_token: bool = True,
        extra_query: Optional[Dict[str, Any]] = None,
        token_override: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        allowed_codes: Optional[set[int]] = None,
    ) -> Dict[str, Any]:
        allowed_codes = allowed_codes or set()
        url = self._build_url(path, method=method, extra_query=extra_query)
        body = {}
        if payload is not None:
            body["data"] = json.dumps(payload, ensure_ascii=False)
        request_headers = self._build_headers(include_token=include_token, token_override=token_override)
        if headers:
            request_headers.update(headers)
        response = self._client.post(url, data=body, headers=request_headers)
        response.raise_for_status()
        payload = response.json()
        code = payload.get("code", 200)
        message = payload.get("message") or ""
        if code == 520:
            raise SessionExpired("Token expired or invalid")
        if code == 505:
            raise AuthenticationFailed(message or "Identifiants invalides")
        if code not in allowed_codes and code != 200:
            raise ApiRequestFailed(message or "Erreur inconnue", code=code)
        new_token = payload.get("token")
        if new_token:
            self._set_token(new_token)
        return payload

    def _set_token(self, token: str) -> None:
        self._token = token
        self.state.set_token(token)

    def _set_account(self, account: Dict[str, Any]) -> None:
        self._account = account
        self.state.set_account_metadata(account)

    def _decode_b64(self, value: str) -> str:
        return base64.b64decode(value).decode("utf-8")

    # ------------------------------------------------------------------ #
    # Authentication
    def _fetch_gtk_cookie(self) -> str:
        response = self._client.get(
            f"{self.BASE_URL}/v3/login.awp",
            params={"gtk": "1", "v": self.API_VERSION},
        )
        response.raise_for_status()
        gtk = response.cookies.get("GTK")
        if not gtk:
            raise RuntimeError("Impossible de récupérer le cookie GTK.")
        self._gtk_cookie = gtk
        return gtk

    def _request_qcm(self, temp_token: str) -> Dict[str, Any]:
        if not temp_token:
            raise RuntimeError("Token temporaire manquant pour la double authentification.")
        headers: Dict[str, str] = {}
        if self._gtk_cookie:
            headers["X-Gtk"] = self._gtk_cookie
        payload = self._post(
            "/v3/connexion/doubleauth.awp",
            payload={},
            include_token=True,
            token_override=temp_token,
            headers=headers if headers else None,
        )
        data = payload["data"]
        question = self._decode_b64(data["question"])
        propositions = data.get("propositions", [])
        decoded_choices = [
            {
                "encoded": choice,
                "decoded": self._decode_b64(choice),
                "index": idx,
            }
            for idx, choice in enumerate(propositions)
        ]
        qcm_info = {
            "token": temp_token,
            "question": question,
            "choices": decoded_choices,
            "received_at": datetime.utcnow().isoformat(),
        }
        self.state.set_pending_qcm(qcm_info)
        return qcm_info

    def submit_qcm_answer(self, answer: str | int) -> Dict[str, Any]:
        pending = self.state.get_pending_qcm()
        if not pending:
            raise ValueError("Aucun QCM en attente.")

        choice_encoded = None
        if isinstance(answer, int):
            for choice in pending["choices"]:
                if choice["index"] == answer:
                    choice_encoded = choice["encoded"]
                    break
        else:
            for choice in pending["choices"]:
                if choice["decoded"].lower().strip() == str(answer).lower().strip():
                    choice_encoded = choice["encoded"]
                    break

        if not choice_encoded:
            raise ValueError("Réponse QCM inconnue. Fournissez l'index ou le libellé exact.")

        result = self._post(
            "/v3/connexion/doubleauth.awp",
            payload={"choix": choice_encoded},
            include_token=True,
            token_override=pending["token"],
            method="post",
        )
        data = result["data"]
        cn = data.get("cn")
        cv = data.get("cv")
        if not cn or not cv:
            raise RuntimeError("La réponse QCM est invalide.")
        self.state.set_cn_cv(cn, cv)
        self.state.set_pending_qcm(None)
        return {"cn": cn, "cv": cv}

    def login(self, force: bool = False) -> Dict[str, Any]:
        if self._token and self._account and not force:
            return self._account

        gtk = self._fetch_gtk_cookie()
        payload = {
            "identifiant": self.settings.ecole_directe_username,
            "motdepasse": self.settings.ecole_directe_password,
            "isReLogin": False,
            "uuid": "",
        }
        fa_credentials = self.state.get_cn_cv()
        if fa_credentials:
            payload["fa"] = [fa_credentials]
        headers = {"X-Gtk": gtk}

        try:
            response = self._post(
                "/v3/login.awp",
                payload=payload,
                include_token=False,
                headers=headers,
                extra_query={"v": self.API_VERSION},
                method="post",
                allowed_codes={250},
            )
        except ApiRequestFailed as exc:
            if "mot de passe" in exc.message.lower():
                raise AuthenticationFailed(exc.message)
            raise
        except SessionExpired:
            # Should not happen on login; fall back to fresh attempt
            response = self._post(
                "/v3/login.awp",
                payload=payload,
                include_token=False,
                headers=headers,
                extra_query={"v": self.API_VERSION},
                method="post",
                allowed_codes={250},
            )

        code = response.get("code", 200)
        if code == 250:
            temp_token = response.get("token") or self._token
            if not temp_token:
                raise RuntimeError("Token de double authentification non disponible.")
            qcm_info = self._request_qcm(temp_token)
            raise QCMRequired(
                qcm_info["question"],
                [choice["decoded"] for choice in qcm_info["choices"]],
                temp_token,
            )

        account = response["data"]["accounts"][self.settings.account_index]
        self._set_token(response["token"])
        self._set_account(account)
        return account

    def ensure_authenticated(self) -> Dict[str, Any]:
        if not self._token or not self._account:
            return self.login()
        return self._account

    # ------------------------------------------------------------------ #
    # Public data fetchers
    def _student_path(self, suffix: str) -> str:
        account = self.ensure_authenticated()
        return f"/v3/eleves/{account['id']}/{suffix}"

    def fetch_timeline(self, days: int) -> List[Dict[str, Any]]:
        account = self.ensure_authenticated()
        payload = {"dateDebut": (date.today() - timedelta(days=days)).isoformat()}
        result = self._request_with_reauth(
            path=f"/v3/eleves/{account['id']}/timeline.awp",
            payload=payload,
        )
        return result["data"]

    def fetch_notes(self) -> Dict[str, Any]:
        account = self.ensure_authenticated()
        payload = {
            "anneeScolaire": account.get("anneeScolaireCourante", ""),
        }
        result = self._request_with_reauth(
            path=f"/v3/eleves/{account['id']}/notes.awp",
            payload=payload,
        )
        return result["data"]

    def fetch_vie_scolaire(self) -> Dict[str, Any]:
        account = self.ensure_authenticated()
        result = self._request_with_reauth(
            path=f"/v3/eleves/{account['id']}/viescolaire.awp",
        )
        return result["data"]

    def fetch_messages(self, mode: str = "destinataire") -> Dict[str, Any]:
        account = self.ensure_authenticated()
        payload = {
            "anneeMessages": account.get("anneeScolaireCourante", ""),
            "order": "desc",
        }
        extra_query = {"mode": mode}
        result = self._request_with_reauth(
            path=f"/v3/eleves/{account['id']}/messages.awp",
            payload=payload,
            extra_query=extra_query,
        )
        return result["data"]

    def fetch_message_detail(self, message_id: int, mode: str = "destinataire") -> Dict[str, Any]:
        account = self.ensure_authenticated()
        payload = {
            "anneeMessages": account.get("anneeScolaireCourante", ""),
        }
        result = self._request_with_reauth(
            path=f"/v3/eleves/{account['id']}/messages/{message_id}.awp",
            payload=payload,
            extra_query={"mode": mode},
        )
        return result["data"]

    def fetch_schedule(self, start: date, end: date, with_gaps: bool = False) -> List[Dict[str, Any]]:
        account = self.ensure_authenticated()
        payload = {
            "dateDebut": start.isoformat(),
            "dateFin": end.isoformat(),
            "avecTrous": with_gaps,
        }
        result = self._request_with_reauth(
            path=f"/v3/E/{account['id']}/emploidutemps.awp",
            payload=payload,
        )
        return result["data"]

    # ------------------------------------------------------------------ #
    def _request_with_reauth(
        self,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
        method: str = "get",
        extra_query: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            return self._post(
                path,
                payload=payload,
                method=method,
                extra_query=extra_query,
            )
        except SessionExpired:
            self.login(force=True)
            return self._post(
                path,
                payload=payload,
                method=method,
                extra_query=extra_query,
            )
