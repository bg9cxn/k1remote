"""PttManager 状态机单元测试：锁存/拒绝/看门狗/断线撤发/尾音保护。"""

import asyncio
import time

from k1remote.ptt import PttManager


class FakeClient:
    def __init__(self, available=True):
        self.ptt_available = available
        self.calls = []

    def set_ptt(self, hold):
        self.calls.append(hold)


class FakeScreen:
    def __init__(self):
        self.led_tx = False


class FakeHub:
    def __init__(self):
        self.screen = FakeScreen()
        self.broadcasts = []

    def broadcast_json(self, obj):
        self.broadcasts.append(obj)


def make(available=True, max_tx_s=180.0, tail_ms=400):
    client, hub = FakeClient(available), FakeHub()
    changes = []
    ptt = PttManager(client, hub, max_tx_s=max_tx_s, tail_ms=tail_ms,
                     on_hold_change=changes.append)
    return ptt, client, hub, changes


def test_request_on_holds():
    ptt, client, _, _ = make()
    assert ptt.request_on("ws1") is None
    assert ptt.held and ptt.holder == "ws1"
    assert client.calls == [True]


def test_request_on_refuses_when_radio_already_tx():
    ptt, client, hub, _ = make()
    hub.screen.led_tx = True  # 卡发射/VOX 误触发
    err = ptt.request_on("ws1")
    assert err and "已在发射" in err
    assert client.calls == [] and not ptt.held


def test_request_on_refuses_when_unavailable():
    ptt, client, _, _ = make(available=False)
    assert ptt.request_on("ws1")
    assert client.calls == []


def test_second_operator_refused_while_held():
    ptt, _, _, _ = make()
    assert ptt.request_on("ws1") is None
    err = ptt.request_on("ws2")
    assert err and "其他操作员" in err
    assert ptt.holder == "ws1"  # 锁未移交


def test_request_on_idempotent_for_holder():
    ptt, client, _, _ = make()
    ptt.request_on("ws1")
    assert ptt.request_on("ws1") is None
    assert client.calls == [True]  # 不重复键上


def test_request_off_immediate_releases():
    ptt, client, _, changes = make()
    ptt.request_on("ws1")
    ptt.request_off("ws1", immediate=True)  # 串口重连等电平已消失的场景
    assert not ptt.held and client.calls == [True, False]
    assert changes == [True, False]


def test_non_holder_cannot_release():
    ptt, client, _, _ = make()
    ptt.request_on("ws1")
    ptt.request_off("ws2")  # 他人释放被忽略（含尾音路径）
    assert ptt.held and client.calls == [True] and not ptt.releasing


def test_watchdog_force_release():
    async def scenario():
        ptt, client, _, _ = make(max_tx_s=180.0)
        ptt.bind_loop(asyncio.get_running_loop())
        ptt.request_on("ws1")
        assert ptt.check_watchdog(time.monotonic()) is None  # 未到时限
        assert ptt.check_watchdog(time.monotonic() + 181.0) == "发射超时保护"
        assert not ptt.held and ptt.releasing  # 看门狗走尾音路径
        ptt._finish_tail("发射超时保护")
        ptt._release_dtr()
        assert client.calls == [True, False]

    asyncio.run(scenario())


def test_release_if_holder_only():
    async def scenario():
        ptt, client, _, _ = make()
        ptt.bind_loop(asyncio.get_running_loop())
        ptt.request_on("ws1")
        ptt.release_if_holder("ws2")
        assert ptt.held
        ptt.release_if_holder("ws1")
        assert not ptt.held and ptt.releasing
        ptt._finish_tail("")
        ptt._release_dtr()
        assert client.calls == [True, False]

    asyncio.run(scenario())


def test_link_reset_releases_immediately():
    ptt, client, _, changes = make()
    ptt.request_on("ws1")
    ptt.on_link_reset()  # 串口重连（worker 线程调用）：立即释放不走尾音
    assert not ptt.held and not ptt.releasing
    assert client.calls == [True, False]
    assert changes == [True, False]


def test_broadcast_carries_state():
    ptt, _, hub, _ = make()
    ptt.request_on("ws1")
    ptt.request_off("ws1", immediate=True)
    kinds = [(b["t"], b["held"]) for b in hub.broadcasts]
    assert (True in [h for _, h in kinds]) and (False in [h for _, h in kinds])


# ---- 尾音保护（需要真实事件循环）----


def test_tail_keeps_radio_keyed_then_releases():
    async def scenario():
        ptt, client, _, changes = make(tail_ms=80)
        ptt.bind_loop(asyncio.get_running_loop())
        ptt.request_on("ws1")
        ptt.request_off("ws1")  # 默认走尾音
        assert not ptt.held and ptt.releasing
        assert client.calls == [True]  # 电平未撤
        assert changes == [True]  # 播放通路未关
        await asyncio.sleep(0.08 + 0.05)  # 越过尾音
        assert changes == [True, False]  # 停播
        assert client.calls == [True]  # DTR 还差输出管线余量
        await asyncio.sleep(0.15)  # 越过输出延迟
        assert client.calls == [True, False]
        assert not ptt.releasing

    asyncio.run(scenario())


def test_tail_cancelled_by_repress():
    async def scenario():
        ptt, client, _, changes = make(tail_ms=80)
        ptt.bind_loop(asyncio.get_running_loop())
        ptt.request_on("ws1")
        ptt.request_off("ws1")
        ptt.request_on("ws1")  # 尾音期内重新按下
        assert ptt.held and not ptt.releasing
        await asyncio.sleep(0.25)  # 原尾音早已过期
        # 重按时 set_ptt(True) 会再次入队（worker 层因 _ptt_hold 未变而幂等）；
        # on_hold_change(True) 亦重复通知（真实 sink 对已开启状态幂等）
        assert client.calls == [True, True]  # 从未撤电平
        assert changes == [True, True]  # 播放通路从未关闭
        ptt.request_off("ws1", immediate=True)
        assert client.calls == [True, True, False]
        assert changes[-1] is False

    asyncio.run(scenario())


def test_repress_after_full_release_works():
    async def scenario():
        ptt, client, _, _ = make(tail_ms=50)
        ptt.bind_loop(asyncio.get_running_loop())
        ptt.request_on("ws1")
        ptt.request_off("ws1")
        await asyncio.sleep(0.30)  # 尾音 + DTR 延迟全部走完
        assert client.calls == [True, False]
        ptt.request_on("ws1")  # 重新键上
        assert ptt.held and client.calls == [True, False, True]
        ptt.request_off("ws1", immediate=True)
        assert client.calls == [True, False, True, False]

    asyncio.run(scenario())
