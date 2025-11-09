from __future__ import annotations


class EcoleDirecteError(Exception):
    """Base exception for the custom client."""


class AuthenticationFailed(EcoleDirecteError):
    """Raised when the provided credentials are incorrect."""


class QCMRequired(EcoleDirecteError):
    """Raised when EcoleDirecte requests the QCM challenge before issuing a token."""

    def __init__(self, question: str, choices: list[str], token: str):
        super().__init__("QCM challenge required")
        self.question = question
        self.choices = choices
        self.token = token


class SessionExpired(EcoleDirecteError):
    """Raised when the server invalidates the session token."""


class ApiRequestFailed(EcoleDirecteError):
    """Raised when a request fails with a known API message."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(f"API error ({code}): {message}")
        self.code = code
        self.message = message
