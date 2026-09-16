from __future__ import annotations

import unittest
import subprocess
from unittest.mock import patch

import test_tool_schemas as schemas
from code_agent.core.cancellation import CancellationToken
from code_agent.core.models import ActionRequest
from code_agent.workspace._text_search import SearchMatch
from code_agent.workspace.errors import SearchTimeoutError
from code_agent.workspace.git import GitWorkspace


class SearchToolScopeTests(unittest.IsolatedAsyncioTestCase):
    setUp = schemas.DispatcherValidationTests.setUp
    tearDown = schemas.DispatcherValidationTests.tearDown

    async def search(self, **arguments):
        return await self.dispatcher.dispatch(
            ActionRequest('search-1', 'search_text', {'pattern': 'before', **arguments}),
            CancellationToken(),
        )

    async def test_scope_and_glob_reach_workspace(self):
        result = await self.search(root='note.txt', include_globs=['*.txt'])
        self.assertFalse(result.is_error)
        self.assertTrue(result.output['complete'])
        self.assertEqual(result.output['matches'][0]['path'], 'note.txt')
        result = await self.search(root='note.txt', include_globs=['*.py'])
        self.assertEqual(result.output['matches'], ())

    async def test_partial_deadline_results_are_explicit(self):
        for matches in ((), (SearchMatch('note.txt', 1, 1, 'before'),)):
            with self.subTest(matches=matches), patch.object(
                self.files, 'search', side_effect=SearchTimeoutError('deadline', matches)
            ):
                result = await self.search()
                self.assertFalse(result.is_error)
                self.assertFalse(result.output['complete'])
                self.assertEqual(result.output['incomplete_reason'], 'timeout')
                self.assertEqual(len(result.output['matches']), len(matches))

    async def test_result_cap_is_not_reported_as_complete(self):
        result = await self.search(max_results=1)
        self.assertEqual(result.output['incomplete_reason'], 'result_limit')
        self.assertFalse(result.output['complete'])

    async def test_scope_argument_types_rejected_before_policy(self):
        for arguments in ({'root': 1}, {'include_globs': '*.py'}, {'max_results': True}):
            with self.subTest(arguments=arguments):
                self.policy.reset_mock()
                result = await self.search(**arguments)
                self.assertTrue(result.is_error)
                self.policy.evaluate.assert_not_called()

    async def test_git_nested_ignore_and_negation_reach_real_search(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True, capture_output=True)
        env = self.root / 'environment'
        env.mkdir()
        (env / '.gitignore').write_text('*\n!keep.py\n')
        (env / 'ignored.py').write_text('before')
        (env / 'keep.py').write_text('before')
        self.dispatcher.git = GitWorkspace(self.root)
        result = await self.search()
        self.assertTrue(result.output['complete'])
        self.assertEqual([m['path'] for m in result.output['matches']], ['environment/keep.py', 'note.txt'])
        result = await self.search(root='environment')
        self.assertEqual([m['path'] for m in result.output['matches']], ['environment/keep.py'])
        result = await self.search(root='environment/ignored.py')
        self.assertTrue(result.output['complete'])
        self.assertEqual(result.output['matches'], ())
