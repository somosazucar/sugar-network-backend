#!/usr/bin/env python
# -*- coding: utf-8 -*-
# sugar-lint: disable

import json
from cStringIO import StringIO

from __init__ import tests

from sugar_network import db, client, model
from sugar_network.client import journal, IPCConnection
from sugar_network.client.routes import ClientRoutes, CachedClientRoutes
from sugar_network.model.user import User
from sugar_network.model.report import Report
from sugar_network.toolkit.router import Router, Request, Response
from sugar_network.toolkit import coroutine

import requests


class RoutesTest(tests.Test):

    def test_Hub(self):
        volume = db.Volume('db', model.RESOURCES)
        cp = ClientRoutes(volume)
        server = coroutine.WSGIServer(
                ('127.0.0.1', client.ipc_port.value), Router(cp))
        coroutine.spawn(server.serve_forever)
        coroutine.dispatch()

        url = 'http://127.0.0.1:%s' % client.ipc_port.value

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

    def test_LocalLayers(self):
        self.home_volume = self.start_online_client()
        ipc = IPCConnection()

        guid1 = ipc.post(['context'], {
            'guid': 'context1',
            'type': 'activity',
            'title': '1',
            'summary': 'summary',
            'description': 'description',
            })
        ipc.upload(['implementation'], StringIO(self.zips(['TestActivity/activity/activity.info', [
            '[Activity]',
            'name = 2',
            'bundle_id = context2',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            'stability = stable',
            ]])), cmd='submit', initial=True)
        guid2 = 'context2'
        ipc.upload(['implementation'], StringIO(self.zips(['TestActivity/activity/activity.info', [
            '[Activity]',
            'name = 3',
            'bundle_id = context3',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            'stability = stable',
            ]])), cmd='submit', initial=True)
        guid3 = 'context3'
        guid4 = ipc.post(['context'], {
            'guid': 'context4',
            'type': 'activity',
            'title': '4',
            'summary': 'summary',
            'description': 'description',
            })

        self.assertEqual([
            {'guid': guid1, 'title': '1', 'layer': []},
            {'guid': guid2, 'title': '2', 'layer': []},
            {'guid': guid3, 'title': '3', 'layer': []},
            {'guid': guid4, 'title': '4', 'layer': []},
            ],
            ipc.get(['context'], reply=['guid', 'title', 'layer'])['result'])
        self.assertEqual([
            ],
            ipc.get(['context'], reply=['guid', 'title'], layer='favorite')['result'])
        self.assertEqual([
            ],
            ipc.get(['context'], reply=['guid', 'title'], layer='clone')['result'])

        ipc.put(['context', guid1], True, cmd='favorite')
        ipc.put(['context', guid2], True, cmd='favorite')
        ipc.put(['context', guid2], True, cmd='clone')
        ipc.put(['context', guid3], True, cmd='clone')
        self.home_volume['context'].update(guid1, {'title': '1_'})
        self.home_volume['context'].update(guid2, {'title': '2_'})
        self.home_volume['context'].update(guid3, {'title': '3_'})

        self.assertEqual([
            {'guid': guid1, 'title': '1', 'layer': ['favorite']},
            {'guid': guid2, 'title': '2', 'layer': ['clone', 'favorite']},
            {'guid': guid3, 'title': '3', 'layer': ['clone']},
            {'guid': guid4, 'title': '4', 'layer': []},
            ],
            ipc.get(['context'], reply=['guid', 'title', 'layer'])['result'])
        self.assertEqual([
            {'guid': guid1, 'title': '1_'},
            {'guid': guid2, 'title': '2_'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], layer='favorite')['result'])
        self.assertEqual([
            {'guid': guid2, 'title': '2_'},
            {'guid': guid3, 'title': '3_'},
            ],
            ipc.get(['context'], reply=['guid', 'title'], layer='clone')['result'])

    def test_SetLocalLayerInOffline(self):
        volume = db.Volume('client', model.RESOURCES)
        cp = ClientRoutes(volume, client.api_url.value)
        post = Request(method='POST', path=['context'])
        post.content_type = 'application/json'
        post.content = {
                'type': 'activity',
                'title': 'title',
                'summary': 'summary',
                'description': 'description',
                }

        guid = call(cp, post)
        self.assertEqual(['local'], call(cp, Request(method='GET', path=['context', guid, 'layer'])))

        trigger = self.wait_for_events(cp, event='inline', state='online')
        node_volume = self.start_master()
        call(cp, Request(method='GET', cmd='inline'))
        trigger.wait()

        guid = call(cp, post)
        self.assertEqual([], call(cp, Request(method='GET', path=['context', guid, 'layer'])))

    def test_CachedClientRoutes(self):
        volume = db.Volume('client', model.RESOURCES, lazy_open=True)
        cp = CachedClientRoutes(volume, client.api_url.value)

        post = Request(method='POST', path=['context'])
        post.content_type = 'application/json'
        post.content = {
                'type': 'activity',
                'title': 'title',
                'summary': 'summary',
                'description': 'description',
                'layer': ['foo', 'clone', 'favorite'],
                }
        guid1 = call(cp, post)
        guid2 = call(cp, post)

        trigger = self.wait_for_events(cp, event='push')
        self.start_master()
        call(cp, Request(method='GET', cmd='inline'))
        trigger.wait()

        self.assertEqual([[3, None]], json.load(file('client/push.sequence')))
        self.assertEqual({'en-us': 'title'}, volume['context'].get(guid1)['title'])
        self.assertEqual(['foo', 'clone', 'favorite', 'local'], volume['context'].get(guid1)['layer'])
        self.assertEqual({'en-us': 'title'}, self.node_volume['context'].get(guid1)['title'])
        self.assertEqual(['foo'], self.node_volume['context'].get(guid1)['layer'])
        self.assertEqual(
                {tests.UID: {'role': 3, 'name': 'test', 'order': 0}},
                self.node_volume['context'].get(guid1)['author'])
        self.assertEqual({'en-us': 'title'}, volume['context'].get(guid2)['title'])
        self.assertEqual(['foo', 'clone', 'favorite', 'local'], volume['context'].get(guid2)['layer'])
        self.assertEqual({'en-us': 'title'}, self.node_volume['context'].get(guid2)['title'])
        self.assertEqual(['foo'], self.node_volume['context'].get(guid2)['layer'])
        self.assertEqual(
                {tests.UID: {'role': 3, 'name': 'test', 'order': 0}},
                self.node_volume['context'].get(guid2)['author'])

        trigger = self.wait_for_events(cp, event='inline', state='offline')
        self.node.stop()
        trigger.wait()
        self.node_volume.close()

        volume['context'].update(guid1, {'title': 'title_'})
        volume['context'].delete(guid2)

        trigger = self.wait_for_events(cp, event='push')
        self.start_master()
        call(cp, Request(method='GET', cmd='inline'))
        trigger.wait()

        self.assertEqual([[4, None]], json.load(file('client/push.sequence')))
        self.assertEqual({'en-us': 'title_'}, volume['context'].get(guid1)['title'])
        self.assertEqual({'en-us': 'title_'}, self.node_volume['context'].get(guid1)['title'])
        self.assertEqual(
                {tests.UID: {'role': 3, 'name': 'test', 'order': 0}},
                self.node_volume['context'].get(guid1)['author'])
        assert not volume['context'].exists(guid2)
        self.assertEqual({'en-us': 'title'}, self.node_volume['context'].get(guid2)['title'])
        self.assertEqual(
                {tests.UID: {'role': 3, 'name': 'test', 'order': 0}},
                self.node_volume['context'].get(guid2)['author'])

    def test_CachedClientRoutes_WipeReports(self):
        volume = db.Volume('client', model.RESOURCES, lazy_open=True)
        cp = CachedClientRoutes(volume, client.api_url.value)

        post = Request(method='POST', path=['report'])
        post.content_type = 'application/json'
        post.content = {
                'context': 'context',
                'error': 'error',
                }
        guid = call(cp, post)

        trigger = self.wait_for_events(cp, event='push')
        self.start_master([User, Report])
        call(cp, Request(method='GET', cmd='inline'))
        trigger.wait()

        assert not volume['report'].exists(guid)
        assert self.node_volume['report'].exists(guid)

    def test_CachedClientRoutes_OpenOnlyChangedResources(self):
        volume = db.Volume('client', model.RESOURCES, lazy_open=True)
        cp = CachedClientRoutes(volume, client.api_url.value)
        guid = call(cp, Request(method='POST', path=['context'], content_type='application/json', content={
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            'layer': ['foo', 'clone', 'favorite'],
            }))
        cp.close()

        volume = db.Volume('client', model.RESOURCES, lazy_open=True)
        cp = CachedClientRoutes(volume, client.api_url.value)

        trigger = self.wait_for_events(cp, event='push')
        self.start_master()
        call(cp, Request(method='GET', cmd='inline'))
        trigger.wait()

        self.assertEqual([[2, None]], json.load(file('client/push.sequence')))
        assert self.node_volume['context'].exists(guid)
        self.assertEqual(['context'], volume.keys())

    def test_SwitchToOfflineForAbsentOnlineProps(self):
        volume = db.Volume('client', model.RESOURCES)
        cp = ClientRoutes(volume, client.api_url.value)

        post = Request(method='POST', path=['context'])
        post.content_type = 'application/json'
        post.content = {
                'type': 'activity',
                'title': 'title',
                'summary': 'summary',
                'description': 'description',
                }
        guid = call(cp, post)

        self.assertEqual('title', call(cp, Request(method='GET', path=['context', guid, 'title'])))

        trigger = self.wait_for_events(cp, event='inline', state='online')
        self.start_master()
        call(cp, Request(method='GET', cmd='inline'))
        trigger.wait()

        assert not self.node_volume['context'].exists(guid)
        self.assertEqual('title', call(cp, Request(method='GET', path=['context', guid, 'title'])))

    def test_I18nQuery(self):
        client.accept_language.value = 'foo'
        self.start_online_client()
        ipc = IPCConnection()

        guid1 = self.node_volume['context'].create({
            'type': 'activity',
            'title': {'en-US': 'qwe', 'ru-RU': 'йцу'},
            'summary': 'summary',
            'description': 'description',
            })
        guid2 = self.node_volume['context'].create({
            'type': 'activity',
            'title': {'en-US': 'qwerty', 'ru-RU': 'йцукен'},
            'summary': 'summary',
            'description': 'description',
            })

        self.assertEqual([
            {'guid': guid1},
            {'guid': guid2},
            ],
            ipc.get(['context'], query='йцу')['result'])
        self.assertEqual([
            {'guid': guid1},
            {'guid': guid2},
            ],
            ipc.get(['context'], query='qwe')['result'])

        self.assertEqual([
            {'guid': guid2},
            ],
            ipc.get(['context'], query='йцукен')['result'])
        self.assertEqual([
            {'guid': guid2},
            ],
            ipc.get(['context'], query='qwerty')['result'])


def call(routes, request):
    router = Router(routes)
    return router.call(request, Response())


if __name__ == '__main__':
    tests.main()