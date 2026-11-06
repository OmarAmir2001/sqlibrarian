"""The ONE place that builds what the model sees.

Training, evaluation and the API all import build_messages(), so the model
always gets exactly the same layout.
"""

SYSTEM = "Write one PostgreSQL SELECT query that answers the question. Return only the SQL."


def build_messages(schema_text: str, question: str, sql: str | None = None) -> list[dict]:
    """Return chat messages. Leave sql=None for evaluation and the API."""
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Database:\n{schema_text}\n\nQuestion: {question}"},
    ]
    if sql is not None:
        messages.append({"role": "assistant", "content": sql.strip()})
    return messages
