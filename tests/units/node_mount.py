#!/usr/bin/env python
# sugar-lint: disable

import os
import socket
from os.path import exists

from __init__ import tests

import active_document as ad
from active_toolkit import coroutine, sockets
from sugar_network.local.mounts import HomeMount
from sugar_network.local.mountset import Mountset
from sugar_network.local.bus import IPCServer
from sugar_network.resources.user import User
from sugar_network.resources.context import Context
from sugar_network import local, Client, ServerError, sugar
from sugar_network.resources.volume import Volume


class NodeMountTest(tests.Test):

    def setUp(self):
        tests.Test.setUp(self)

    def tearDown(self):
        tests.Test.tearDown(self)

    def start_server(self):
        local.mounts_root.value = tests.tmpdir

        volume = Volume('local', [User, Context])
        mounts = Mountset(volume)
        ipc_server = IPCServer(mounts)
        coroutine.spawn(ipc_server.serve_forever)
        coroutine.dispatch()
        self.got_event = coroutine.Event()

        def events_cb(event):
            if event['event'] in ('mount', 'unmount'):
                self.events.append((event['event'], event['mountpoint']))
                self.got_event.set()

        self.events = []
        Client.connect(events_cb)
        coroutine.sleep(.1)
        mounts.open()

        return mounts

    def test_GetKeep(self):
        os.makedirs('mnt/sugar-network')
        mounts = self.start_server()
        self.got_event.wait()

        remote = Client(tests.tmpdir + '/mnt')

        guid = remote.Context(
                type='activity',
                title='remote',
                summary='summary',
                description='description').post()

        context = remote.Context(guid, ['keep', 'keep_impl'])
        self.assertEqual(False, context['keep'])
        self.assertEqual(0, context['keep_impl'])
        self.assertEqual(
                [(guid, False, False)],
                [(i['guid'], i['keep'], i['keep_impl']) for i in remote.Context.cursor(reply=['keep', 'keep_impl'])])

        mounts.home_volume['context'].create_with_guid(guid, {
            'type': 'activity',
            'title': {'en': 'local'},
            'summary': {'en': 'summary'},
            'description': {'en': 'description'},
            'keep': True,
            'keep_impl': 2,
            'user': [sugar.uid()],
            })

        context = remote.Context(guid, ['keep', 'keep_impl'])
        self.assertEqual(True, context['keep'])
        self.assertEqual(2, context['keep_impl'])
        self.assertEqual(
                [(guid, True, 2)],
                [(i['guid'], i['keep'], i['keep_impl']) for i in remote.Context.cursor(reply=['keep', 'keep_impl'])])

    def test_SetKeep(self):
        os.makedirs('mnt/sugar-network')
        mounts = self.start_server()
        mounts['~'] = HomeMount(mounts.home_volume)
        self.got_event.wait()
        remote = Client(tests.tmpdir + '/mnt')
        local = Client('~')

        guid_1 = remote.Context(
                type=['activity'],
                title='remote',
                summary='summary',
                description='description').post()
        guid_2 = remote.Context(
                type=['activity'],
                title='remote-2',
                summary='summary',
                description='description').post()

        self.assertRaises(ServerError, lambda: local.Context(guid_1, reply=['title'])['title'])
        self.assertRaises(ServerError, lambda: local.Context(guid_2, reply=['title'])['title'])

        remote.Context(guid_1, keep=True).post()

        cursor = local.Context.cursor(reply=['keep', 'keep_impl', 'title'])
        self.assertEqual(
                sorted([
                    (guid_1, 'remote', True, 0),
                    ]),
                sorted([(i.guid, i['title'], i['keep'], i['keep_impl']) for i in cursor]))
        cursor = remote.Context.cursor(reply=['keep', 'keep_impl', 'title'])
        self.assertEqual(
                sorted([
                    (guid_1, 'remote', True, 0),
                    (guid_2, 'remote-2', False, 0),
                    ]),
                sorted([(i.guid, i['title'], i['keep'], i['keep_impl']) for i in cursor]))

        remote.Context(guid_1, keep=False).post()

        cursor = local.Context.cursor(reply=['keep', 'keep_impl', 'title'])
        self.assertEqual(
                sorted([
                    (guid_1, 'remote', False, 0),
                    ]),
                sorted([(i.guid, i['title'], i['keep'], i['keep_impl']) for i in cursor]))
        cursor = remote.Context.cursor(reply=['keep', 'keep_impl', 'title'])
        self.assertEqual(
                sorted([
                    (guid_1, 'remote', False, 0),
                    (guid_2, 'remote-2', False, 0),
                    ]),
                sorted([(i.guid, i['title'], i['keep'], i['keep_impl']) for i in cursor]))

        context = local.Context(guid_1)
        context['title'] = 'local'
        context.post()
        context = local.Context(guid_1, reply=['keep', 'keep_impl', 'title'])
        self.assertEqual('local', context['title'])

        remote.Context(guid_1, keep=True).post()

        cursor = local.Context.cursor(reply=['keep', 'keep_impl', 'title'])
        self.assertEqual(
                sorted([
                    (guid_1, 'local', True, 0),
                    ]),
                sorted([(i.guid, i['title'], i['keep'], i['keep_impl']) for i in cursor]))
        cursor = remote.Context.cursor(reply=['keep', 'keep_impl', 'title'])
        self.assertEqual(
                sorted([
                    (guid_1, 'remote', True, 0),
                    (guid_2, 'remote-2', False, 0),
                    ]),
                sorted([(i.guid, i['title'], i['keep'], i['keep_impl']) for i in cursor]))

    def test_OnlineSubscription(self):
        os.makedirs('mnt/sugar-network')
        self.start_server()
        self.got_event.wait()
        client = Client(tests.tmpdir + '/mnt')

        subscription = sockets.SocketFile(coroutine.socket(socket.AF_UNIX))
        subscription.connect('run/subscribe')
        coroutine.sleep(1)

        guid = client.Context(
                type='activity',
                title='title',
                summary='summary',
                description='description').post()

        event = subscription.read_message()
        event.pop('props')
        self.assertEqual(
                {'mountpoint': tests.tmpdir + '/mnt', 'document': 'context', 'event': 'create', 'guid': guid},
                event)
        self.assertEqual(
                {'mountpoint': tests.tmpdir + '/mnt', 'document': 'context', 'event': 'commit', 'seqno': 1},
                subscription.read_message())

        client.Context(guid, title='new-title').post()

        coroutine.select([subscription.fileno()], [], [])
        event = subscription.read_message()
        event.pop('props')
        self.assertEqual(
                {'mountpoint': tests.tmpdir + '/mnt', 'document': 'context', 'event': 'update', 'guid': guid},
                event)
        self.assertEqual(
                {'mountpoint': tests.tmpdir + '/mnt', 'document': 'context', 'event': 'commit', 'seqno': 2},
                subscription.read_message())

        guid_path = 'mnt/sugar-network/context/%s/%s' % (guid[:2], guid)
        assert exists(guid_path)
        client.Context.delete(guid)
        assert not exists(guid_path)

        coroutine.select([subscription.fileno()], [], [])
        self.assertEqual(
                {'mountpoint': tests.tmpdir + '/mnt', 'document': 'context', 'event': 'delete', 'guid': guid},
                subscription.read_message())

    def test_upload_blob(self):
        os.makedirs('mnt/sugar-network')
        self.start_server()
        self.got_event.wait()
        remote = Client(tests.tmpdir + '/mnt')

        guid = remote.Context(
                type='activity',
                title='title',
                summary='summary',
                description='description').post()

        self.touch(('file', 'blob'))
        remote.Context(guid).upload_blob('preview', 'file')
        self.assertEqual('blob', remote.Context(guid).get_blob('preview').read())

        self.touch(('file2', 'blob2'))
        remote.Context(guid).upload_blob('preview', 'file2', pass_ownership=True)
        self.assertEqual('blob2', remote.Context(guid).get_blob('preview').read())
        assert not exists('file2')

    def test_GetAbsetnBLOB(self):
        os.makedirs('mnt/sugar-network')
        self.start_server()
        self.got_event.wait()
        client = Client(tests.tmpdir + '/mnt')

        guid = client.Context(
                type='activity',
                title='title',
                summary='summary',
                description='description').post()

        path, mime_type = client.Context(guid).get_blob_path('icon')
        self.assertEqual(None, path)
        self.assertEqual(True, client.Context(guid).get_blob('icon').closed)


if __name__ == '__main__':
    tests.main()
