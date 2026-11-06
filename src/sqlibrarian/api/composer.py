"""Turns a query result into a short answer, in the question's language (SDD: bilingual
templates, no second model call, so an answer can never invent a number)."""

import re

from sqlibrarian.helpers.enums import AskOutcome, Lang

_ARABIC = re.compile(r"[؀-ۿ]")

MESSAGES = {
    AskOutcome.NO_ROWS: {
        Lang.EN: "I found no matching records.",
        Lang.AR: "لم أجد سجلات مطابقة.",
    },
    AskOutcome.REJECTED: {
        Lang.EN: "I can only answer questions that read your own records. Try rephrasing.",
        Lang.AR: "يمكنني فقط الإجابة عن أسئلة تقرأ سجلاتك. حاول إعادة صياغة السؤال.",
    },
    AskOutcome.DB_ERROR: {
        Lang.EN: "Sorry, I couldn't answer that. Try asking in a different way.",
        Lang.AR: "عذرًا، لم أتمكن من الإجابة. حاول طرح السؤال بطريقة أخرى.",
    },
    AskOutcome.MODEL_ERROR: {
        Lang.EN: "The assistant is not available right now. Please try again in a minute.",
        Lang.AR: "المساعد غير متاح الآن. حاول مرة أخرى بعد قليل.",
    },
}


def answer_language(question: str) -> Lang:
    """Arabic letters anywhere (MSA, Egyptian or mixed) -> answer in Arabic."""
    return Lang.AR if _ARABIC.search(question) else Lang.EN


def compose(question: str, outcome: AskOutcome, columns: list[str], rows: list[list]) -> str:
    lang = answer_language(question)
    if outcome in MESSAGES:
        return MESSAGES[outcome][lang]
    if len(rows) == 1 and len(columns) == 1:
        value = rows[0][0]
        if value is None:
            return "لم يُسجَّل هذا بعد." if lang is Lang.AR else "This isn't recorded yet."
        return f"الإجابة: {value}" if lang is Lang.AR else f"The answer is {value}."
    if len(rows) == 1:
        separator = "، " if lang is Lang.AR else ", "
        return separator.join(f"{c}: {v}" for c, v in zip(columns, rows[0], strict=True))
    return f"وجدت {len(rows)} نتيجة:" if lang is Lang.AR else f"I found {len(rows)} results:"
