import asyncio
from pathlib import Path
import tempfile
import threading
import unittest

from chaos_agent.remote.pairing import PairingStore
from code_agent.authentication.store_lock import StoreLock
from unittest.mock import patch


class ResponseRevocationTests(unittest.IsolatedAsyncioTestCase):
    async def test_repeated_cancel_waiting_for_lock_keeps_independent_release_owner(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / 'devices.json'
            self.assertTrue(path.is_absolute() and path.is_relative_to(root))
            store = PairingStore(path)
            credential = store.pair(store.issue_token())
            blocker = StoreLock(path)
            blocker.acquire()
            started, released = threading.Event(), threading.Event()
            tracked = []
            class ObservedLock(StoreLock):
                def acquire(self):
                    tracked.append(self)
                    started.set()
                    super().acquire()
                def release(self):
                    super().release()
                    released.set()
            async def response():
                async with store.authorized_response(credential):
                    self.fail('cancelled request entered approval scope')
            try:
                with patch('chaos_agent.remote.pairing.StoreLock', ObservedLock):
                    request = asyncio.create_task(response())
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    request.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(request.done())
                    request.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await request
                    blocker.release()
                    self.assertTrue(await asyncio.to_thread(released.wait, 5))
                    await asyncio.gather(*store._response_lock_owners)
                    self.assertFalse(tracked[0].local.locked())
                    self.assertIsNone(tracked[0].handle)
                # Both a new response and independent revocation remain usable.
                async with store.authorized_response(credential):
                    self.assertTrue(store.authenticate(credential))
                await asyncio.to_thread(PairingStore(path).revoke)
                self.assertFalse(store.authenticate(credential))
            finally:
                if blocker.handle is not None:
                    blocker.release()

    async def test_committed_response_finishes_before_concurrent_revocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "devices.json"
            self.assertTrue(path.is_absolute() and path.is_relative_to(root))
            store, external = PairingStore(path), PairingStore(path)
            credential = store.pair(store.issue_token())
            entered = threading.Event()
            def revoke():
                entered.set()
                external.revoke()
            async with store.authorized_response(credential):
                revocation = asyncio.create_task(asyncio.to_thread(revoke))
                await asyncio.to_thread(entered.wait)
                self.assertTrue(store.authenticate(credential))
                # The durable consume would occur here, while the same store lock is held.
            await revocation
            self.assertFalse(store.authenticate(credential))
            with self.assertRaises(PermissionError):
                async with store.authorized_response(credential):
                    self.fail("revoked device entered a response scope")

    async def test_revocation_completed_first_never_enters_response(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            path = root / "devices.json"
            self.assertTrue(path.is_absolute() and path.is_relative_to(root))
            store = PairingStore(path)
            credential = store.pair(store.issue_token())
            store.revoke()
            with self.assertRaises(PermissionError):
                async with store.authorized_response(credential):
                    self.fail("revocation must precede approval")
