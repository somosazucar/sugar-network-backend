#!/usr/bin/env python
# sugar-lint: disable

import json

from __init__ import tests

from sugar_network import db, client
from sugar_network.client import journal, injector, IPCClient
from sugar_network.client.commands import ClientCommands, CachedClientCommands
from sugar_network.resources.volume import Volume
from sugar_network.resources.user import User
from sugar_network.resources.report import Report
from sugar_network.client import IPCRouter
from sugar_network.toolkit import coroutine

import requests


class CommandsTest(tests.Test):

    def test_Hub(self):
        volume = Volume('db')
        cp = ClientCommands(volume, offline=True)
        server = coroutine.WSGIServer(
                ('localhost', client.ipc_port.value), IPCRouter(cp))
        coroutine.spawn(server.serve_forever)
        coroutine.dispatch()

        url = 'http://localhost:%s' % client.ipc_port.value

        response = requests.request('GET', url + '/hub', allow_redirects=False)
        self.assertEqual(303, response.status_code)
        self.assertEqual('/hub/', response.headers['Location'])

        client.hub_root.value = '.'
        index_html = '<html><body>index</body></html>'
        self.touch(('index.html', index_html))

        response = requests.request('GET', url + '/hub', allow_redirects=True)
        self.assertEqual(index_html, response.content)

        response = requests.request('GET', url + '/hub/', allow_redirects=False)
        self.assertEqual(index_html, response.content)

    def test_launch(self):
        self.override(injector, 'launch', lambda *args, **kwargs: [{'args': args, 'kwargs': kwargs}])
        volume = Volume('db')
        cp = ClientCommands(volume, offline=True)

        self.assertRaises(RuntimeError, cp.launch, 'fake-document', 'app', [])

        trigger = self.wait_for_events(cp, event='launch')
        cp.launch('context', 'app', [])
        self.assertEqual(
                {'event': 'launch', 'args': ['app', []], 'kwargs': {'color': None, 'activity_id': None, 'uri': None, 'object_id': None}},
                trigger.wait())

    def test_launch_ResumeJobject(self):
        self.override(injector, 'launch', lambda *args, **kwargs: [{'args': args, 'kwargs': kwargs}])
        self.override(journal, 'exists', lambda *args: True)
        volume = Volume('db')
        cp = ClientCommands(volume, offline=True)

        trigger = self.wait_for_events(cp, event='launch')
        cp.launch('context', 'app', [], object_id='object_id')
        self.assertEqual(
                {'event': 'launch', 'args': ['app', []], 'kwargs': {'color': None, 'activity_id': None, 'uri': None, 'object_id': 'object_id'}},
                trigger.wait())

    def test_InlineSwitchInFind(self):
        self.home_volume = self.start_online_client()
        ipc = IPCClient()

        guid1 = ipc.post(['context'], {
            'type': 'activity',
            'title': '1',
            'summary': 'summary',
            'description': 'description',
            })
        guid2 = ipc.post(['context'], {
            'type': 'activity',
            'title': '2',
            'summary': 'summary',
            'description': 'description',
            })
        guid3 = ipc.post(['context'], {
            'type': 'activity',
            'title': '3',
            'summary': 'summary',
            'description': 'description',
            })

        self.assertEqual([
            {'guid': guid1, 'title': '1'},
            {'guid': guid2, 'title': '2'},
            {'guid': guid3, 'title': '3'},
            ],
            ipc.get(['context'], reply=['guid', 'title'])['result'])
        self.assertEqual([
            {'guid': guid1, 'title': '1'},
            {'guid': guid2, 'title': '2'},
            {'guid': guid3, 'title': '3'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], clone=0)['result'])
        self.assertEqual([
            {'guid': guid1, 'title': '1'},
            {'guid': guid2, 'title': '2'},
            {'guid': guid3, 'title': '3'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], favorite=False)['result'])
        self.assertEqual([
            ],
            ipc.get(['context'], reply=['guid', 'title'], favorite=True)['result'])
        self.assertEqual([
            ],
            ipc.get(['context'], reply=['guid', 'title'], clone=2)['result'])

        ipc.put(['context', guid2], True, cmd='favorite')
        self.home_volume['context'].update(guid2, {'title': '2_'})
        self.assertEqual([
            {'guid': guid2, 'title': '2_'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], favorite=True)['result'])
        self.assertEqual([
            ],
            ipc.get(['context'], reply=['guid', 'title'], clone=2)['result'])

        ipc.put(['context', guid1], True, cmd='favorite')
        ipc.put(['context', guid3], True, cmd='favorite')
        self.home_volume['context'].update(guid1, {'clone': 1, 'title': '1_'})
        self.home_volume['context'].update(guid3, {'clone': 2, 'title': '3_'})
        self.assertEqual([
            {'guid': guid1, 'title': '1_'},
            {'guid': guid2, 'title': '2_'},
            {'guid': guid3, 'title': '3_'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], favorite=True)['result'])
        self.assertEqual([
            {'guid': guid1, 'title': '1_'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], clone=1)['result'])
        self.assertEqual([
            {'guid': guid3, 'title': '3_'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], clone=2)['result'])

        self.assertEqual([
            {'guid': guid1, 'title': '1'},
            {'guid': guid2, 'title': '2'},
            {'guid': guid3, 'title': '3'},
            ],
            ipc.get(['context'], reply=['guid', 'title'])['result'])
        self.assertEqual([
            {'guid': guid1, 'title': '1'},
            {'guid': guid2, 'title': '2'},
            {'guid': guid3, 'title': '3'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], clone=0)['result'])
        self.assertEqual([
            {'guid': guid1, 'title': '1'},
            {'guid': guid2, 'title': '2'},
            {'guid': guid3, 'title': '3'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], favorite=False)['result'])

    def test_SetLocalLayerInOffline(self):
        volume = Volume('client')
        cp = ClientCommands(volume)
        post = db.Request(method='POST', document='context')
        post.content_type = 'application/json'
        post.content = {
                'type': 'activity',
                'title': 'title',
                'summary': 'summary',
                'description': 'description',
                }

        guid = cp.call(post)
        self.assertEqual(['public', 'local'], cp.call(db.Request(method='GET', document='context', guid=guid, prop='layer')))

        trigger = self.wait_for_events(cp, event='inline', state='online')
        node_volume = self.start_master()
        cp.call(db.Request(method='GET', cmd='inline'))
        trigger.wait()

        guid = cp.call(post)
        self.assertEqual(['public'], cp.call(db.Request(method='GET', document='context', guid=guid, prop='layer')))

    def test_CachedClientCommands(self):
        volume = Volume('client')
        cp = CachedClientCommands(volume)

        post = db.Request(method='POST', document='context')
        post.content_type = 'application/json'
        post.content = {
                'type': 'activity',
                'title': 'title',
                'summary': 'summary',
                'description': 'description',
                }
        guid1 = cp.call(post)
        guid2 = cp.call(post)

        trigger = self.wait_for_events(cp, event='push')
        self.start_master()
        cp.call(db.Request(method='GET', cmd='inline'))
        trigger.wait()

        self.assertEqual([[3, None]], json.load(file('client/push.sequence')))
        self.assertEqual({'en-us': 'title'}, volume['context'].get(guid1)['title'])
        self.assertEqual({'en-us': 'title'}, self.node_volume['context'].get(guid1)['title'])
        self.assertEqual({'en-us': 'title'}, volume['context'].get(guid2)['title'])
        self.assertEqual({'en-us': 'title'}, self.node_volume['context'].get(guid2)['title'])

        trigger = self.wait_for_events(cp, event='inline', state='offline')
        self.node.stop()
        trigger.wait()
        self.node_volume.close()

        volume['context'].update(guid1, {'title': 'title_'})
        volume['context'].delete(guid2)

        trigger = self.wait_for_events(cp, event='push')
        self.start_master()
        cp.call(db.Request(method='GET', cmd='inline'))
        trigger.wait()

        self.assertEqual([[4, None]], json.load(file('client/push.sequence')))
        self.assertEqual({'en-us': 'title_'}, volume['context'].get(guid1)['title'])
        self.assertEqual({'en-us': 'title_'}, self.node_volume['context'].get(guid1)['title'])
        assert not volume['context'].exists(guid2)
        self.assertEqual({'en-us': 'title'}, self.node_volume['context'].get(guid2)['title'])

    def test_CachedClientCommands_WipeReports(self):
        volume = Volume('client')
        cp = CachedClientCommands(volume)

        post = db.Request(method='POST', document='report')
        post.content_type = 'application/json'
        post.content = {
                'context': 'context',
                'description': 'description',
                'error': 'error',
                }
        guid = cp.call(post)

        trigger = self.wait_for_events(cp, event='push')
        self.start_master([User, Report])
        cp.call(db.Request(method='GET', cmd='inline'))
        trigger.wait()

        assert not volume['report'].exists(guid)
        assert self.node_volume['report'].exists(guid)

    def test_SwitchToOfflineForAbsentOnlineProps(self):
        volume = Volume('client')
        cp = ClientCommands(volume)

        post = db.Request(method='POST', document='context')
        post.content_type = 'application/json'
        post.content = {
                'type': 'activity',
                'title': 'title',
                'summary': 'summary',
                'description': 'description',
                }
        guid = cp.call(post)

        self.assertEqual('title', cp.call(db.Request(method='GET', document='context', guid=guid, prop='title')))

        trigger = self.wait_for_events(cp, event='inline', state='online')
        self.start_master()
        cp.call(db.Request(method='GET', cmd='inline'))
        trigger.wait()

        assert not self.node_volume['context'].exists(guid)
        self.assertEqual('title', cp.call(db.Request(method='GET', document='context', guid=guid, prop='title')))


if __name__ == '__main__':
    tests.main()
