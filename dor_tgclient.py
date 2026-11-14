# -*- coding: utf-8 -*-
"""
اتصال به تلگرام با Telethon (MTProto) — دقیقاً همان روش ربات قبلی؛ بدون پراکسی و بدون هیچ فایل اضافه.

Hugging Face آدرس api.telegram.org را بسته است، ولی Telethon مستقیم به IPهای خود تلگرام وصل می‌شود.
این ماژول همان چند متد Bot API را که ربات استفاده می‌کند (sendMessage, copyMessage, ...) با Telethon
انجام می‌دهد و پیام‌های دریافتی را به همان قالب Bot API تبدیل می‌کند؛ پس منطق ربات دست نخورده است.

Needs TG_API_ID / TG_API_HASH (my.telegram.org) + BOT_TOKEN.
"""
import asyncio
import io
import logging
import re
import threading
import time

from telethon import Button, TelegramClient, errors, events, utils
from telethon.extensions import html as tl_html
from telethon.sessions import StringSession
from telethon.tl import functions, types

log = logging.getLogger("tgclient")

STATE = {"status": "—", "me": None}


class _Err(Exception):
    def __init__(self, desc, code=0):
        super().__init__(desc)
        self.code = code


class Bridge:
    def __init__(self, api_id, api_hash, token, on_update, error_cls=None,
                 load_session=None, save_session=None, get_peer=None, set_peer=None):
        self.api_id = int(api_id)
        self.api_hash = api_hash
        self.token = token
        self.bot_id = int(token.split(":")[0]) if ":" in token else 0
        self.on_update = on_update
        self.Err = error_cls or _Err
        self.load_session = load_session or (lambda: "")
        self.save_session = save_session or (lambda s: None)
        self.get_peer = get_peer or (lambda uid: None)
        self.set_peer = set_peer or (lambda uid, h: None)
        self.loop = None
        self.client = None
        self.ready = threading.Event()
        self.update_id = int(time.time()) * 10

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        threading.Thread(target=self._thread, daemon=True, name="telethon").start()

    def _thread(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        delay = 5
        while True:
            try:
                self.loop.run_until_complete(self._run())
                delay = 5
            except (errors.AccessTokenInvalidError, errors.AccessTokenExpiredError, errors.ApiIdInvalidError) as e:
                log.error("telethon: %s — %s", STATE["status"], e)
                delay = 120
            except Exception as e:
                STATE["status"] = f"❌ {type(e).__name__}: {e}"
                log.error("telethon: %s", e)
                delay = min(delay * 2, 120)
            self.ready.clear()
            time.sleep(delay)

    async def _login(self, session_str):
        c = TelegramClient(StringSession(session_str), self.api_id, self.api_hash,
                           connection_retries=None, retry_delay=5, auto_reconnect=True,
                           request_retries=3, flood_sleep_threshold=60)
        await c.connect()
        if session_str:
            try:
                if await c.is_user_authorized():
                    me = await c.get_me()
                    if me and me.id == self.bot_id:
                        return c, me
            except Exception as e:
                log.warning("saved session unusable (%s) — logging in again", e)
            await c.disconnect()
            return await self._login("")
        await c.start(bot_token=self.token)
        me = await c.get_me()
        return c, me

    async def _run(self):
        STATE["status"] = "⏳ در حال اتصال به تلگرام (MTProto)…"
        try:
            c, me = await self._login(self.load_session() or "")
        except (errors.AccessTokenInvalidError, errors.AccessTokenExpiredError):
            STATE["status"] = "❌ BOT_TOKEN اشتباه است یا باطل شده (از @BotFather دوباره بگیرید)"
            raise
        except errors.ApiIdInvalidError:
            STATE["status"] = "❌ TG_API_ID / TG_API_HASH اشتباه است"
            raise
        self.client = c
        STATE["me"] = {"id": me.id, "is_bot": True, "first_name": me.first_name or "", "username": me.username or ""}
        try:
            self.save_session(c.session.save())
        except Exception as e:
            log.warning("session save failed: %s", e)

        c.add_event_handler(self._on_message, events.NewMessage(incoming=True))
        c.add_event_handler(self._on_callback, events.CallbackQuery())
        c.add_event_handler(self._on_raw, events.Raw(types.UpdateBotStopped))
        STATE["status"] = "✅ متصل (MTProto مستقیم با Telethon، بدون پراکسی)"
        log.info("telethon connected as @%s", me.username)
        self.ready.set()
        await c.run_until_disconnected()
        STATE["status"] = "⚠️ اتصال قطع شد — اتصال دوباره…"

    # ------------------------------------------------------------------ sync entry points
    def _run_coro(self, coro, timeout):
        if not self.ready.wait(timeout=min(timeout, 60)):
            coro.close()
            raise self.Err(f"Telegram not connected yet ({STATE['status']})", 0)
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return fut.result(timeout=timeout)
        except self.Err:
            raise
        except Exception as e:
            fut.cancel()
            raise self._map_error(e)

    def call(self, method, data=None, files=None, timeout=60):
        data = dict(data or {})
        fn = getattr(self, "m_" + method, None)
        if fn is None:
            if method in ("deleteWebhook", "setWebhook"):
                return True
            if method == "getWebhookInfo":
                return {"url": "", "pending_update_count": 0}
            raise self.Err(f"method {method} not supported", 400)
        for attempt in range(3):
            try:
                return self._run_coro(fn(data, files), timeout)
            except self.Err as e:
                if getattr(e, "retry_after", 0) and attempt < 2:
                    time.sleep(e.retry_after + 1)
                    continue
                raise

    def download(self, file_id, timeout=180):
        return self._run_coro(self._download(file_id), timeout)

    # ------------------------------------------------------------------ errors
    def _map_error(self, e):
        if isinstance(e, self.Err):
            return e
        if isinstance(e, errors.FloodWaitError):
            err = self.Err(f"Too Many Requests: retry after {e.seconds}", 429)
            err.retry_after = e.seconds if e.seconds <= 120 else 0
            return err
        if isinstance(e, (errors.UserIsBlockedError, errors.InputUserDeactivatedError)):
            return self.Err("Forbidden: bot was blocked by the user", 403)
        if isinstance(e, errors.MessageNotModifiedError):
            return self.Err("Bad Request: message is not modified", 400)
        if isinstance(e, (errors.PeerIdInvalidError, errors.ChatIdInvalidError)):
            return self.Err("Bad Request: chat not found", 400)
        if isinstance(e, errors.RPCError):
            return self.Err(f"Bad Request: {e.message if hasattr(e, 'message') else e}", 400)
        if isinstance(e, ValueError) and "entity" in str(e).lower():
            return self.Err("Bad Request: chat not found (user must start the bot first)", 400)
        if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
            return self.Err("timeout", 0)
        return self.Err(f"{type(e).__name__}: {e}", 0)

    # ------------------------------------------------------------------ peers
    async def _peer(self, chat_id):
        chat_id = int(chat_id)
        try:
            return await self.client.get_input_entity(chat_id)
        except Exception:
            pass
        h = self.get_peer(chat_id)
        if h is not None and chat_id > 0:
            return types.InputPeerUser(chat_id, int(h))
        return await self.client.get_input_entity(chat_id)   # raises → "chat not found"

    def _remember(self, user):
        try:
            if user is not None and getattr(user, "access_hash", None) is not None and not getattr(user, "min", False):
                if self.get_peer(user.id) != user.access_hash:
                    self.set_peer(user.id, user.access_hash)
        except Exception:
            pass

    # ------------------------------------------------------------------ formatting
    async def _input_user(self, uid):
        p = await self._peer(uid)
        if isinstance(p, types.InputPeerUser):
            return types.InputUser(p.user_id, p.access_hash)
        raise ValueError("not a user")

    async def _fix_mentions(self, ents):
        """tg://user?id=N links → real mentions (clickable even without username)."""
        out = []
        for e in ents or []:
            if isinstance(e, types.MessageEntityTextUrl):
                m = re.match(r"^tg://user\?id=(\d+)$", e.url or "")
                if m:
                    try:
                        out.append(types.InputMessageEntityMentionName(e.offset, e.length,
                                                                       await self._input_user(int(m.group(1)))))
                        continue
                    except Exception:
                        pass
            elif isinstance(e, types.MessageEntityMentionName):
                try:
                    out.append(types.InputMessageEntityMentionName(e.offset, e.length, await self._input_user(e.user_id)))
                    continue
                except Exception:
                    continue
            out.append(e)
        return out

    async def _text_and_entities(self, text, parse_mode=None, entities=None):
        text = text or ""
        if entities:
            ents = [x for x in (self._ent_from_api(e) for e in entities) if x is not None]
        elif (parse_mode or "").upper() == "HTML":
            text, ents = tl_html.parse(text)
        else:
            ents = []
        return text, await self._fix_mentions(ents)

    _E2T = {"bold": types.MessageEntityBold, "italic": types.MessageEntityItalic,
            "underline": types.MessageEntityUnderline, "strikethrough": types.MessageEntityStrike,
            "spoiler": types.MessageEntitySpoiler, "code": types.MessageEntityCode,
            "url": types.MessageEntityUrl, "mention": types.MessageEntityMention,
            "hashtag": types.MessageEntityHashtag, "cashtag": types.MessageEntityCashtag,
            "bot_command": types.MessageEntityBotCommand, "email": types.MessageEntityEmail,
            "phone_number": types.MessageEntityPhone, "bank_card": types.MessageEntityBankCard}

    def _ent_from_api(self, e):
        t, o, n = e.get("type"), int(e.get("offset", 0)), int(e.get("length", 0))
        if t in self._E2T:
            return self._E2T[t](o, n)
        if t == "pre":
            return types.MessageEntityPre(o, n, e.get("language", "") or "")
        if t == "text_link":
            return types.MessageEntityTextUrl(o, n, e.get("url", ""))
        if t == "text_mention":
            return types.MessageEntityMentionName(o, n, int(e.get("user", {}).get("id", 0)))
        if t in ("blockquote", "expandable_blockquote"):
            return types.MessageEntityBlockquote(o, n, collapsed=(t == "expandable_blockquote") or None)
        if t == "custom_emoji":
            return types.MessageEntityCustomEmoji(o, n, int(e.get("custom_emoji_id", 0)))
        return None

    def _ent_to_api(self, e):
        o, n = e.offset, e.length
        rev = {v: k for k, v in self._E2T.items()}
        if type(e) in rev:
            return {"type": rev[type(e)], "offset": o, "length": n}
        if isinstance(e, types.MessageEntityPre):
            return {"type": "pre", "offset": o, "length": n, "language": e.language or ""}
        if isinstance(e, types.MessageEntityTextUrl):
            return {"type": "text_link", "offset": o, "length": n, "url": e.url}
        if isinstance(e, types.MessageEntityMentionName):
            return {"type": "text_mention", "offset": o, "length": n, "user": {"id": e.user_id}}
        if isinstance(e, types.MessageEntityBlockquote):
            return {"type": "expandable_blockquote" if e.collapsed else "blockquote", "offset": o, "length": n}
        if isinstance(e, types.MessageEntityCustomEmoji):
            return {"type": "custom_emoji", "offset": o, "length": n, "custom_emoji_id": str(e.document_id)}
        return None

    def _markup(self, kb):
        if not kb:
            return None
        if isinstance(kb, str):
            import json
            kb = json.loads(kb)
        if kb.get("remove_keyboard"):
            return Button.clear()
        if "inline_keyboard" in kb:
            rows = []
            for row in kb["inline_keyboard"]:
                r = []
                for b in row:
                    if b.get("url"):
                        r.append(Button.url(b["text"], b["url"]))
                    else:
                        r.append(Button.inline(b["text"], (b.get("callback_data") or "").encode("utf-8")))
                rows.append(r)
            return self.client.build_reply_markup(rows)
        if "keyboard" in kb:
            rows = []
            first = True
            for row in kb["keyboard"]:
                r = []
                for b in row:
                    t = b["text"] if isinstance(b, dict) else str(b)
                    if first:
                        r.append(Button.text(t, resize=bool(kb.get("resize_keyboard")),
                                             single_use=bool(kb.get("one_time_keyboard")) or None,
                                             persistent=bool(kb.get("is_persistent")) or None,
                                             placeholder=kb.get("input_field_placeholder")))
                        first = False
                    else:
                        r.append(Button.text(t))
                rows.append(r)
            return self.client.build_reply_markup(rows)
        return None

    @staticmethod
    def _sent(m, chat_id):
        return {"message_id": m.id, "chat": {"id": int(chat_id), "type": "private"},
                "date": int(m.date.timestamp()) if m.date else int(time.time())}

    # ------------------------------------------------------------------ methods
    async def m_getMe(self, d, f):
        me = await self.client.get_me()
        return {"id": me.id, "is_bot": True, "first_name": me.first_name or "", "username": me.username or ""}

    async def m_sendMessage(self, d, f):
        peer = await self._peer(d["chat_id"])
        text, ents = await self._text_and_entities(d.get("text", ""), d.get("parse_mode"), d.get("entities"))
        m = await self.client.send_message(peer, text, formatting_entities=ents, parse_mode=None,
                                           buttons=self._markup(d.get("reply_markup")),
                                           reply_to=d.get("reply_to_message_id"),
                                           link_preview=not d.get("disable_web_page_preview", False))
        return self._sent(m, d["chat_id"])

    async def m_editMessageText(self, d, f):
        peer = await self._peer(d["chat_id"])
        text, ents = await self._text_and_entities(d.get("text", ""), d.get("parse_mode"), d.get("entities"))
        m = await self.client.edit_message(peer, int(d["message_id"]), text, formatting_entities=ents,
                                           parse_mode=None, buttons=self._markup(d.get("reply_markup")),
                                           link_preview=not d.get("disable_web_page_preview", False))
        return self._sent(m, d["chat_id"]) if hasattr(m, "id") else True

    async def m_answerCallbackQuery(self, d, f):
        await self.client(functions.messages.SetBotCallbackAnswerRequest(
            query_id=int(d["callback_query_id"]), cache_time=0,
            alert=bool(d.get("show_alert")) or None, message=(d.get("text") or None)))
        return True

    async def _get_msg(self, chat_id, mid):
        peer = await self._peer(chat_id)
        m = await self.client.get_messages(peer, ids=int(mid))
        if m is None:
            raise self.Err("Bad Request: message to copy not found", 400)
        return m

    async def m_copyMessage(self, d, f):
        src = await self._get_msg(d["from_chat_id"], d["message_id"])
        peer = await self._peer(d["chat_id"])
        if "caption" in d:
            text, ents = await self._text_and_entities(d.get("caption", ""), d.get("parse_mode"), d.get("caption_entities"))
        else:
            text, ents = src.message or "", await self._fix_mentions(src.entities or [])
        markup = self._markup(d.get("reply_markup"))
        reply_to = d.get("reply_to_message_id")
        media = src.media
        if media is not None and not isinstance(media, types.MessageMediaWebPage):
            try:
                m = await self.client.send_file(peer, media, caption=text, formatting_entities=ents, parse_mode=None,
                                                buttons=markup, reply_to=reply_to)
                return {"message_id": m.id}
            except (TypeError, ValueError):
                m = (await self.client.forward_messages(peer, src.id, src.peer_id, drop_author=True))
                m = m[0] if isinstance(m, list) else m
                return {"message_id": m.id}
        m = await self.client.send_message(peer, text, formatting_entities=ents, parse_mode=None,
                                           buttons=markup, reply_to=reply_to, link_preview=False)
        return {"message_id": m.id}

    async def m_sendDocument(self, d, f):
        peer = await self._peer(d["chat_id"])
        text, ents = await self._text_and_entities(d.get("caption", ""), d.get("parse_mode"), d.get("caption_entities"))
        markup = self._markup(d.get("reply_markup"))
        if f and "document" in f:
            name, content = f["document"][0], f["document"][1]
            bio = io.BytesIO(content if isinstance(content, (bytes, bytearray)) else content.read())
            bio.name = name
            m = await self.client.send_file(peer, bio, caption=text, formatting_entities=ents, parse_mode=None,
                                            buttons=markup, force_document=True)
            return self._sent(m, d["chat_id"])
        fid = str(d.get("document", ""))
        mm = re.match(r"^tgm:(-?\d+):(\d+)$", fid)
        if mm:
            src = await self._get_msg(int(mm.group(1)), int(mm.group(2)))
            media = src.media
        else:
            media = utils.resolve_bot_file_id(fid)     # old Bot-API style file_id
        if media is None or isinstance(media, types.MessageMediaWebPage):
            raise self.Err("Bad Request: file not found — please upload the file to the bot again", 400)
        m = await self.client.send_file(peer, media, caption=text, formatting_entities=ents, parse_mode=None,
                                        buttons=markup, force_document=True)
        return self._sent(m, d["chat_id"])

    async def m_getFile(self, d, f):
        return {"file_id": d["file_id"], "file_path": d["file_id"]}

    async def m_setMyCommands(self, d, f):
        cmds = [types.BotCommand(c["command"], c["description"]) for c in d.get("commands", [])]
        sc = d.get("scope") or {}
        if sc.get("type") == "chat":
            scope = types.BotCommandScopePeer(await self._peer(sc["chat_id"]))
        else:
            scope = types.BotCommandScopeDefault()
        await self.client(functions.bots.SetBotCommandsRequest(scope=scope, lang_code="", commands=cmds))
        return True

    async def _download(self, file_id):
        mm = re.match(r"^tgm:(-?\d+):(\d+)$", str(file_id))
        if not mm:
            raise self.Err("Bad Request: unknown file", 400)
        src = await self._get_msg(int(mm.group(1)), int(mm.group(2)))
        data = await self.client.download_media(src, file=bytes)
        if data is None:
            raise self.Err("Bad Request: message has no file", 400)
        return data

    # ------------------------------------------------------------------ incoming
    def _next_id(self):
        self.update_id += 1
        return self.update_id

    @staticmethod
    def _user_dict(u, uid):
        if u is None:
            return {"id": uid, "is_bot": False, "first_name": str(uid)}
        d = {"id": u.id, "is_bot": bool(getattr(u, "bot", False)), "first_name": getattr(u, "first_name", "") or ""}
        if getattr(u, "last_name", None):
            d["last_name"] = u.last_name
        if getattr(u, "username", None):
            d["username"] = u.username
        elif getattr(u, "usernames", None):
            act = [x.username for x in u.usernames if getattr(x, "active", False)]
            if act:
                d["username"] = act[0]
        if getattr(u, "lang_code", None):
            d["language_code"] = u.lang_code
        return d

    def _msg_dict(self, m, sender):
        chat_id = m.chat_id
        frm = self._user_dict(sender, m.sender_id)
        d = {"message_id": m.id, "date": int(m.date.timestamp()) if m.date else int(time.time()),
             "chat": {"id": chat_id, "type": "private" if m.is_private else "group",
                      "first_name": frm.get("first_name", "")},
             "from": frm}
        if frm.get("username"):
            d["chat"]["username"] = frm["username"]
        ents = [x for x in (self._ent_to_api(e) for e in (m.entities or [])) if x]
        media = m.media
        has_media = media is not None and not isinstance(media, types.MessageMediaWebPage)
        if has_media:
            if m.message:
                d["caption"] = m.message
                if ents:
                    d["caption_entities"] = ents
        else:
            d["text"] = m.message or ""
            if ents:
                d["entities"] = ents
        fid = f"tgm:{chat_id}:{m.id}"
        if has_media:
            if m.photo:
                d["photo"] = [{"file_id": fid, "file_unique_id": fid}]
            elif m.sticker:
                d["sticker"] = {"file_id": fid}
            elif m.voice:
                d["voice"] = {"file_id": fid}
            elif m.video_note:
                d["video_note"] = {"file_id": fid}
            elif m.gif:
                d["animation"] = {"file_id": fid}
            elif m.video:
                d["video"] = {"file_id": fid}
            elif m.audio:
                d["audio"] = {"file_id": fid}
            elif m.document:
                doc = m.document
                name = ""
                for a in doc.attributes or []:
                    if isinstance(a, types.DocumentAttributeFilename):
                        name = a.file_name
                d["document"] = {"file_id": fid, "file_name": name, "mime_type": doc.mime_type or "",
                                 "file_size": int(doc.size or 0)}
            elif m.contact:
                d["contact"] = {"phone_number": m.contact.phone_number, "first_name": m.contact.first_name}
            else:
                d["other"] = True
        if m.reply_to and getattr(m.reply_to, "reply_to_msg_id", None):
            d["reply_to_message"] = {"message_id": m.reply_to.reply_to_msg_id, "chat": {"id": chat_id}}
        return d

    async def _on_message(self, ev):
        try:
            if not ev.is_private or ev.out:
                return
            sender = await ev.get_sender()
            self._remember(sender)
            if getattr(sender, "bot", False):
                return
            self.on_update({"update_id": self._next_id(), "message": self._msg_dict(ev.message, sender)})
        except Exception as e:
            log.error("incoming message: %s", e)

    async def _on_callback(self, ev):
        try:
            sender = await ev.get_sender()
            self._remember(sender)
            q = ev.query
            chat_id = ev.chat_id or ev.sender_id
            data = q.data.decode("utf-8", "replace") if q.data else ""
            self.on_update({"update_id": self._next_id(), "callback_query": {
                "id": str(q.query_id), "from": self._user_dict(sender, ev.sender_id), "data": data,
                "message": {"message_id": q.msg_id, "chat": {"id": chat_id, "type": "private"}}}})
        except Exception as e:
            log.error("callback: %s", e)

    async def _on_raw(self, upd):
        try:
            if isinstance(upd, types.UpdateBotStopped):
                self.on_update({"update_id": self._next_id(), "my_chat_member": {
                    "from": {"id": upd.user_id}, "chat": {"id": upd.user_id, "type": "private"},
                    "new_chat_member": {"status": "kicked" if upd.stopped else "member"}}})
        except Exception as e:
            log.error("raw update: %s", e)
