"""Reusable client for an OpenAI-compatible chat completions endpoint."""

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, Self

import httpx

from backend.config import ExternalLLMSettings


class LLMError(RuntimeError):
    """Base class for deterministic external LLM failures."""


class LLMTransportError(LLMError):
    """The configured LLM endpoint could not be reached."""


class LLMTimeoutError(LLMTransportError):
    """The LLM request exceeded its configured timeout."""


class LLMHTTPError(LLMError):
    """The LLM endpoint returned a non-success status."""

    def __init__(self, status_code: int) -> None:
        """Record the failed status without exposing response or prompt content."""
        self.status_code = status_code
        super().__init__(f"LLM endpoint returned HTTP {status_code}")


class LLMProtocolError(LLMError):
    """The LLM endpoint returned an invalid or incompatible response contract."""


class LLMClient:
    """Generate text through one configured OpenAI-compatible model."""

    def __init__(
        self,
        settings: ExternalLLMSettings,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Configure the adapter without contacting or loading a model.

        Args:
            settings: Endpoint, model, credentials, and request timeout.
            http_client: Optional caller-owned async client, primarily for tests.
        """
        self._settings = settings
        self._endpoint = f"{str(settings.base_url).rstrip('/')}/chat/completions"
        self._owns_http_client = http_client is None
        self._headers: dict[str, str] = {}
        if settings.api_key is not None:
            self._headers["Authorization"] = (
                f"Bearer {settings.api_key.get_secret_value()}"
            )
        self._http_client = http_client or httpx.AsyncClient()

    async def complete(self, messages: Sequence[Mapping[str, object]]) -> str:
        """Return text from the first assistant choice.

        Messages use the OpenAI-compatible mapping shape. The adapter preserves
        standard fields such as ``role``, ``content``, and ``name`` and does not
        rewrite prompt content.

        Args:
            messages: Ordered OpenAI-compatible conversation messages.

        Returns:
            Text content of the first assistant message.

        Raises:
            LLMTimeoutError: If the request times out.
            LLMTransportError: If the endpoint cannot be reached.
            LLMHTTPError: If the endpoint returns a non-2xx response.
            LLMProtocolError: If the response is malformed or incompatible.
        """
        request_messages = [dict(message) for message in messages]
        try:
            response = await self._http_client.post(
                self._endpoint,
                json={"model": self._settings.model, "messages": request_messages},
                headers=self._headers,
                timeout=self._settings.timeout_seconds,
            )
        except httpx.TimeoutException as error:
            raise LLMTimeoutError("LLM request timed out") from error
        except httpx.TransportError as error:
            raise LLMTransportError("LLM transport failed") from error

        if not response.is_success:
            raise LLMHTTPError(response.status_code)

        try:
            payload: Any = response.json()
        except ValueError as error:
            raise LLMProtocolError("LLM response is not valid JSON") from error
        return self._parse_content(payload)

    @staticmethod
    def _parse_content(payload: Any) -> str:
        if not isinstance(payload, dict):
            raise LLMProtocolError("LLM response must be an object")
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMProtocolError("LLM response must contain at least one choice")
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise LLMProtocolError("LLM choice must be an object")
        message = first_choice.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            raise LLMProtocolError("LLM choice must contain an assistant message")
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMProtocolError("LLM assistant content must be text")
        return content

    async def close(self) -> None:
        """Close the internally owned HTTP client; caller-owned clients stay open."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def __aenter__(self) -> Self:
        """Return the configured client for an async managed block."""
        return self

    async def __aexit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Release the internally owned HTTP client."""
        await self.close()
