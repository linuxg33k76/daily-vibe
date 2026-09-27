"""AI highlights — STUB, NOT IMPLEMENTED.

Idea: summarize recent entries (e.g. the last 7 days, or this day in past
years) into a few bullet-point highlights / themes / open questions.

What it will need:
  * An LLM backend: either a local model (e.g. Ollama at http://localhost:11434)
    or a hosted API with a key stored in config ([plugins.ai_highlights]
    provider/model/api_key_env) — read the key from an environment variable,
    never commit it to the journal.
  * Read access to past entries via context["journal_root"] (use
    daily_vibe.storage.Journal(context["journal_root"])).
  * Strip existing plugin blocks (daily_vibe.markers) before summarizing so it
    doesn't summarize weather lines.
  * A privacy decision: journals are personal; default should be local-only.

Enable it by adding "ai_highlights" to plugins.enabled once implemented.
"""
ID = "ai_highlights"
NAME = "AI Highlights"
TITLE = "Highlights"
VERSION = "0.0.1"
AUTHOR = "The Daily Vibe"
API_VERSION = 1
SETTINGS = [  # example schema (not used yet)
    {"key": "provider", "label": "Provider", "type": "choice", "choices": ["ollama (local)", "hosted API"],
     "default": "ollama (local)"},
    {"key": "model", "label": "Model", "type": "string", "default": "llama3.1"},
    {"key": "api_key", "label": "API key", "type": "secret", "help": "Only for a hosted API."},
]
DESCRIPTION = "(stub) AI summary of recent entries — not implemented."
IMPLEMENTED = False


def render(date, context) -> str:
    raise NotImplementedError("ai_highlights is a stub; see the module docstring")
