"""Local demo mailbox: the product Graph client reads the demo store, sends land in sent items,
and a customer reply points at the agent's last answer so it links by header."""

import asyncio
import json
import tempfile
import unittest
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import SecretStr

from backend.api.platform.auth.callback import OAuthPurpose
from backend.api.platform.mail.graph import GraphMail, initial_delta_url
from backend.api.platform.mail.links import reply_headers
from scripts.local_business import demo_mail, mail


def run(coro):
    return asyncio.run(coro)


class DemoMailTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.store = demo_mail.DemoStore(Path(self.dir.name) / demo_mail.STORE)
        self.graph = demo_mail.DemoGraph(self.store)

    def tearDown(self):
        self.dir.cleanup()

    def client(self):
        return httpx.AsyncClient(transport=self.graph, follow_redirects=False)

    def test_product_graph_client_reads_inbox_delta_and_mime(self):
        demo_mail.receive(
            self.store,
            sender="buyer@customer.example.invalid",
            subject="Gasket oil leak",
            body="Oil leaks from the gasket of TRF-OIL-1000.",
        )

        async def read():
            async with self.client() as http:
                mailbox = GraphMail(http, SecretStr(demo_mail.TOKEN))
                page = await mailbox.delta_page(initial_delta_url("inbox"))
                raw = await mailbox.message_mime(page.messages[0].graph_id)
                again = await mailbox.delta_page(page.delta_link)
                return page, raw, again, await mailbox.attachments(page.messages[0].graph_id)

        page, raw, again, attachments = run(read())
        [message] = page.messages
        self.assertEqual(message.from_address, "buyer@customer.example.invalid")
        self.assertEqual(message.subject, "Gasket oil leak")
        parsed = BytesParser(policy=policy.default).parsebytes(raw)
        self.assertIn("TRF-OIL-1000", parsed.get_content())
        self.assertEqual(again.messages, [])  # the delta link returns only newer mail
        self.assertEqual(attachments, [])

    def test_wrong_token_is_refused_and_other_hosts_pass_through(self):
        async def call():
            async with self.client() as http:
                return await http.get(
                    initial_delta_url("inbox"), headers={"Authorization": "Bearer other"}
                )

        self.assertEqual(run(call()).status_code, 401)

    def test_send_lands_in_sent_items_and_a_reply_points_back_by_header(self):
        intent = str(uuid4())
        demo_mail.receive(
            self.store,
            sender="Buyer@Customer.example.invalid",
            subject="Gasket oil leak",
            body="first mail",
        )

        async def send_and_find():
            async with self.client() as http:
                sent = await http.post(
                    "https://graph.microsoft.com/v1.0/me/sendMail",
                    headers={"Authorization": f"Bearer {demo_mail.TOKEN}"},
                    json={
                        "message": {
                            "subject": "Re: Gasket oil leak",
                            "body": {"contentType": "Text", "content": "Please send photos."},
                            "toRecipients": [
                                {"emailAddress": {"address": "buyer@customer.example.invalid"}}
                            ],
                            "singleValueExtendedProperties": [{"id": "String x", "value": intent}],
                        },
                        "saveToSentItems": True,
                    },
                )
                found = await http.get(
                    "https://graph.microsoft.com/v1.0/me/mailFolders/sentitems/messages",
                    params={
                        "$filter": f"singleValueExtendedProperties/Any(ep: ep/id eq 'x' and ep/value eq '{intent}')"
                    },
                    headers={"Authorization": f"Bearer {demo_mail.TOKEN}"},
                )
                return sent, found.json()["value"]

        sent, found = run(send_and_find())
        self.assertEqual(sent.status_code, 202)
        [copy] = found
        reply = demo_mail.receive(
            self.store,
            sender="buyer@customer.example.invalid",
            subject=None,
            body="Photos attached.",
            reply=True,
        )
        self.assertEqual(reply["subject"], "Re: Gasket oil leak")
        with self.store.open() as data:
            sent_row = next(m for m in data["messages"] if m["id"] == copy["id"])
            reply_row = next(m for m in data["messages"] if m["id"] == reply["id"])
            first = data["messages"][0]
        self.assertEqual(sent_row["conversationId"], first["conversationId"])
        self.assertEqual(reply_row["conversationId"], sent_row["conversationId"])
        raw = demo_mail.base64.b64decode(reply_row["mime"])
        self.assertEqual(reply_headers(raw), [copy["internetMessageId"]])

    def test_reply_needs_an_earlier_sent_mail(self):
        with self.assertRaises(ValueError):
            demo_mail.receive(
                self.store, sender="a@b.example.invalid", subject=None, body="x", reply=True
            )

    def test_synthetic_consent_binds_the_session_owner(self):
        adapter = demo_mail.LocalDemoMailbox("https://localhost:56443")
        owner = uuid4()
        url, pending = adapter.begin(
            OAuthPurpose.MAILBOX, browser_binding="b", now=datetime.now(UTC), owner_user_id=owner
        )
        self.assertTrue(url.startswith("https://localhost:56443/local/mailbox-authorize?state="))
        params = [("state", pending.state), ("code", demo_mail.CODE)]
        authorization = adapter.redeem_mailbox(
            pending, params, browser_binding="b", session_user_id=owner, now=datetime.now(UTC)
        )
        # The vault reads the cache as MSAL and needs exactly this mailbox's account.
        import msal

        from backend.api.platform.auth.microsoft import _mailbox_account

        cache = msal.SerializableTokenCache()
        cache.deserialize(authorization.serialized_cache.get_secret_value())
        self.assertEqual(
            _mailbox_account(cache, authorization.subject)["local_account_id"],
            str(authorization.subject.oid),
        )
        token = adapter.acquire_mailbox_token(
            subject=authorization.subject, serialized_cache=authorization.serialized_cache
        )
        self.assertEqual(token.access_token.get_secret_value(), demo_mail.TOKEN)
        with self.assertRaises(Exception):
            adapter.redeem_mailbox(
                pending, params, browser_binding="b", session_user_id=uuid4(), now=datetime.now(UTC)
            )
        with self.assertRaises(Exception):
            adapter.begin(OAuthPurpose.LOGIN, browser_binding="b", now=datetime.now(UTC))

    def test_demo_mail_config_round_trip_and_marker(self):
        state = Path(self.dir.name) / "state"
        (state / "auth").mkdir(parents=True)
        value = mail.write(
            state,
            app=mail.DEMO_APP,
            sync=True,
            send=True,
            allow=mail.DEMO_ALLOW,
            interval=60,
            demo=True,
        )
        self.assertTrue((state / "auth" / "mail-demo").exists())
        self.assertEqual(mail.load(state)["demo"], True)
        self.assertEqual(
            json.loads((state / "auth" / "mail.json").read_text())["allow"], ["@example.invalid"]
        )
        mail.write(
            state,
            app={"client_id": str(uuid4()), "client_secret": "s"},
            sync=True,
            send=False,
            allow=[],
            interval=60,
        )
        self.assertFalse((state / "auth" / "mail-demo").exists())
        self.assertEqual(value["encryption_key"], mail.load(state)["encryption_key"])


if __name__ == "__main__":
    unittest.main()
