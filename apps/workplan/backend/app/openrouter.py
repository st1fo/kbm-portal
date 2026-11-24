"""OpenRouter client with the same `models.generate_content` shape as google-genai.

gemini.py builds google-genai contents and config as before; this adapter translates them to an OpenRouter
chat completion with a JSON schema, so prompts, validation and fallback logic stay in one place.
Identical copy in both backends.
"""
import base64
import os
from types import SimpleNamespace

import httpx

URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"   # cheapest model that keeps the structured output reliably
DEFAULT_FALLBACK = "google/gemini-3.6-flash"   # other provider, so one outage does not stop both tries


class OpenRouterError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(f"{code} {message}")
        self.code = code


def configured():
    return bool(os.getenv("OPENROUTER_API_KEY", "").strip())


def model_name():
    return os.getenv("OPENROUTER_MODEL", "").strip() or DEFAULT_MODEL


def fallback_model():
    return os.getenv("OPENROUTER_FALLBACK_MODEL", "").strip() or DEFAULT_FALLBACK


def _part(part):
    if isinstance(part, str):
        return dict(type="text", text=part)
    if getattr(part, "text", None) is not None:
        return dict(type="text", text=part.text)
    blob = part.inline_data
    url = f"data:{blob.mime_type};base64,{base64.b64encode(blob.data).decode()}"
    if blob.mime_type.startswith("image/"):
        return dict(type="image_url", image_url=dict(url=url))
    return dict(type="file", file=dict(filename="doklad.pdf", file_data=url))


def _messages(contents, system):
    messages = [dict(role="system", content=system)]
    loose = [c for c in contents if not hasattr(c, "role")]
    if loose:  # receipt call: bare parts form one user message
        messages.append(dict(role="user", content=[_part(p) for p in loose]))
    for content in contents:
        if hasattr(content, "role"):
            messages.append(dict(role="assistant" if content.role == "model" else "user",
                                 content=[_part(p) for p in content.parts]))
    return messages


def _strict(schema):
    """Strict mode: closed objects, every field required, `X | None` as a type list (Claude rejects many optionals)."""
    if isinstance(schema, list):
        return [_strict(value) for value in schema]
    if not isinstance(schema, dict):
        return schema
    original = schema
    schema = {key: _strict(value) for key, value in schema.items() if key not in ("default", "title", "properties", "$defs")}
    for key in ("properties", "$defs"):  # maps of names, not keywords: a field called "title" stays
        if key in original:
            schema[key] = {name: _strict(value) for name, value in original[key].items()}
    options = schema.get("anyOf")
    if options and len(options) == 2 and {"type": "null"} in options:
        other = next(option for option in options if option != {"type": "null"})
        if "type" in other and "$ref" not in other and "enum" not in other:  # a nullable enum stays anyOf
            del schema["anyOf"]
            schema.update(other, type=[other["type"], "null"])
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
        schema["required"] = list(schema.get("properties", {}))
    return schema


class _Models:
    def generate_content(self, model, contents, config):
        schema = config.response_schema
        body = dict(model=model, messages=_messages(contents, config.system_instruction),
                    max_tokens=config.max_output_tokens,  # no temperature: newer Claude models reject it
                    response_format=dict(type="json_schema", json_schema=dict(
                        name=schema.__name__, strict=True, schema=_strict(schema.model_json_schema()))),
                    provider=dict(require_parameters=True))
        try:
            response = httpx.post(URL, json=body, timeout=config.http_options.timeout / 1000, headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY'].strip()}", "X-Title": "KBM portal"})
        except httpx.TimeoutException as error:
            raise OpenRouterError(504, "timed out") from error
        data = response.json() if response.content else {}
        if response.status_code >= 400 or "error" in data:
            error = data.get("error") or {}
            detail = (error.get("metadata") or {}).get("raw") or ""  # the provider's own reason, for the log
            raise OpenRouterError(int(error.get("code") or response.status_code), f"{error.get('message', response.text[:300])} {detail[:300]}")
        return SimpleNamespace(text=data["choices"][0]["message"].get("content"))


def client():
    return SimpleNamespace(models=_Models())
