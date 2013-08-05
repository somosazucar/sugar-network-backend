#!/usr/bin/env python
# -*- coding: utf-8 -*-
# sugar-lint: disable

import os
import time
import json
import base64
from email.utils import formatdate, parsedate
from cStringIO import StringIO
from os.path import exists

from __init__ import tests

from sugar_network import db, node, model
from sugar_network.client import Connection
from sugar_network.toolkit import http, coroutine
from sugar_network.toolkit.rrd import Rrd
from sugar_network.node import stats_user, stats_node, obs
from sugar_network.node.routes import NodeRoutes
from sugar_network.node.master import MasterRoutes
from sugar_network.model.user import User
from sugar_network.model.context import Context
from sugar_network.model.implementation import Implementation
from sugar_network.model.review import Review
from sugar_network.model.feedback import Feedback
from sugar_network.model.artifact import Artifact
from sugar_network.model.solution import Solution
from sugar_network.model.user import User
from sugar_network.toolkit.router import Router, Request, Response, fallbackroute, Blob, ACL, route


class NodeTest(tests.Test):

    def setUp(self):
        tests.Test.setUp(self)
        node.stats_root.value = 'stats'
        stats_user.stats_user_step.value = 1
        stats_user.stats_user_rras.value = ['RRA:AVERAGE:0.5:1:100']

    def test_UserStats(self):
        volume = db.Volume('db', model.RESOURCES)
        cp = NodeRoutes('guid', volume)

        call(cp, method='POST', document='user', principal=tests.UID, content={
            'name': 'user',
            'color': '',
            'machine_sn': '',
            'machine_uuid': '',
            'pubkey': tests.PUBKEY,
            })

        ts = int(time.time())

        self.assertEqual({
            'enable': True,
            'status': {},
            'rras': ['RRA:AVERAGE:0.5:1:4320', 'RRA:AVERAGE:0.5:5:2016'],
            'step': stats_user.stats_user_step.value,
            },
            call(cp, method='GET', cmd='stats-info', document='user', guid=tests.UID, principal=tests.UID))

        call(cp, method='POST', cmd='stats-upload', document='user', guid=tests.UID, principal=tests.UID, content={
            'name': 'test',
            'values': [(ts + 1, {'field': '1'})],
            })

        self.assertEqual({
            'enable': True, 'status': {
                'test': ts + 2,
                },
            'rras': ['RRA:AVERAGE:0.5:1:4320', 'RRA:AVERAGE:0.5:5:2016'],
            'step': stats_user.stats_user_step.value,
            },
            call(cp, method='GET', cmd='stats-info', document='user', guid=tests.UID, principal=tests.UID))

        call(cp, method='POST', cmd='stats-upload', document='user', guid=tests.UID, principal=tests.UID, content={
            'name': 'test',
            'values': [(ts + 2, {'field': '2'})],
            })

        self.assertEqual({
            'enable': True, 'status': {
                'test': ts + 3,
                },
            'rras': ['RRA:AVERAGE:0.5:1:4320', 'RRA:AVERAGE:0.5:5:2016'],
            'step': stats_user.stats_user_step.value,
            },
            call(cp, method='GET', cmd='stats-info', document='user', guid=tests.UID, principal=tests.UID))

        call(cp, method='POST', cmd='stats-upload', document='user', guid=tests.UID, principal=tests.UID, content={
            'name': 'test2',
            'values': [(ts + 3, {'field': '3'})],
            })

        self.assertEqual({
            'enable': True, 'status': {
                'test': ts + 3,
                'test2': ts + 4,
                },
            'rras': ['RRA:AVERAGE:0.5:1:4320', 'RRA:AVERAGE:0.5:5:2016'],
            'step': stats_user.stats_user_step.value,
            },
            call(cp, method='GET', cmd='stats-info', document='user', guid=tests.UID, principal=tests.UID))

    def test_NodeStats(self):
        stats_node.stats_node.value = True
        stats_node.stats_node_step.value = 1
        rrd = Rrd('stats/node', stats_node.stats_node_step.value, stats_node.stats_node_rras.value)

        ts = int(time.time()) / 3 * 3
        for i in range(100):
            rrd['user'].put({'total': i}, ts + i)

        volume = db.Volume('db', model.RESOURCES)
        cp = NodeRoutes('guid', volume)

        self.assertEqual({
            'user': [
                (ts + 0, {'total': 0.0}),
                (ts + 1, {'total': 1.0}),
                (ts + 2, {'total': 2.0}),
                (ts + 3, {'total': 3.0}),
                ],
            },
            cp.stats(ts, ts + 3, 1, ['user.total']))

        self.assertEqual({
            'user': [
                (ts + 3, {'total': 2.0}),
                (ts + 6, {'total': 5.0}),
                (ts + 9, {'total': 8.0}),
                (ts + 12, {'total': 11.0}),
                ],
            },
            cp.stats(ts, ts + 12, 3, ['user.total']))

    def test_HandleDeletes(self):
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = NodeRoutes('guid', volume)

        guid = call(cp, method='POST', document='context', principal=tests.UID, content={
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })
        guid_path = 'db/context/%s/%s' % (guid[:2], guid)

        assert exists(guid_path)
        self.assertEqual({
            'guid': guid,
            'title': 'title',
            'layer': [],
            },
            call(cp, method='GET', document='context', guid=guid, reply=['guid', 'title', 'layer']))
        self.assertEqual([], volume['context'].get(guid)['layer'])

        def subscribe():
            for event in cp.subscribe():
                events.append(json.loads(event[6:]))
        events = []
        coroutine.spawn(subscribe)
        coroutine.dispatch()

        call(cp, method='DELETE', document='context', guid=guid, principal=tests.UID)
        coroutine.dispatch()
        self.assertRaises(http.NotFound, call, cp, method='GET', document='context', guid=guid, reply=['guid', 'title'])
        self.assertEqual(['deleted'], volume['context'].get(guid)['layer'])
        self.assertEqual({'event': 'delete', 'resource': 'context', 'guid': guid}, events[0])

    def test_SimulateDeleteEvents(self):
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = NodeRoutes('guid', volume)

        guid = call(cp, method='POST', document='context', principal=tests.UID, content={
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })

        def subscribe():
            for event in cp.subscribe():
                events.append(json.loads(event[6:]))
        events = []
        coroutine.spawn(subscribe)
        coroutine.dispatch()

        call(cp, method='PUT', document='context', guid=guid, principal=tests.UID, content={'layer': ['deleted']})
        coroutine.dispatch()
        self.assertEqual({'event': 'delete', 'resource': 'context', 'guid': guid}, events[0])

    def test_RegisterUser(self):
        cp = NodeRoutes('guid', db.Volume('db', [User]))

        guid = call(cp, method='POST', document='user', principal='fake', content={
            'name': 'user',
            'color': '',
            'machine_sn': '',
            'machine_uuid': '',
            'pubkey': tests.PUBKEY,
            })
        assert guid == tests.UID
        self.assertEqual('user', call(cp, method='GET', document='user', guid=tests.UID, prop='name'))

    def test_UnauthorizedCommands(self):
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})

        class Routes(NodeRoutes):

            @route('GET', [None, None], cmd='probe1', acl=ACL.AUTH)
            def probe1(self, directory):
                pass

            @route('GET', [None, None], cmd='probe2')
            def probe2(self, directory):
                pass

        class Document(db.Resource):
            pass

        cp = Routes('guid', db.Volume('db', [User, Document]))
        guid = call(cp, method='POST', document='document', principal=tests.UID, content={})

        self.assertRaises(http.Unauthorized, call, cp, method='GET', cmd='probe1', document='document', guid=guid)
        call(cp, method='GET', cmd='probe1', document='document', guid=guid, principal=tests.UID)
        call(cp, method='GET', cmd='probe2', document='document', guid=guid)

    def test_ForbiddenCommands(self):

        class Routes(NodeRoutes):

            @route('GET', [None, None], cmd='probe1', acl=ACL.AUTHOR)
            def probe1(self):
                pass

            @route('GET', [None, None], cmd='probe2')
            def probe2(self):
                pass

        class Document(db.Resource):
            pass

        volume = db.Volume('db', [User, Document])
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = Routes('guid', volume)

        guid = call(cp, method='POST', document='document', principal=tests.UID, content={})

        self.assertRaises(http.Forbidden, call, cp, method='GET', cmd='probe1', document='document', guid=guid)
        self.assertRaises(http.Unauthorized, call, cp, method='GET', cmd='probe1', document='document', guid=guid, principal='fake')
        call(cp, method='GET', cmd='probe1', document='document', guid=guid, principal=tests.UID)
        call(cp, method='GET', cmd='probe2', document='document', guid=guid)

    def test_ForbiddenCommandsForUserResource(self):
        cp = NodeRoutes('guid', db.Volume('db', [User]))

        call(cp, method='POST', document='user', principal='fake', content={
            'name': 'user1',
            'color': '',
            'machine_sn': '',
            'machine_uuid': '',
            'pubkey': tests.PUBKEY,
            })
        self.assertEqual('user1', call(cp, method='GET', document='user', guid=tests.UID, prop='name'))

        self.assertRaises(http.Unauthorized, call, cp, method='PUT', document='user', guid=tests.UID, content={'name': 'user2'})
        self.assertRaises(http.Unauthorized, call, cp, method='PUT', document='user', guid=tests.UID, principal='fake', content={'name': 'user2'})
        call(cp, method='PUT', document='user', guid=tests.UID, principal=tests.UID, content={'name': 'user2'})
        self.assertEqual('user2', call(cp, method='GET', document='user', guid=tests.UID, prop='name'))

    def test_authorize_Config(self):
        self.touch(('authorization.conf', [
            '[%s]' % tests.UID,
            'root = True',
            ]))

        class Routes(NodeRoutes):

            @route('PROBE', acl=ACL.SUPERUSER)
            def probe(self):
                return 'ok'

        volume = db.Volume('db', [User])
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        volume['user'].create({'guid': tests.UID2, 'name': 'test', 'color': '', 'pubkey': tests.PUBKEY2})
        cp = Routes('guid', volume)

        self.assertRaises(http.Forbidden, call, cp, method='PROBE')
        self.assertRaises(http.Forbidden, call, cp, method='PROBE', principal=tests.UID2)
        self.assertEqual('ok', call(cp, method='PROBE', principal=tests.UID))

    def test_authorize_OnlyAuthros(self):

        class Document(db.Resource):

            @db.indexed_property(slot=1, acl=ACL.PUBLIC | ACL.AUTHOR)
            def prop(self, value):
                return value

        volume = db.Volume('db', [User, Document])
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        volume['user'].create({'guid': tests.UID2, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY2})
        cp = NodeRoutes('guid', volume)

        guid = call(cp, method='POST', document='document', principal=tests.UID, content={'prop': '1'})
        self.assertRaises(http.Forbidden, call, cp, 'PUT', document='document', guid=guid, content={'prop': '2'}, principal=tests.UID2)
        self.assertEqual('1', volume['document'].get(guid)['prop'])

    def test_authorize_FullWriteForRoot(self):
        self.touch(('authorization.conf', [
            '[%s]' % tests.UID2,
            'root = True',
            ]))

        class Document(db.Resource):

            @db.indexed_property(slot=1, acl=ACL.PUBLIC | ACL.AUTHOR)
            def prop(self, value):
                return value

        volume = db.Volume('db', [User, Document])
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        volume['user'].create({'guid': tests.UID2, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY2})
        cp = NodeRoutes('guid', volume)

        guid = call(cp, method='POST', document='document', principal=tests.UID, content={'prop': '1'})

        call(cp, 'PUT', document='document', guid=guid, content={'prop': '2'}, principal=tests.UID)
        self.assertEqual('2', volume['document'].get(guid)['prop'])

        call(cp, 'PUT', document='document', guid=guid, content={'prop': '3'}, principal=tests.UID2)
        self.assertEqual('3', volume['document'].get(guid)['prop'])

    def test_authorize_LiveConfigUpdates(self):

        class Routes(NodeRoutes):

            @route('PROBE', acl=ACL.SUPERUSER)
            def probe(self):
                pass

        volume = db.Volume('db', [User])
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = Routes('guid', volume)

        self.assertRaises(http.Forbidden, call, cp, 'PROBE', principal=tests.UID)
        self.touch(('authorization.conf', [
            '[%s]' % tests.UID,
            'root = True',
            ]))
        call(cp, 'PROBE', principal=tests.UID)

    def test_authorize_Anonymous(self):

        class Routes(NodeRoutes):

            @route('PROBE1', acl=ACL.AUTH)
            def probe1(self, request):
                pass

            @route('PROBE2', acl=ACL.SUPERUSER)
            def probe2(self, request):
                pass

        volume = db.Volume('db', [User])
        cp = Routes('guid', volume)

        self.assertRaises(http.Unauthorized, call, cp, 'PROBE1')
        self.assertRaises(http.Forbidden, call, cp, 'PROBE2')

        self.touch(('authorization.conf', [
            '[anonymous]',
            'user = True',
            'root = True',
            ]))
        call(cp, 'PROBE1')
        call(cp, 'PROBE2')

    def test_SetUser(self):
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = NodeRoutes('guid', volume)

        guid = call(cp, method='POST', document='context', principal=tests.UID, content={
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })
        self.assertEqual(
                [{'guid': tests.UID, 'name': 'user', 'role': 3}],
                call(cp, method='GET', document='context', guid=guid, prop='author'))

    def test_find_MaxLimit(self):
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = NodeRoutes('guid', volume)

        call(cp, method='POST', document='context', principal=tests.UID, content={
            'type': 'activity',
            'title': 'title1',
            'summary': 'summary',
            'description': 'description',
            })
        call(cp, method='POST', document='context', principal=tests.UID, content={
            'type': 'activity',
            'title': 'title2',
            'summary': 'summary',
            'description': 'description',
            })
        call(cp, method='POST', document='context', principal=tests.UID, content={
            'type': 'activity',
            'title': 'title3',
            'summary': 'summary',
            'description': 'description',
            })

        node.find_limit.value = 3
        self.assertEqual(3, len(call(cp, method='GET', document='context', limit=1024)['result']))
        node.find_limit.value = 2
        self.assertEqual(2, len(call(cp, method='GET', document='context', limit=1024)['result']))
        node.find_limit.value = 1
        self.assertEqual(1, len(call(cp, method='GET', document='context', limit=1024)['result']))

    def test_DeletedDocuments(self):
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = NodeRoutes('guid', volume)

        guid = call(cp, method='POST', document='context', principal=tests.UID, content={
            'type': 'activity',
            'title': 'title1',
            'summary': 'summary',
            'description': 'description',
            })

        call(cp, method='GET', document='context', guid=guid)
        self.assertNotEqual([], call(cp, method='GET', document='context')['result'])

        volume['context'].update(guid, {'layer': ['deleted']})

        self.assertRaises(http.NotFound, call, cp, method='GET', document='context', guid=guid)
        self.assertEqual([], call(cp, method='GET', document='context')['result'])

    def test_CreateGUID(self):
        # TODO Temporal security hole, see TODO
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = NodeRoutes('guid', volume)
        call(cp, method='POST', document='context', principal=tests.UID, content={
            'guid': 'foo',
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })
        self.assertEqual(
                {'guid': 'foo', 'title': 'title'},
                call(cp, method='GET', document='context', guid='foo', reply=['guid', 'title']))

    def test_CreateMalformedGUID(self):
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = MasterRoutes('guid', volume)

        self.assertRaises(RuntimeError, call, cp, method='POST', document='context', principal=tests.UID, content={
            'guid': '!?',
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })

    def test_FailOnExistedGUID(self):
        volume = db.Volume('db', model.RESOURCES)
        volume['user'].create({'guid': tests.UID, 'name': 'user', 'color': '', 'pubkey': tests.PUBKEY})
        cp = MasterRoutes('guid', volume)

        guid = call(cp, method='POST', document='context', principal=tests.UID, content={
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })

        self.assertRaises(RuntimeError, call, cp, method='POST', document='context', principal=tests.UID, content={
            'guid': guid,
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })

    def test_PackagesRoute(self):
        node.files_root.value = '.'
        self.touch(('packages/repo/arch/package', 'file'))
        volume = self.start_master()
        client = Connection()

        self.assertEqual(['repo'], client.get(['packages']))
        self.assertEqual(['arch'], client.get(['packages', 'repo']))
        self.assertEqual(['package'], client.get(['packages', 'repo', 'arch']))
        self.assertEqual('file', client.get(['packages', 'repo', 'arch', 'package']))

    def test_PackageUpdatesRoute(self):
        node.files_root.value = '.'
        self.touch(
                ('packages/repo/1', '', 1),
                ('packages/repo/1.1', '', 1),
                ('packages/repo/2', '', 2),
                ('packages/repo/2.2', '', 2),
                )
        volume = self.start_master()
        ipc = Connection()

        self.assertEqual(
                sorted(['1', '2']),
                sorted(ipc.get(['packages', 'repo', 'updates'])))

        response = ipc.request('GET', ['packages', 'repo', 'updates'], headers={'if-modified-since': formatdate(0)})
        self.assertEqual(
                sorted(['1', '2']),
                sorted(json.loads(response.content)))
        self.assertEqual(2, time.mktime(parsedate(response.headers['last-modified'])))

        response = ipc.request('GET', ['packages', 'repo', 'updates'], headers={'if-modified-since': formatdate(1)})
        self.assertEqual(
                sorted(['2']),
                sorted(json.loads(response.content)))
        self.assertEqual(2, time.mktime(parsedate(response.headers['last-modified'])))

        response = ipc.request('GET', ['packages', 'repo', 'updates'], headers={'if-modified-since': formatdate(2)})
        self.assertEqual(
                sorted([]),
                sorted(json.loads(response.content)))
        assert 'last-modified' not in response.headers

        response = ipc.request('GET', ['packages', 'repo', 'updates'], headers={'if-modified-since': formatdate(3)})
        self.assertEqual(
                sorted([]),
                sorted(json.loads(response.content)))
        assert 'last-modified' not in response.headers

    def test_Clone(self):
        volume = self.start_master()
        client = Connection()

        context = client.post(['context'], {
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })
        impl = client.post(['implementation'], {
            'context': context,
            'license': 'GPLv3+',
            'version': '1',
            'stability': 'stable',
            'notes': '',
            })
        blob1 = self.zips(('topdir/probe', 'probe1'))
        volume['implementation'].update(impl, {'data': {
            'blob': StringIO(blob1),
            'spec': {
                '*-*': {
                    'requires': {
                        'dep1': {},
                        },
                    },
                },
            }})
        impl = client.post(['implementation'], {
            'context': context,
            'license': 'GPLv3+',
            'version': '2',
            'stability': 'stable',
            'notes': '',
            })
        blob2 = self.zips(('topdir/probe', 'probe2'))
        volume['implementation'].update(impl, {'data': {
            'blob': StringIO(blob2),
            'spec': {
                '*-*': {
                    'requires': {
                        'dep2': {'restrictions': [[None, '2']]},
                        'dep3': {},
                        },
                    },
                },
            }})
        impl = client.post(['implementation'], {
            'context': context,
            'license': 'GPLv3+',
            'version': '3',
            'stability': 'stable',
            'notes': '',
            })
        blob3 = self.zips(('topdir/probe', 'probe3'))
        volume['implementation'].update(impl, {'data': {
            'blob': StringIO(blob3),
            'spec': {
                '*-*': {
                    'requires': {
                        'dep2': {'restrictions': [['2', None]]},
                        },
                    },
                },
            }})
        impl = client.post(['implementation'], {
            'context': context,
            'license': 'GPLv3+',
            'version': '4',
            'stability': 'developer',
            'notes': '',
            })
        blob4 = self.zips(('topdir/probe', 'probe4'))
        volume['implementation'].update(impl, {'data': {
            'blob': StringIO(blob4),
            'spec': {
                '*-*': {
                    'requires': {},
                    },
                },
            }})

        self.assertEqual(blob3, client.get(['context', context], cmd='clone'))
        self.assertEqual(blob4, client.get(['context', context], cmd='clone', stability='developer'))
        self.assertEqual(blob1, client.get(['context', context], cmd='clone', version='1'))

        self.assertEqual(blob1, client.get(['context', context], cmd='clone', requires='dep1'))
        self.assertEqual(blob3, client.get(['context', context], cmd='clone', requires='dep2'))
        self.assertEqual(blob2, client.get(['context', context], cmd='clone', requires='dep2=1'))
        self.assertEqual(blob3, client.get(['context', context], cmd='clone', requires='dep2=2'))
        self.assertEqual(blob2, client.get(['context', context], cmd='clone', requires='dep3'))

        self.assertRaises(http.NotFound, client.get, ['context', context], cmd='clone', requires='dep4')
        self.assertRaises(http.NotFound, client.get, ['context', context], cmd='clone', stability='foo')

    def test_release(self):
        volume = self.start_master()
        conn = Connection()

        conn.post(['context'], {
            'guid': 'bundle_id',
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })
        activity_info = '\n'.join([
            '[Activity]',
            'name = TestActivitry',
            'bundle_id = bundle_id',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            'stability = developer',
            'requires = sugar>=0.88; dep'
            ])
        bundle1 = self.zips(('topdir/activity/activity.info', activity_info))
        guid1 = json.load(conn.request('POST', ['implementation'], bundle1, params={'cmd': 'release'}).raw)

        impl = volume['implementation'].get(guid1)
        self.assertEqual('bundle_id', impl['context'])
        self.assertEqual('1', impl['version'])
        self.assertEqual('developer', impl['stability'])
        self.assertEqual(['Public Domain'], impl['license'])
        self.assertEqual('developer', impl['stability'])

        data = impl.meta('data')
        self.assertEqual({
            '*-*': {
                'extract': 'topdir',
                'commands': {'activity': {'exec': 'true'}},
                'requires': {'dep': {}, 'sugar': {'restrictions': [['0.88', None]]}},
                },
            },
            data['spec'])

        self.assertEqual('application/vnd.olpc-sugar', data['mime_type'])
        self.assertEqual(len(bundle1), data['blob_size'])
        self.assertEqual(len(activity_info), data.get('unpack_size'))
        self.assertEqual(bundle1, conn.get(['context', 'bundle_id'], cmd='clone', stability='developer'))

        activity_info = '\n'.join([
            '[Activity]',
            'name = TestActivitry',
            'bundle_id = bundle_id',
            'exec = true',
            'icon = icon',
            'activity_version = 2',
            'license = Public Domain',
            'stability = stable',
            ])
        bundle2 = self.zips(('topdir/activity/activity.info', activity_info))
        guid2 = json.load(conn.request('POST', ['implementation'], bundle2, params={'cmd': 'release'}).raw)

        self.assertEqual('1', volume['implementation'].get(guid1)['version'])
        self.assertEqual([], volume['implementation'].get(guid1)['layer'])
        self.assertEqual('2', volume['implementation'].get(guid2)['version'])
        self.assertEqual([], volume['implementation'].get(guid2)['layer'])
        self.assertEqual(bundle2, conn.get(['context', 'bundle_id'], cmd='clone'))

        activity_info = '\n'.join([
            '[Activity]',
            'name = TestActivitry',
            'bundle_id = bundle_id',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            'stability = stable',
            ])
        bundle3 = self.zips(('topdir/activity/activity.info', activity_info))
        guid3 = json.load(conn.request('POST', ['implementation'], bundle3, params={'cmd': 'release'}).raw)

        self.assertEqual('1', volume['implementation'].get(guid1)['version'])
        self.assertEqual(['deleted'], volume['implementation'].get(guid1)['layer'])
        self.assertEqual('2', volume['implementation'].get(guid2)['version'])
        self.assertEqual([], volume['implementation'].get(guid2)['layer'])
        self.assertEqual('1', volume['implementation'].get(guid3)['version'])
        self.assertEqual([], volume['implementation'].get(guid3)['layer'])
        self.assertEqual(bundle2, conn.get(['context', 'bundle_id'], cmd='clone'))

        activity_info = '\n'.join([
            '[Activity]',
            'name = TestActivitry',
            'bundle_id = bundle_id',
            'exec = true',
            'icon = icon',
            'activity_version = 2',
            'license = Public Domain',
            'stability = buggy',
            ])
        bundle4 = self.zips(('topdir/activity/activity.info', activity_info))
        guid4 = json.load(conn.request('POST', ['implementation'], bundle4, params={'cmd': 'release'}).raw)

        self.assertEqual('1', volume['implementation'].get(guid1)['version'])
        self.assertEqual(['deleted'], volume['implementation'].get(guid1)['layer'])
        self.assertEqual('2', volume['implementation'].get(guid2)['version'])
        self.assertEqual(['deleted'], volume['implementation'].get(guid2)['layer'])
        self.assertEqual('1', volume['implementation'].get(guid3)['version'])
        self.assertEqual([], volume['implementation'].get(guid3)['layer'])
        self.assertEqual('2', volume['implementation'].get(guid4)['version'])
        self.assertEqual([], volume['implementation'].get(guid4)['layer'])
        self.assertEqual(bundle3, conn.get(['context', 'bundle_id'], cmd='clone'))

    def test_release_LoadMetadata(self):
        volume = self.start_master()
        conn = Connection()

        conn.post(['context'], {
            'guid': 'org.laptop.ImageViewerActivity',
            'type': 'activity',
            'title': {'en': ''},
            'summary': {'en': ''},
            'description': {'en': ''},
            })
        svg = '\n'.join([
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" [',
            '  <!ENTITY fill_color "#FFFFFF">',
            '  <!ENTITY stroke_color "#010101">',
            ']>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="50" height="50">',
            '    <rect x="3" y="7" width="44" height="36" style="fill:&fill_color;;stroke:&stroke_color;;stroke-width:3"/>',
            '    <polyline points="15,7 25,1 35,7" style="fill:none;;stroke:&stroke_color;;stroke-width:1.25"/>',
            '    <circle cx="14" cy="19" r="4.5" style="fill:&stroke_color;;stroke:&stroke_color;;stroke-width:1.5"/>',
            '    <polyline points="3,36 16,32 26,35" style="fill:none;;stroke:&stroke_color;;stroke-width:2.5"/>',
            '    <polyline points="15,43 37,28 47,34 47,43" style="fill:&stroke_color;;stroke:&stroke_color;;stroke-width:3"/>',
            '    <polyline points="22,41.5 35,30 27,41.5" style="fill:&fill_color;;stroke:none;;stroke-width:0"/>',
            '    <polyline points="26,23 28,25 30,23" style="fill:none;;stroke:&stroke_color;;stroke-width:.9"/>',
            '    <polyline points="31.2,20 33.5,17.7 35.8,20" style="fill:none;;stroke:&stroke_color;;stroke-width:1"/>',
            '    <polyline points="36,13 38.5,15.5 41,13" style="fill:none;;stroke:&stroke_color;;stroke-width:1"/>',
            '</svg>',
            ])
        bundle = self.zips(
                ('ImageViewer.activity/activity/activity.info', '\n'.join([
                    '[Activity]',
                    'bundle_id = org.laptop.ImageViewerActivity',
                    'name      = Image Viewer',
                    'summary   = The Image Viewer activity is a simple and fast image viewer tool',
                    'description = It has features one would expect of a standard image viewer, like zoom, rotate, etc.',
                    'homepage  = http://wiki.sugarlabs.org/go/Activities/Image_Viewer',
                    'activity_version = 22',
                    'license   = GPLv2+',
                    'icon      = activity-imageviewer',
                    'exec      = true',
                    'mime_types = image/bmp;image/gif',
                    ])),
                ('ImageViewer.activity/locale/ru/LC_MESSAGES/org.laptop.ImageViewerActivity.mo',
                    base64.b64decode('3hIElQAAAAAMAAAAHAAAAHwAAAARAAAA3AAAAAAAAAAgAQAADwAAACEBAAAOAAAAMQEAAA0AAABAAQAACgAAAE4BAAAMAAAAWQEAAA0AAABmAQAAJwAAAHQBAAAUAAAAnAEAABAAAACxAQAABwAAAMIBAAAIAAAAygEAANEBAADTAQAAIQAAAKUDAAATAAAAxwMAABwAAADbAwAAFwAAAPgDAAAhAAAAEAQAAB0AAAAyBAAAQAAAAFAEAAA9AAAAkQQAADUAAADPBAAAFAAAAAUFAAAQAAAAGgUAAAEAAAACAAAABwAAAAAAAAADAAAAAAAAAAwAAAAJAAAAAAAAAAoAAAAEAAAAAAAAAAAAAAALAAAABgAAAAgAAAAFAAAAAENob29zZSBkb2N1bWVudABEb3dubG9hZGluZy4uLgBGaXQgdG8gd2luZG93AEZ1bGxzY3JlZW4ASW1hZ2UgVmlld2VyAE9yaWdpbmFsIHNpemUAUmV0cmlldmluZyBzaGFyZWQgaW1hZ2UsIHBsZWFzZSB3YWl0Li4uAFJvdGF0ZSBhbnRpY2xvY2t3aXNlAFJvdGF0ZSBjbG9ja3dpc2UAWm9vbSBpbgBab29tIG91dABQcm9qZWN0LUlkLVZlcnNpb246IFBBQ0tBR0UgVkVSU0lPTgpSZXBvcnQtTXNnaWQtQnVncy1UbzogClBPVC1DcmVhdGlvbi1EYXRlOiAyMDEyLTA5LTI3IDE0OjU3LTA0MDAKUE8tUmV2aXNpb24tRGF0ZTogMjAxMC0wOS0yMiAxMzo1MCswMjAwCkxhc3QtVHJhbnNsYXRvcjoga3JvbTlyYSA8a3JvbTlyYUBnbWFpbC5jb20+Ckxhbmd1YWdlLVRlYW06IExBTkdVQUdFIDxMTEBsaS5vcmc+Ckxhbmd1YWdlOiAKTUlNRS1WZXJzaW9uOiAxLjAKQ29udGVudC1UeXBlOiB0ZXh0L3BsYWluOyBjaGFyc2V0PVVURi04CkNvbnRlbnQtVHJhbnNmZXItRW5jb2Rpbmc6IDhiaXQKUGx1cmFsLUZvcm1zOiBucGx1cmFscz0zOyBwbHVyYWw9KG4lMTA9PTEgJiYgbiUxMDAhPTExID8gMCA6IG4lMTA+PTIgJiYgbiUxMDw9NCAmJiAobiUxMDA8MTAgfHwgbiUxMDA+PTIwKSA/IDEgOiAyKTsKWC1HZW5lcmF0b3I6IFBvb3RsZSAyLjAuMwoA0JLRi9Cx0LXRgNC40YLQtSDQtNC+0LrRg9C80LXQvdGCANCX0LDQs9GA0YPQt9C60LAuLi4A0KPQvNC10YHRgtC40YLRjCDQsiDQvtC60L3QtQDQn9C+0LvQvdGL0Lkg0Y3QutGA0LDQvQDQn9GA0L7RgdC80L7RgtGAINC60LDRgNGC0LjQvdC+0LoA0JjRgdGC0LjQvdC90YvQuSDRgNCw0LfQvNC10YAA0J/QvtC70YPRh9C10L3QuNC1INC40LfQvtCx0YDQsNC20LXQvdC40LksINC/0L7QtNC+0LbQtNC40YLQtS4uLgDQn9C+0LLQtdGA0L3Rg9GC0Ywg0L/RgNC+0YLQuNCyINGH0LDRgdC+0LLQvtC5INGB0YLRgNC10LvQutC4ANCf0L7QstC10YDQvdGD0YLRjCDQv9C+INGH0LDRgdC+0LLQvtC5INGB0YLRgNC10LvQutC1ANCf0YDQuNCx0LvQuNC30LjRgtGMANCe0YLQtNCw0LvQuNGC0YwA')),
                ('ImageViewer.activity/activity/activity-imageviewer.svg', svg),
                )
        impl = json.load(conn.request('POST', ['implementation'], bundle, params={'cmd': 'release'}).raw)

        context = volume['context'].get('org.laptop.ImageViewerActivity')
        self.assertEqual({
            'en': 'Image Viewer',
            'ru': u'Просмотр картинок',
            },
            context['title'])
        self.assertEqual({
            'en': 'The Image Viewer activity is a simple and fast image viewer tool',
            },
            context['summary'])
        self.assertEqual({
            'en': 'It has features one would expect of a standard image viewer, like zoom, rotate, etc.',
            },
            context['description'])
        self.assertEqual(svg, file(context['artifact_icon']['blob']).read())
        assert 'blob' in context['icon']
        assert 'blob' in context['preview']
        self.assertEqual('http://wiki.sugarlabs.org/go/Activities/Image_Viewer', context['homepage'])
        self.assertEqual(['image/bmp', 'image/gif'], context['mime_types'])


def call(routes, method, document=None, guid=None, prop=None, principal=None, cmd=None, content=None, **kwargs):
    path = []
    if document:
        path.append(document)
    if guid:
        path.append(guid)
    if prop:
        path.append(prop)
    request = Request(method=method, path=path)
    request.update(kwargs)
    request.cmd = cmd
    request.content = content
    request.environ = {'HTTP_HOST': '127.0.0.1'}
    if principal:
        request.environ['HTTP_X_SN_LOGIN'] = principal
    router = Router(routes)
    return router.call(request, Response())


if __name__ == '__main__':
    tests.main()
