"""Student tab: log in, ask a question in Arabic, English or a mix, see the answer,
the SQL that ran, and the rows (Gradio 6).

    uv run --extra ui python -m sqlibrarian.ui.app        # http://localhost:7860

The UI only calls the API (settings.api_url); every rule (login, row-level security,
the guard) is enforced there. The login token lives in gr.State, i.e. per browser
session, never in a module variable shared by all users.
"""

import gradio as gr
import httpx

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.log import configure_logging, get_logger

log = get_logger(__name__)

# One shared HTTP client (no user data in it); the token is sent per request.
_client = httpx.Client(base_url=get_settings().api_url, timeout=60)

TEXT = {
    "title": "SQLibrarian · مساعد السجلات الأكاديمية",
    "intro": "Ask about your grades, GPA, credit hours, courses or plan — in Arabic, English, "
    "or both.  \nاسأل عن درجاتك أو معدلك أو ساعاتك أو مقرراتك أو خطتك، بالعربية أو الإنجليزية.",
    "username": "Student ID or TA username · رقم الطالب",
    "password": "Password · كلمة المرور",
    "login": "Log in · دخول",
    "question": "Your question · سؤالك",
    "placeholder": "e.g. What's my GPA?  ·  مثال: أنا جبت كام في Data Structures؟",
    "ask": "Ask · اسأل",
    "answer": "Answer · الإجابة",
    "sql": "The query that ran · الاستعلام المنفذ",
    "rows": "Results · النتائج",
    "logged_in": "Logged in as {role}. · تم تسجيل الدخول.",
    "need_login": "Log in first. · سجّل الدخول أولًا.",
    "empty_question": "Type a question first. · اكتب سؤالًا أولًا.",
    "bad_login": "Wrong username or password. · اسم المستخدم أو كلمة المرور غير صحيحة.",
    "expired": "Your session expired; log in again. · انتهت الجلسة، سجّل الدخول مرة أخرى.",
    "invalid": "That question can't be sent: {detail}",
    "down": "The service is not reachable right now; try again in a minute. · الخدمة غير متاحة.",
    "failed": "Something went wrong on our side (HTTP {status}). · حدث خطأ، حاول مرة أخرى.",
}


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return ""
    if isinstance(detail, list):  # FastAPI validation errors
        return "; ".join(str(d.get("msg", "")) for d in detail)
    return str(detail or "")


def login(username: str, password: str) -> tuple[str | None, str]:
    """Returns (token for gr.State, status line)."""
    if not username.strip() or not password:
        raise gr.Error(TEXT["bad_login"])
    try:
        r = _client.post("/auth/login", json={"username": username.strip(), "password": password})
    except httpx.HTTPError as e:
        log.warning("ui.api_unreachable", error=type(e).__name__)
        raise gr.Error(TEXT["down"]) from e
    if r.status_code in (401, 422):
        raise gr.Error(TEXT["bad_login"])
    if r.is_error:
        raise gr.Error(TEXT["failed"].format(status=r.status_code))
    body = r.json()
    return body["access_token"], TEXT["logged_in"].format(role=body["role"])


def ask(question: str, token: str | None) -> tuple[str, str, list[list], list[str]]:
    """Returns (answer, sql, rows, column headers)."""
    if not token:
        raise gr.Error(TEXT["need_login"])
    if not question or not question.strip():
        raise gr.Error(TEXT["empty_question"])
    try:
        r = _client.post(
            "/ask", json={"question": question}, headers={"Authorization": f"Bearer {token}"}
        )
    except httpx.HTTPError as e:
        log.warning("ui.api_unreachable", error=type(e).__name__)
        raise gr.Error(TEXT["down"]) from e
    if r.status_code == 401:
        raise gr.Error(TEXT["expired"])
    if r.status_code == 422:
        raise gr.Error(TEXT["invalid"].format(detail=_detail(r)))
    if r.is_error:
        raise gr.Error(TEXT["failed"].format(status=r.status_code))
    body = r.json()
    return body["answer"], body.get("sql") or "", body.get("rows") or [], body.get("columns") or []


def ask_and_show(question: str, token: str | None):
    """Event handler: the answer, the SQL, and the table (shown only when there are rows)."""
    answer, sql, rows, columns = ask(question, token)
    table = gr.Dataframe(value=rows, headers=columns or None, visible=bool(rows))
    return answer, sql, table


def build() -> gr.Blocks:
    with gr.Blocks(title="SQLibrarian") as demo:
        token = gr.State(None)  # per browser session, never shared
        gr.Markdown(f"# {TEXT['title']}\n{TEXT['intro']}")
        with gr.Row():
            username = gr.Textbox(label=TEXT["username"], scale=2)
            password = gr.Textbox(label=TEXT["password"], type="password", scale=2)
            login_btn = gr.Button(TEXT["login"], variant="secondary", scale=1)
        status = gr.Markdown()
        question = gr.Textbox(
            label=TEXT["question"], placeholder=TEXT["placeholder"], rtl=True, lines=2
        )
        ask_btn = gr.Button(TEXT["ask"], variant="primary")
        # plain text, never HTML: the answer contains database values
        answer = gr.Textbox(label=TEXT["answer"], rtl=True, interactive=False)
        sql = gr.Code(label=TEXT["sql"], language="sql", interactive=False)
        table = gr.Dataframe(label=TEXT["rows"], visible=False, interactive=False, wrap=True)

        login_btn.click(login, [username, password], [token, status], api_name="login")
        password.submit(login, [username, password], [token, status], api_name=False)
        ask_btn.click(ask_and_show, [question, token], [answer, sql, table], api_name="ask")
        question.submit(ask_and_show, [question, token], [answer, sql, table], api_name=False)
    return demo


def main() -> None:
    configure_logging()
    build().launch(theme=gr.themes.Soft(), server_name="127.0.0.1", server_port=7860)


if __name__ == "__main__":
    main()
