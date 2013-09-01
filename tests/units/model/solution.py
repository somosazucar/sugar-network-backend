#!/usr/bin/env python
# sugar-lint: disable

from __init__ import tests

from sugar_network.client import Connection
from sugar_network.model.user import User
from sugar_network.model.context import Context
from sugar_network.model.feedback import Feedback
from sugar_network.model.solution import Solution
from sugar_network.model.implementation import Implementation


class SolutionTest(tests.Test):

    def test_SetContext(self):
        volume = self.start_master([User, Context, Feedback, Solution, Implementation])
        client = Connection()

        context = client.post(['context'], {
            'type': 'package',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })
        feedback = client.post(['feedback'], {
            'context': context,
            'type': 'idea',
            'title': 'title',
            'content': 'content',
            })
        solution = client.post(['solution'], {
            'feedback': feedback,
            'content': '',
            })

        self.assertEqual(
                context,
                client.get(['solution', solution, 'context']))


if __name__ == '__main__':
    tests.main()