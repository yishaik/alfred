"""Exercise the TV boundary, failed delivery, persistence and Telegram routing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiowebostv import WebOsClient

from tgbridge import handlers, tv_handlers
from tgbridge.config import load_json
from tgbridge.tv import LGRemote, TVError


def client():
    result = Mock(spec=WebOsClient)
    result.client_key = "fixture-pairing-key"
    result.connect = AsyncMock(return_value=True)
    result.disconnect = AsyncMock()
    result.is_connected.return_value = True
    result.get_volume = AsyncMock(return_value=12)
    result.get_muted = AsyncMock(return_value=False)
    result.get_current_app = AsyncMock(return_value="youtube.leanback.v4")
    return result


def controller(tmp_path, tv=None):
    tv = tv or client()
    factory = Mock(return_value=tv)
    with patch.dict("os.environ", {"BRIDGE_TV_HOST": "", "BRIDGE_TV_MAC": ""}):
        remote = LGRemote(tmp_path / "tv.json", client_factory=factory)
    return remote, tv, factory


def run(coro):
    return asyncio.run(coro)


def test_pairing_persists_and_connection_is_reused(tmp_path):
    async def scenario():
        remote, tv, factory = controller(tmp_path)
        await remote.configure("192.168.1.50")
        status = await remote.execute("status")
        await remote.execute("button", "ENTER")
        factory.assert_called_once_with("192.168.1.50", None)
        assert "12" in status and "youtube.leanback.v4" in status
        tv.button.assert_awaited_once_with("ENTER")
        assert load_json(remote.state_file, {})["client_key"] == "fixture-pairing-key"
        restored = LGRemote(remote.state_file, client_factory=factory)
        await restored.execute("status")
        factory.assert_called_with("192.168.1.50", "fixture-pairing-key")
        await remote.close()
        await restored.close()
    run(scenario())


@pytest.mark.parametrize("host", ["8.8.8.8", "127.0.0.1", "0.0.0.0", "169.254.1.1", "example.com", "192.168.1.1/path", "::1"])
def test_host_is_a_private_lan_ip(tmp_path, host):
    async def scenario():
        remote, _, factory = controller(tmp_path)
        with pytest.raises(TVError):
            await remote.configure(host)
        factory.assert_not_called()
        assert not remote.state_file.exists()
    run(scenario())


@pytest.mark.parametrize("action,value", [("button", "UP\nname:HOME"), ("button", "IN_START"), ("volume", -1), ("volume", 101), ("volume", True), ("volume", "12"), ("text", ""), ("text", "x" * 501), ("erase", None)])
def test_bad_commands_never_connect(tmp_path, action, value):
    async def scenario():
        remote, _, factory = controller(tmp_path)
        with pytest.raises(TVError):
            await remote.execute(action, value)
        factory.assert_not_called()
    run(scenario())


def test_changed_tv_cannot_reuse_previous_key_or_mac(tmp_path):
    async def scenario():
        remote, tv, factory = controller(tmp_path)
        await remote.configure("192.168.1.50")
        await remote.execute("status")
        await remote.configure_wake("AA:BB:CC:DD:EE:FF")
        await remote.configure("192.168.1.51")
        assert not remote.paired and not remote.settings["mac"]
        tv.disconnect.assert_awaited_once()
        await remote.execute("status")
        factory.assert_called_with("192.168.1.51", None)
        await remote.close()
    run(scenario())


def test_delivery_failure_is_not_replayed_and_next_action_reconnects(tmp_path):
    async def scenario():
        remote, tv, factory = controller(tmp_path)
        await remote.configure("192.168.1.50")
        tv.volume_up = AsyncMock(side_effect=ConnectionError("lost after send"))
        with pytest.raises(TVError, match="לא אומתה"):
            await remote.execute("volup")
        tv.volume_up.assert_awaited_once()
        tv.disconnect.assert_awaited_once()
        assert factory.call_count == 1
        await remote.execute("status")
        assert factory.call_count == 2
        await remote.close()
    run(scenario())


def test_connect_timeout_and_cancellation_cleanup(tmp_path):
    async def scenario():
        remote, tv, _ = controller(tmp_path)
        await remote.configure("192.168.1.50")
        tv.connect.side_effect = TimeoutError()
        with pytest.raises(TVError, match="לא ענתה בזמן"):
            await remote.execute("status")
        tv.disconnect.assert_awaited_once()
        tv.connect.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await remote.execute("status")
        assert tv.disconnect.await_count == 2
    run(scenario())


def test_concurrent_taps_are_serialized(tmp_path):
    async def scenario():
        remote, tv, factory = controller(tmp_path)
        await remote.configure("192.168.1.50")
        order = []
        async def button(name):
            order.append("start:" + name)
            await asyncio.sleep(0)
            order.append("end:" + name)
        tv.button = AsyncMock(side_effect=button)
        await asyncio.gather(remote.execute("button", "UP"), remote.execute("button", "ENTER"))
        assert order == ["start:UP", "end:UP", "start:ENTER", "end:ENTER"]
        assert factory.call_count == 1
        await remote.close()
    run(scenario())


def test_mute_is_a_toggle_and_volume_is_validated(tmp_path):
    async def scenario():
        remote, tv, _ = controller(tmp_path)
        await remote.configure("192.168.1.50")
        tv.set_mute = AsyncMock()
        tv.get_muted.side_effect = [False, True, None]
        await remote.execute("toggle_mute")
        await remote.execute("toggle_mute")
        assert [call.args for call in tv.set_mute.await_args_list] == [(True,), (False,)]
        with pytest.raises(TVError):
            await remote.execute("toggle_mute")
        assert tv.set_mute.await_count == 2
        tv.set_volume = AsyncMock()
        await remote.execute("volume", 0)
        tv.set_volume.assert_awaited_once_with(0)
        await remote.close()
    run(scenario())


def test_wake_uses_correct_packet_without_claiming_tv_is_on(tmp_path):
    async def scenario():
        remote, _, factory = controller(tmp_path)
        with pytest.raises(TVError, match="/tv mac"):
            await remote.execute("on")
        await remote.configure_wake("AA:BB:CC:DD:EE:FF", "192.168.1.255")
        with patch("tgbridge.tv.socket.socket") as make_socket:
            message = await remote.execute("on")
            sock = make_socket.return_value.__enter__.return_value
            packet, address = sock.sendto.call_args.args
            assert packet == b"\xff" * 6 + bytes.fromhex("aabbccddeeff") * 16
            assert address == ("192.168.1.255", 9)
        factory.assert_not_called()
        assert "בקשת הדלקה" in message and "כדי לבדוק" in message
    run(scenario())


def update_context(*, text="/tv", data=None):
    user = SimpleNamespace(is_bot=False)
    message = SimpleNamespace(text=text, message_id=812345, from_user=user,
                              message_thread_id=None, reply_to_message=None,
                              reply_text=AsyncMock(), set_reaction=AsyncMock())
    query = None if data is None else SimpleNamespace(data=data, from_user=user,
                        message=message, answer=AsyncMock(), edit_message_text=AsyncMock())
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=42), effective_user=user,
                             effective_message=message, message=message, callback_query=query)
    remote = SimpleNamespace(host="192.168.1.50", execute=AsyncMock(return_value="sent"))
    ctx = SimpleNamespace(args=[], application=SimpleNamespace(bot_data={"tv_remote": remote}))
    return update, ctx, remote


def test_unauthorized_callback_cannot_touch_tv():
    async def scenario():
        update, ctx, remote = update_context(data="tv:off")
        with patch.object(tv_handlers, "authorized_chat", return_value=False):
            await tv_handlers.on_tv_callback(update, ctx)
        remote.execute.assert_not_awaited()
        update.callback_query.answer.assert_awaited_once_with("אין הרשאה", show_alert=True)
    run(scenario())


def test_callback_failure_shows_error_and_keeps_remote():
    async def scenario():
        update, ctx, remote = update_context(data="tv:volup")
        remote.execute.side_effect = TVError("offline")
        with patch.object(tv_handlers, "authorized_chat", return_value=True):
            await tv_handlers.on_tv_callback(update, ctx)
        update.callback_query.answer.assert_awaited_once()
        call = update.callback_query.edit_message_text.await_args
        assert "offline" in call.args[0] and "⚠️" in call.args[0]
        assert call.kwargs["reply_markup"].inline_keyboard
        update.message.set_reaction.assert_not_awaited()
    run(scenario())


def test_open_remote_does_not_start_model_session():
    async def scenario():
        update, ctx, remote = update_context()
        # No mgr is present: /tv must work even when there is no agent session.
        with patch.object(tv_handlers, "authorized_chat", return_value=True):
            await tv_handlers.cmd_tv(update, ctx)
        remote.execute.assert_awaited_once_with("status", None)
        update.message.reply_text.assert_awaited_once()
    run(scenario())


def test_ordinary_ok_remains_a_chat_message():
    async def scenario():
        update, ctx, remote = update_context(text="OK")
        session = SimpleNamespace(questions={}, submit=AsyncMock())
        with patch.object(handlers, "_session", AsyncMock(return_value=session)):
            handlers._SEEN_MSGS.clear()
            await handlers.on_text(update, ctx)
        assert session.submit.await_args.args[0] == "OK"
        remote.execute.assert_not_awaited()
    run(scenario())


def test_legacy_arrows_do_not_start_an_agent_and_errors_are_visible():
    async def scenario():
        update, ctx, remote = update_context(text="⬆️")
        remote.execute.side_effect = TVError("offline")
        with patch.object(handlers, "_session", AsyncMock()) as get_session, \
             patch.object(tv_handlers, "authorized_chat", return_value=True):
            handlers._SEEN_MSGS.clear()
            await handlers.on_text(update, ctx)
        get_session.assert_not_awaited()
        assert "offline" in update.message.reply_text.await_args.args[0]
    run(scenario())


def test_app_catalog_handles_long_ids_pagination_and_stale_choices():
    async def scenario():
        update, ctx, remote = update_context(data="tv:apps:0")
        appid = "long-app-" + "x" * 100
        remote.execute.return_value = [{"id": appid, "title": "Long app"}] + [
            {"id": f"app-{i}", "title": f"App {i}"} for i in range(10)]
        with patch.object(tv_handlers, "authorized_chat", return_value=True):
            await tv_handlers.on_tv_callback(update, ctx)
            markup = update.callback_query.edit_message_text.await_args.kwargs["reply_markup"]
            buttons = [b for row in markup.inline_keyboard for b in row]
            assert all(len(b.callback_data.encode()) <= 64 for b in buttons)
            assert any(b.callback_data == "tv:apps:8" for b in buttons)
            selection = buttons[0].callback_data
            update.callback_query.data = selection
            remote.execute.side_effect = [remote.execute.return_value, "launched"]
            await tv_handlers.on_tv_callback(update, ctx)
            assert remote.execute.await_args.args == ("app", appid)
            remote.execute.side_effect = None
            remote.execute.return_value = []
            await tv_handlers.on_tv_callback(update, ctx)
            assert "כבר לא זמינה" in update.callback_query.edit_message_text.await_args.args[0]
    run(scenario())


def test_expired_queued_tap_is_not_delivered(tmp_path):
    async def scenario():
        remote, _, factory = controller(tmp_path)
        await remote._lock.acquire()
        try:
            with pytest.raises(TVError, match="הלחיצה לא נשלחה"):
                await remote.execute("button", "ENTER")
        finally:
            remote._lock.release()
        factory.assert_not_called()
        await remote.configure("192.168.1.50")
        await remote.execute("status")
        await remote.close()
    run(scenario())


def test_pairing_storage_failure_is_visible(tmp_path):
    async def scenario():
        remote, _, _ = controller(tmp_path)
        with patch("tgbridge.tv.save_json", side_effect=OSError("disk full")):
            with pytest.raises(TVError, match="לא נשמרו"):
                await remote.configure("192.168.1.50")
    run(scenario())


def test_main_registers_remote_before_generic_callback_without_starting_polling():
    from tgbridge import main
    from telegram.ext import CallbackQueryHandler, CommandHandler
    app = Mock()
    builder = Mock()
    builder.token.return_value.defaults.return_value.post_init.return_value.post_shutdown.return_value.build.return_value = app
    with patch.object(main, "ApplicationBuilder", return_value=builder), \
         patch.object(main, "_acquire_singleton_lock", return_value=True), \
         patch.object(main, "_await_dns", return_value=True), \
         patch.object(main, "_disable_wmi_platform_lookup"), \
         patch.object(main, "RotatingFileHandler"), \
         patch.object(main.logging, "basicConfig"), \
         patch.object(main, "BOT_TOKEN", "fixture:token"), \
         patch.object(main, "CHAT_ID", 42):
        main.main()
    registered = [call.args[0] for call in app.add_handler.call_args_list]
    command = next(h for h in registered if isinstance(h, CommandHandler) and "tv" in h.commands)
    assert command.callback is tv_handlers.cmd_tv and command.block is False
    callbacks = [h for h in registered if isinstance(h, CallbackQueryHandler)]
    assert callbacks[0].callback is tv_handlers.on_tv_callback
    assert callbacks[0].pattern.match("tv:button:ENTER")
    assert not callbacks[0].pattern.match("act:status")
    assert callbacks[0].block is False
    assert callbacks[1].callback is handlers.on_callback
