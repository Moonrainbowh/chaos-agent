"""Read-only deterministic CI race diagnosis; no production/test edits or HTTP."""
import asyncio
import json
from unittest.mock import AsyncMock, patch
from tests.test_workbuddy_switch import WorkBuddyModelSelectionTests
from code_agent.authentication.models import Credential
from code_agent.interfaces.tests.test_command_navigation import Runtime, make_app


async def main():
    case = WorkBuddyModelSelectionTests('test_discovery_failure_retains_login_and_a_visible_retry_choice')
    case.setUp()
    credential = Credential('oauth', 'test-secret', extra={'workbuddyEndpoint': 'https://copilot.tencent.com'})
    try:
        with patch('code_agent.authentication.source.StoredCredentialSource.resolve', new=AsyncMock(return_value=credential)):
            try:
                await case.test_discovery_failure_retains_login_and_a_visible_retry_choice()
            except TypeError as error:
                assert str(error) == "object NoneType can't be used in 'await' expression"
                print(json.dumps({'probe': 'original_test_immediate_credential_resolution',
                    'result': 'EXPECTED_SAME_CI_TYPEERROR', 'error': str(error), 'http_calls': 0}))
            else:
                raise AssertionError('Controlled fast completion did not reproduce the exact CI race')

        app = make_app(runtime_selection=Runtime())
        app.authentication = case.control
        started, release = asyncio.Event(), asyncio.Event()
        async def discover(*args, **kwargs):
            started.set()
            await release.wait()
            raise ValueError('private')
        with patch('code_agent.authentication.source.StoredCredentialSource.resolve', new=AsyncMock(return_value=credential)), \
             patch('code_agent.authentication.workbuddy_catalog.discover', new=discover):
            assert await app.submit('/model workbuddy:oauth')
            loading = app._auth_task
            assert loading is not None
            try:
                await asyncio.wait_for(started.wait(), 3)
            finally:
                release.set()
            await asyncio.wait_for(loading, 3)
        text = '\n'.join(entry.text for entry in app.state.entries)
        assert 'discovery failed' in text and 'private' not in text
        assert case.store.get('workbuddy', 'oauth') is not None
        assert app.runtime_selection.current.profile == 'gpt56_sol'
        assert app.input.text == '/model workbuddy:oauth'
        assert app._auth_task is None
        print(json.dumps({'probe': 'event_barrier_and_captured_task_proposal',
            'result': 'PASS_ORIGINAL_SEMANTICS_AND_VISIBLE_RETRY', 'slot_cleared': True, 'http_calls': 0}))
    finally:
        case.doCleanups()


if __name__ == '__main__':
    asyncio.run(main())
