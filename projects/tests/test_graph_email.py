"""Microsoft 365 email (Step 15d). The tests talk to a small fake "Microsoft" running on this computer,
so the real network code runs, but nothing leaves the machine."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import StringIO
from unittest import mock
from urllib.parse import parse_qs

from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMultiAlternatives
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, override_settings

from config import graph_email
from config.graph_email import GraphEmailBackend, GraphEmailError

SECRET = "super-secret-value-123"


class FakeMicrosoft:
    """Answers like the sign-in service and Graph. 'plan' lists what to answer, in order, for sendMail."""

    def __init__(self):
        self.requests = []  # (path, headers, body)
        self.send_plan = []  # for example [(429, {"Retry-After": "0"}, {}), (202, {}, None)]
        self.token_calls = 0
        self.token_refusal = None  # (status, body) to answer at the sign-in step, like a wrong secret
        self.expires_in = 3600
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                fake.requests.append((self.path, dict(self.headers), body))
                if self.path.endswith("/oauth2/v2.0/token"):
                    fake.token_calls += 1
                    if fake.token_refusal:
                        return self.reply(*fake.token_refusal)
                    self.reply(200, {"access_token": f"token-{fake.token_calls}", "expires_in": fake.expires_in})
                else:
                    status, headers, payload = fake.send_plan.pop(0) if fake.send_plan else (202, {}, None)
                    self.reply(status, payload, headers)

            def reply(self, status, payload, headers=None):
                data = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    @property
    def sends(self):
        return [r for r in self.requests if r[0].endswith("/sendMail")]


class GraphTestCase(SimpleTestCase):
    def setUp(self):
        graph_email.forget_token()
        self.microsoft = FakeMicrosoft()
        self.addCleanup(self.microsoft.stop)
        self.addCleanup(graph_email.forget_token)
        change = override_settings(
            EMAIL_BACKEND="config.graph_email.GraphEmailBackend",
            GRAPH_TENANT_ID="tenant-1234",
            GRAPH_CLIENT_ID="client-5678",
            GRAPH_CLIENT_SECRET=SECRET,
            GRAPH_SENDER="it-projects@company.mr",
            GRAPH_AUTHORITY=self.microsoft.url,
            GRAPH_API_URL=self.microsoft.url + "/v1.0",
            GRAPH_SAVE_TO_SENT_ITEMS=False,
        )
        change.enable()
        self.addCleanup(change.disable)

    def message(self, **extra):
        message = EmailMultiAlternatives(
            "Your tasks: 1 overdue", "plain text", to=["Sara Ben <sara@example.com>"], **extra
        )
        message.attach_alternative("<p>html text</p>", "text/html")
        return message


class SendingTests(GraphTestCase):
    def test_sends_with_a_token_to_the_sender_mailbox(self):
        self.assertEqual(self.message().send(), 1)

        token_path, _, token_body = self.microsoft.requests[0]
        self.assertEqual(token_path, "/tenant-1234/oauth2/v2.0/token")
        form = {key: values[0] for key, values in parse_qs(token_body).items()}
        self.assertEqual(form["grant_type"], "client_credentials")
        self.assertEqual(form["client_id"], "client-5678")
        self.assertEqual(form["client_secret"], SECRET)
        self.assertEqual(form["scope"], "https://graph.microsoft.com/.default")

        path, headers, body = self.microsoft.sends[0]
        self.assertEqual(path, "/v1.0/users/it-projects@company.mr/sendMail")
        self.assertEqual(headers["Authorization"], "Bearer token-1")
        payload = json.loads(body)
        self.assertFalse(payload["saveToSentItems"])
        message = payload["message"]
        self.assertEqual(message["subject"], "Your tasks: 1 overdue")
        self.assertEqual(message["body"], {"contentType": "HTML", "content": "<p>html text</p>"})
        sara = {"emailAddress": {"address": "sara@example.com", "name": "Sara Ben"}}
        self.assertEqual(message["toRecipients"], [sara])

    def test_plain_email_without_html_version(self):
        mail.EmailMessage("Hello", "just text", to=["a@example.com"]).send()
        body = json.loads(self.microsoft.sends[0][2])["message"]["body"]
        self.assertEqual(body, {"contentType": "Text", "content": "just text"})

    def test_copies_and_reply_to(self):
        self.message(cc=["c@example.com"], bcc=["b@example.com"], reply_to=["r@example.com"]).send()
        message = json.loads(self.microsoft.sends[0][2])["message"]
        self.assertEqual(message["ccRecipients"][0]["emailAddress"]["address"], "c@example.com")
        self.assertEqual(message["bccRecipients"][0]["emailAddress"]["address"], "b@example.com")
        self.assertEqual(message["replyTo"][0]["emailAddress"]["address"], "r@example.com")

    def test_arabic_and_french_texts_arrive_intact(self):
        mail.EmailMessage("مهامك: 1 متأخرة — Tâches", "نص", to=["a@example.com"]).send()
        message = json.loads(self.microsoft.sends[0][2])["message"]
        self.assertEqual(message["subject"], "مهامك: 1 متأخرة — Tâches")
        self.assertEqual(message["body"]["content"], "نص")

    def test_the_token_is_reused_then_renewed(self):
        self.message().send()
        self.message().send()
        self.assertEqual(self.microsoft.token_calls, 1)
        # a token that is about to expire is replaced
        self.microsoft.expires_in = 30
        graph_email.forget_token()
        self.message().send()
        self.message().send()
        self.assertEqual(self.microsoft.token_calls, 3)

    def test_save_to_sent_items_can_be_turned_on(self):
        with override_settings(GRAPH_SAVE_TO_SENT_ITEMS=True):
            self.message().send()
        self.assertTrue(json.loads(self.microsoft.sends[0][2])["saveToSentItems"])

    def test_email_without_a_recipient_is_skipped(self):
        self.assertEqual(GraphEmailBackend().send_messages([mail.EmailMessage("x", "y", to=[])]), 0)
        self.assertEqual(self.microsoft.requests, [])

    def test_attachments_are_refused_clearly(self):
        message = mail.EmailMessage("x", "y", to=["a@example.com"])
        message.attach("a.txt", "data", "text/plain")
        with self.assertLogs("config.graph_email", level="ERROR"):
            with self.assertRaisesMessage(GraphEmailError, "Attachments are not supported"):
                message.send()


class WhenThingsGoWrongTests(GraphTestCase):
    def test_too_many_requests_waits_and_tries_again(self):
        too_many = {"error": {"code": "TooManyRequests", "message": "slow"}}
        self.microsoft.send_plan = [(429, {"Retry-After": "0"}, too_many)]
        with mock.patch("config.graph_email.time.sleep") as sleep:
            self.assertEqual(self.message().send(), 1)
        sleep.assert_called_once_with(0.0)
        self.assertEqual(len(self.microsoft.sends), 2)

    def test_wait_is_capped(self):
        self.microsoft.send_plan = [(503, {"Retry-After": "9999"}, {})]
        with mock.patch("config.graph_email.time.sleep") as sleep:
            self.message().send()
        sleep.assert_called_once_with(graph_email.MAX_WAIT_SECONDS)

    def test_an_old_token_is_replaced_once(self):
        self.microsoft.send_plan = [(401, {}, {"error": {"code": "InvalidAuthenticationToken", "message": "expired"}})]
        self.assertEqual(self.message().send(), 1)
        self.assertEqual(self.microsoft.token_calls, 2)
        self.assertEqual(self.microsoft.sends[1][1]["Authorization"], "Bearer token-2")

    def test_a_refusal_is_explained_without_the_secret(self):
        self.microsoft.send_plan = [(403, {}, {"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})]
        with self.assertLogs("config.graph_email", level="ERROR") as logs:
            with self.assertRaises(GraphEmailError) as caught:
                self.message().send()
        self.assertIn("HTTP 403 ErrorAccessDenied: Access is denied.", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(SECRET, "\n".join(logs.output))

    def test_fail_silently_swallows_the_error(self):
        self.microsoft.send_plan = [(403, {}, {"error": {"code": "ErrorAccessDenied", "message": "no"}})]
        with self.assertLogs("config.graph_email", level="ERROR"):
            self.assertEqual(self.message().send(fail_silently=True), 0)

    def test_unexpected_success_code_is_an_error(self):
        self.microsoft.send_plan = [(200, {}, {})]
        with self.assertLogs("config.graph_email", level="ERROR"):
            with self.assertRaisesMessage(GraphEmailError, "instead of 202"):
                self.message().send()

    def test_wrong_client_secret_says_so_and_never_shows_it(self):
        self.microsoft.token_refusal = (
            401,
            {"error": "invalid_client", "error_description": "AADSTS7000215: Invalid client secret provided."},
        )
        with self.assertLogs("config.graph_email", level="ERROR") as logs:
            with self.assertRaises(GraphEmailError) as caught:
                self.message().send()
        self.assertIn("Could not get an access token: HTTP 401 invalid_client: AADSTS7000215", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(SECRET, "\n".join(logs.output))
        self.assertEqual(self.microsoft.sends, [])  # no email is attempted without a token

    def test_microsoft_unreachable(self):
        with override_settings(GRAPH_AUTHORITY="http://127.0.0.1:9"):  # nothing listens on port 9
            with self.assertLogs("config.graph_email", level="ERROR"):
                with self.assertRaisesMessage(GraphEmailError, "Could not reach Microsoft"):
                    self.message().send()


class ConfigurationTests(GraphTestCase):
    def test_missing_settings_are_named_but_not_their_values(self):
        with override_settings(GRAPH_CLIENT_SECRET="", GRAPH_SENDER=""):
            with self.assertRaises(ImproperlyConfigured) as caught:
                self.message().send()
        self.assertIn("GRAPH_CLIENT_SECRET, GRAPH_SENDER", str(caught.exception))
        self.assertEqual(self.microsoft.requests, [])

    def test_missing_settings_with_fail_silently_send_nothing(self):
        with override_settings(GRAPH_TENANT_ID=""):
            self.assertEqual(self.message().send(fail_silently=True), 0)

    def test_odd_tenant_or_sender_is_refused(self):
        with override_settings(GRAPH_TENANT_ID="../evil"):
            with self.assertRaises(ImproperlyConfigured):
                self.message().send()
        with override_settings(GRAPH_SENDER="not-an-address"):
            with self.assertRaises(ImproperlyConfigured):
                self.message().send()


class TestEmailCommandTests(GraphTestCase):
    def test_reports_success(self):
        output = StringIO()
        call_command("send_test_email", "boss@company.mr", stdout=output)
        self.assertIn("GraphEmailBackend", output.getvalue())
        self.assertIn("Test email accepted for boss@company.mr.", output.getvalue())
        self.assertEqual(len(self.microsoft.sends), 1)

    def test_reports_failure_in_plain_words(self):
        self.microsoft.send_plan = [(403, {}, {"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})]
        with self.assertLogs("config.graph_email", level="ERROR"):
            with self.assertRaisesMessage(CommandError, "The test email could not be sent: HTTP 403 ErrorAccessDenied"):
                call_command("send_test_email", "boss@company.mr", stdout=StringIO())
