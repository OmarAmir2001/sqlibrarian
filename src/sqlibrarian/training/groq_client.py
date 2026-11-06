"""The one place that connects to the pair-generator model (Groq's OpenAI-compatible API).

Model name, URL and key all come from settings, so switching model or provider is a
.env change. Groq is used only on the fake schema, never on real data.
"""

import openai

from sqlibrarian.config import get_settings

# Errors after which a call is skipped (rate limit, network, a reply that isn't JSON).
CALL_ERRORS = (openai.OpenAIError, ValueError, KeyError, TypeError)


def make_client() -> openai.OpenAI:
    s = get_settings()
    if not s.groq_api_key:
        raise SystemExit("GROQ_API_KEY is not set: add it to .env (see .env.example)")
    return openai.OpenAI(base_url=s.pair_generator_base_url, api_key=s.groq_api_key)
