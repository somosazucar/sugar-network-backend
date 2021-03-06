#!/usr/bin/env python
# sugar-lint: disable

import os
import time
import json
import shutil
import hashlib
from cStringIO import StringIO
from os.path import exists, join, basename

from __init__ import tests

from sugar_network import db, client
from sugar_network.client import Connection, api, injector as injector_, model
from sugar_network.client.injector import _PreemptivePool, Injector
from sugar_network.client.model import Volume as LocalVolume
from sugar_network.toolkit.coroutine import this
from sugar_network.toolkit import http, lsb_release, packagekit


class InjectorTest(tests.Test):

    def setUp(self):
        tests.Test.setUp(self)

        class statvfs(object):
            f_blocks = 100
            f_bfree = 999999999
            f_frsize = 1

        self.statvfs = statvfs
        self.override(os, 'statvfs', lambda *args: statvfs())

    def test_PreemptivePool_push(self):
        cache = _PreemptivePool('releases', None, None, None)

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 1)
        self.touch(('releases/2', '2'))
        self.utime('releases/2', 2)
        self.touch(('releases/3', '3'))
        self.utime('releases/3', 3)

        cache.push([{'blob': '1', 'size': 11}])
        cache.push([{'blob': '2', 'size': 1000, 'unpack_size': 22}])
        cache.push([{'size': 2000}])
        self.assertEqual([
            ('1', (11, 1)),
            ('2', (22, 2)),
            ],
            [i for i in cache])
        self.assertEqual(33, cache._du)

        cache.push([{'size': 3000}])
        cache.push([{'blob': '3', 'size': 1000, 'unpack_size': 33}])
        self.assertEqual([
            ('1', (11, 1)),
            ('2', (22, 2)),
            ('3', (33, 3)),
            ],
            [i for i in cache])
        self.assertEqual(66, cache._du)

    def test_PreemptivePool_pop(self):
        cache = _PreemptivePool('releases', None, None, None)

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 1)
        self.touch(('releases/2', '2'))
        self.utime('releases/2', 2)
        self.touch(('releases/3', '3'))
        self.utime('releases/3', 3)

        cache.push([{'blob': '1', 'size': 1}])
        cache.push([{'blob': '2', 'size': 2}])
        cache.push([{'blob': '3', 'size': 3}])
        self.assertEqual(
                [('1', (1, 1)), ('2', (2, 2)), ('3', (3, 3))],
                [i for i in cache])
        self.assertEqual(6, cache._du)

        assert not cache.pop([{'blob': 'fake'}])
        self.assertEqual(
                [('1', (1, 1)), ('2', (2, 2)), ('3', (3, 3))],
                [i for i in cache])
        self.assertEqual(6, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')
        assert exists('releases/3')

        assert cache.pop([{'blob': '2'}])
        self.assertEqual(
                [('1', (1, 1)), ('3', (3, 3))],
                [i for i in cache])
        self.assertEqual(4, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')
        assert exists('releases/3')

        assert cache.pop([{'blob': '1'}])
        self.assertEqual(
                [('3', (3, 3))],
                [i for i in cache])
        self.assertEqual(3, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')
        assert exists('releases/3')

        assert cache.pop([{'blob': '3'}])
        self.assertEqual(
                [],
                [i for i in cache])
        self.assertEqual(0, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')
        assert exists('releases/3')

    def test_PreemptivePool_RestoreAfterClosing(self):
        cache = _PreemptivePool('./releases', None, None, None)

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 1)
        cache.push([{'blob': '1', 'size': 1}])

        self.assertEqual(
                [('1', (1, 1))],
                [i for i in cache])
        self.assertEqual(1, cache._du)

        assert not exists('releases.index')
        cache.close()
        assert exists('releases.index')

        cache2 = _PreemptivePool('./releases', None, None, None)
        self.assertEqual(
                [('1', [1, 1])],
                [i for i in cache2])
        self.assertEqual(1, cache2._du)

    def test_PreemptivePool_EnsureLimitInBytes(self):
        cache = _PreemptivePool('./releases', None, 10, None)
        self.statvfs.f_bfree = 11

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 1)
        cache.push([{'blob': '1', 'size': 5}])

        self.touch(('releases/2', '2'))
        self.utime('releases/2', 2)
        cache.push([{'blob': '2', 'size': 5}])

        self.assertRaises(RuntimeError, cache.ensure, 12, 0)
        self.assertEqual(
                [('1', (5, 1)), ('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(10, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')

        cache.ensure(1, 0)
        self.assertEqual(
                [('1', (5, 1)), ('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(10, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')

        cache.ensure(2, 0)
        self.assertEqual(
                [('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(5, cache._du)
        assert not exists('releases/1')
        assert exists('releases/2')

        cache.ensure(1, 0)
        self.assertEqual(
                [('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(5, cache._du)
        assert not exists('releases/1')
        assert exists('releases/2')

        self.assertRaises(RuntimeError, cache.ensure, 7, 0)
        self.assertEqual(
                [('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(5, cache._du)
        assert not exists('releases/1')
        assert exists('releases/2')

        cache.ensure(6, 0)
        self.assertEqual(
                [],
                [i for i in cache])
        self.assertEqual(0, cache._du)
        assert not exists('releases/1')
        assert not exists('releases/2')

    def test_PreemptivePool_EnsureLimitInPercents(self):
        cache = _PreemptivePool('./releases', None, None, 10)
        self.statvfs.f_bfree = 11

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 1)
        cache.push([{'blob': '1', 'size': 5}])

        self.touch(('releases/2', '2'))
        self.utime('releases/2', 2)
        cache.push([{'blob': '2', 'size': 5}])

        self.assertRaises(RuntimeError, cache.ensure, 12, 0)
        self.assertEqual(
                [('1', (5, 1)), ('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(10, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')

        cache.ensure(1, 0)
        self.assertEqual(
                [('1', (5, 1)), ('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(10, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')

        cache.ensure(2, 0)
        self.assertEqual(
                [('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(5, cache._du)
        assert not exists('releases/1')
        assert exists('releases/2')

        cache.ensure(1, 0)
        self.assertEqual(
                [('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(5, cache._du)
        assert not exists('releases/1')
        assert exists('releases/2')

        self.assertRaises(RuntimeError, cache.ensure, 7, 0)
        self.assertEqual(
                [('2', (5, 2))],
                [i for i in cache])
        self.assertEqual(5, cache._du)
        assert not exists('releases/1')
        assert exists('releases/2')

        cache.ensure(6, 0)
        self.assertEqual(
                [],
                [i for i in cache])
        self.assertEqual(0, cache._du)
        assert not exists('releases/1')
        assert not exists('releases/2')

    def test_PreemptivePool_EnsureWithTmpSize(self):
        cache = _PreemptivePool('./releases', None, 10, None)
        self.statvfs.f_bfree = 11

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 1)
        cache.push([{'blob': '1', 'size': 5}])

        self.assertRaises(RuntimeError, cache.ensure, 7, 0)
        self.assertEqual(
                [('1', (5, 1))],
                [i for i in cache])
        self.assertEqual(5, cache._du)
        assert exists('releases/1')

        cache.ensure(6, 0)
        self.assertEqual(
                [],
                [i for i in cache])
        self.assertEqual(0, cache._du)
        assert not exists('releases/1')

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 1)
        cache.push([{'blob': '1', 'size': 5}])

        cache.ensure(6, 10)
        self.assertEqual(
                [],
                [i for i in cache])
        self.assertEqual(0, cache._du)
        assert not exists('releases/1')

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 1)
        cache.push([{'blob': '1', 'size': 5}])

        self.assertRaises(RuntimeError, cache.ensure, 6, 11)
        self.assertEqual(
                [('1', (5, 1))],
                [i for i in cache])
        self.assertEqual(5, cache._du)
        assert exists('releases/1')

    def test_PreemptivePool_RecycleByLifetime(self):
        cache = _PreemptivePool('./releases', 1, None, None)

        self.touch(('releases/1', '1'))
        self.utime('releases/1', 0)
        cache.push([{'blob': '1', 'size': 1}])
        self.touch(('releases/2', '2'))
        self.utime('releases/2', 86400)
        cache.push([{'blob': '2', 'size': 1}])

        self.override(time, 'time', lambda: 86400)
        cache.recycle()
        self.assertEqual(
                [('1', (1, 0)), ('2', (1, 86400))],
                [i for i in cache])
        self.assertEqual(2, cache._du)
        assert exists('releases/1')
        assert exists('releases/2')

        self.override(time, 'time', lambda: 86400 * 1.5)
        cache.recycle()
        self.assertEqual(
                [('2', (1, 86400))],
                [i for i in cache])
        self.assertEqual(1, cache._du)
        assert not exists('releases/1')
        assert exists('releases/2')

        self.override(time, 'time', lambda: 86400 * 1.5)
        cache.recycle()
        self.assertEqual(
                [('2', (1, 86400))],
                [i for i in cache])
        self.assertEqual(1, cache._du)
        assert not exists('releases/1')
        assert exists('releases/2')

        self.override(time, 'time', lambda: 86400 * 2.5)
        cache.recycle()
        self.assertEqual(
                [],
                [i for i in cache])
        self.assertEqual(0, cache._du)
        assert not exists('releases/1')
        assert not exists('releases/2')

    def test_solve(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        activity_info = '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ])
        activity_bundle = self.zips(('topdir/activity/activity.info', activity_info))
        release = conn.upload(['context'], activity_bundle, cmd='submit', initial=True)

        solution = {
            'context': {
                'blob': 'http://127.0.0.1:7777/blobs/' + release,
                'command': 'true',
                'content-type': 'application/vnd.olpc-sugar',
                'size': len(activity_bundle),
                'title': 'Activity',
                'unpack_size': len(activity_info),
                'version': '1',
                },
            }
        self.assertEqual(solution, injector._solve('context', 'stable'))
        self.assertEqual([client.api.value, 'stable', 0, solution], json.load(file('client/solutions/context')))

    def test_solve_FailInOffline(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = None
        injector.seqno = 0

        activity_info = '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ])
        activity_bundle = self.zips(('topdir/activity/activity.info', activity_info))
        release = conn.upload(['context'], activity_bundle, cmd='submit', initial=True)

        self.assertRaises(http.ServiceUnavailable, injector._solve, 'context', 'stable')

    def test_solve_ReuseCachedSolution(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ]))), cmd='submit', initial=True)

        assert 'context' in injector._solve('context', 'stable')
        conn.delete(['context', 'context'])
        assert 'context' in injector._solve('context', 'stable')
        os.unlink('client/solutions/context')
        self.assertRaises(RuntimeError, injector._solve, 'context', 'stable')

    def test_solve_InvalidateCachedSolution(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = 'http://127.0.0.1:7777'
        injector.seqno = 1

        conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ]))), cmd='submit', initial=True)
        self.assertEqual('1', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual(['http://127.0.0.1:7777', 'stable', 1], json.load(file('client/solutions/context'))[:-1])

        conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 2',
            'license = Public Domain',
            ]))), cmd='submit')
        self.assertEqual('1', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual(['http://127.0.0.1:7777', 'stable', 1], json.load(file('client/solutions/context'))[:-1])
        injector.seqno = 2
        self.assertEqual('2', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual(['http://127.0.0.1:7777', 'stable', 2], json.load(file('client/solutions/context'))[:-1])

        conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 0',
            'license = Public Domain',
            'stability = testing',
            ]))), cmd='submit')
        self.assertEqual('2', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual(['http://127.0.0.1:7777', 'stable', 2], json.load(file('client/solutions/context'))[:-1])
        self.assertEqual('0', injector._solve('context', 'testing')['context']['version'])
        self.assertEqual(['http://127.0.0.1:7777', 'testing', 2], json.load(file('client/solutions/context'))[:-1])

        self.assertEqual('2', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual(['http://127.0.0.1:7777', 'stable', 2], json.load(file('client/solutions/context'))[:-1])
        conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 3',
            'license = Public Domain',
            ]))), cmd='submit')
        self.assertEqual('2', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual(['http://127.0.0.1:7777', 'stable', 2], json.load(file('client/solutions/context'))[:-1])
        injector.api = 'http://localhost:7777'
        self.assertEqual('3', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual(['http://localhost:7777', 'stable', 2], json.load(file('client/solutions/context'))[:-1])

    def test_solve_ForceUsingStaleCachedSolutionInOffline(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ]))), cmd='submit', initial=True)
        self.assertEqual('1', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual([client.api.value, 'stable', 0], json.load(file('client/solutions/context'))[:-1])

        injector.api = None
        injector.seqno = 1
        self.assertEqual('1', injector._solve('context', 'stable')['context']['version'])
        self.assertEqual([client.api.value, 'stable', 0], json.load(file('client/solutions/context'))[:-1])

        os.unlink('client/solutions/context')
        self.assertRaises(http.ServiceUnavailable, injector._solve, 'context', 'stable')

    def test_download_SetExecPermissions(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        release = conn.upload(['context'], self.zips(
            ('topdir/activity/activity.info', '\n'.join([
                '[Activity]',
                'name = Activity',
                'bundle_id = context',
                'exec = true',
                'icon = icon',
                'activity_version = 1',
                'license = Public Domain',
                ])),
            'topdir/activity/foo',
            'topdir/bin/bar',
            'topdir/bin/probe',
            'topdir/file1',
            'topdir/test/file2',
            ), cmd='submit', initial=True)
        for __ in injector.checkin('context'):
            pass

        path = 'client/releases/%s/' % release
        assert os.access(path + 'activity/foo', os.X_OK)
        assert os.access(path + 'bin/bar', os.X_OK)
        assert os.access(path + 'bin/probe', os.X_OK)
        assert not os.access(path + 'file1', os.X_OK)
        assert not os.access(path + 'test/file2', os.X_OK)

    def test_checkin(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        activity_info = '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ])
        activity_bundle = self.zips(('topdir/activity/activity.info', activity_info))
        release = conn.upload(['context'], activity_bundle, cmd='submit', initial=True)

        self.assertEqual([
            {'event': 'checkin', 'state': 'solve'},
            {'event': 'checkin', 'state': 'download'},
            {'event': 'checkin', 'state': 'ready'},
            ],
            [i for i in injector.checkin('context')])

        self.assertEqual(['checkin'], this.volume['context']['context']['pins'])
        self.assertEqual(activity_info, file(join('client', 'releases', release, 'activity', 'activity.info')).read())
        self.assertEqual([client.api.value, 'stable', 0, {
            'context': {
                'title': 'Activity',
                'unpack_size': len(activity_info),
                'version': '1',
                'command': 'true',
                'blob': 'http://127.0.0.1:7777/blobs/' + release,
                'size': len(activity_bundle),
                'content-type': 'application/vnd.olpc-sugar',
                }}],
            json.load(file('client/solutions/context')))
        self.assertEqual({
            'context': [client.api.value, 'stable', 0],
            },
            json.load(file('client/checkins')))
        self.assertEqual(0, injector._pool._du)

        self.assertEqual([
            {'event': 'checkin', 'state': 'solve'},
            {'event': 'checkin', 'state': 'ready'},
            ],
            [i for i in injector.checkin('context')])

    def test_checkin_PreemptivePool(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        activity_info = '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ])
        activity_bundle = self.zips(('topdir/activity/activity.info', activity_info))
        release = conn.upload(['context'], activity_bundle, cmd='submit', initial=True)

        for __ in injector.checkin('context'):
            pass
        assert exists(join('client', 'releases', release))
        self.assertEqual({
            'context': [client.api.value, 'stable', 0],
            },
            json.load(file('client/checkins')))
        self.assertEqual(0, injector._pool._du)
        self.assertEqual(['checkin'], this.volume['context']['context']['pins'])

        assert injector.checkout('context')
        assert exists(join('client', 'releases', release))
        self.assertEqual({
            },
            json.load(file('client/checkins')))
        self.assertEqual(len(activity_info), injector._pool._du)
        self.assertEqual([], this.volume['context']['context']['pins'])

        for __ in injector.checkin('context'):
            pass
        assert exists(join('client', 'releases', release))
        self.assertEqual({
            'context': [client.api.value, 'stable', 0],
            },
            json.load(file('client/checkins')))
        self.assertEqual(0, injector._pool._du)
        self.assertEqual(['checkin'], this.volume['context']['context']['pins'])

        assert injector.checkout('context')
        assert not injector.checkout('context')

        assert exists(join('client', 'releases', release))
        self.assertEqual({
            },
            json.load(file('client/checkins')))
        self.assertEqual(len(activity_info), injector._pool._du)
        self.assertEqual([], this.volume['context']['context']['pins'])

    def test_checkin_Refresh(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        release1 = conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ]))), cmd='submit', initial=True)
        for __ in injector.checkin('context'):
            pass
        assert exists('client/releases/%s' % release1)

        release2 = conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 2',
            'license = Public Domain',
            ]))), cmd='submit')
        injector.seqno = 1
        for __ in injector.checkin('context'):
            pass
        assert exists('client/releases/%s' % release2)

    def test_launch(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        activity_info = '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ])
        activity_bundle = self.zips(('topdir/activity/activity.info', activity_info))
        release = conn.upload(['context'], activity_bundle, cmd='submit', initial=True)

        self.assertEqual([
            {'activity_id': 'activity_id'},
            {'event': 'launch', 'state': 'init'},
            {'event': 'launch', 'state': 'solve'},
            {'event': 'launch', 'state': 'download'},
            {'event': 'launch', 'state': 'exec'},
            {'context': 'context',
                'solution': {
                    'context': {
                        'title': 'Activity',
                        'command': 'true',
                        'content-type': 'application/vnd.olpc-sugar',
                        'blob': 'http://127.0.0.1:7777/blobs/' + hashlib.sha1(activity_bundle).hexdigest(),
                        'size': len(activity_bundle),
                        'unpack_size': len(activity_info),
                        'version': '1',
                        },
                    },
                'logs': [
                    tests.tmpdir + '/.sugar/default/logs/shell.log',
                    tests.tmpdir + '/.sugar/default/logs/sugar-network-client.log',
                    tests.tmpdir + '/.sugar/default/logs/context.log',
                    ],
                'args': ['true', '-b', 'context', '-a', 'activity_id'],
                },
            {'event': 'launch', 'state': 'exit'},
            ],
            [i for i in injector.launch('context', activity_id='activity_id')])

    def test_launch_PreemptivePool(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        activity_info = '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ])
        activity_bundle = self.zips(('topdir/activity/activity.info', activity_info))
        release = conn.upload(['context'], activity_bundle, cmd='submit', initial=True)

        launch = injector.launch('context')
        for event in launch:
            if event.get('state') == 'exec':
                break
        assert exists(join('client', 'releases', release))
        self.assertEqual(0, injector._pool._du)
        for event in launch:
            pass
        assert exists(join('client', 'releases', release))
        self.assertEqual(len(activity_info), injector._pool._du)

        launch = injector.launch('context')
        for event in launch:
            if event.get('state') == 'exec':
                break
        assert exists(join('client', 'releases', release))
        self.assertEqual(0, injector._pool._du)
        for event in launch:
            pass
        assert exists(join('client', 'releases', release))
        self.assertEqual(len(activity_info), injector._pool._du)

    def test_launch_DonntAcquireCheckins(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector('client')
        injector.api = client.api.value
        injector.seqno = 0

        conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ]))), cmd='submit', initial=True)
        for __ in injector.launch('context'):
            pass
        assert injector._pool._du > 0

        for __ in injector.checkin('context'):
            pass
        assert injector._pool._du == 0
        for __ in injector.launch('context'):
            pass
        assert injector._pool._du == 0

    def test_launch_RefreshCheckins(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector(tests.tmpdir + '/client')
        injector.api = client.api.value
        injector.seqno = 1

        release1 = conn.upload(['context'], self.zips(
            ('topdir/activity/activity.info', '\n'.join([
                '[Activity]',
                'name = Activity',
                'bundle_id = context',
                'exec = runner',
                'icon = icon',
                'activity_version = 1',
                'license = Public Domain',
                ])),
            ('topdir/activity/runner', '\n'.join([
                '#!/bin/sh',
                'echo -n 1 > output',
                ])),
            ), cmd='submit', initial=True)
        for __ in injector.checkin('context'):
            pass
        self.assertEqual(
                {'event': 'launch', 'state': 'exit'},
                [i for i in injector.launch('context')][-1])
        self.assertEqual([client.api.value, 'stable', 1], json.load(file('client/solutions/context'))[:-1])
        self.assertEqual('1', file('client/releases/%s/output' % release1).read())

        release2 = conn.upload(['context'], self.zips(
            ('topdir/activity/activity.info', '\n'.join([
                '[Activity]',
                'name = Activity',
                'bundle_id = context',
                'exec = runner',
                'icon = icon',
                'activity_version = 2',
                'license = Public Domain',
                ])),
            ('topdir/activity/runner', '\n'.join([
                '#!/bin/sh',
                'echo -n 2 > output',
                ])),
            ), cmd='submit')
        injector.seqno = 2
        self.assertEqual(
                {'event': 'launch', 'state': 'exit'},
                [i for i in injector.launch('context')][-1])
        self.assertEqual([client.api.value, 'stable', 2], json.load(file('client/solutions/context'))[:-1])
        self.assertEqual('2', file('client/releases/%s/output' % release2).read())

    def test_launch_InstallDeps(self):
        self.fork_master()
        self.touch((
            'master/files/packages/%s/%s/package1' % (lsb_release.name(), os.uname()[-1]),
            json.dumps({'version': '1', 'binary': ['pkg1', 'pkg2']}),
            ))
        self.touch((
            'master/files/packages/%s/%s/package2' % (lsb_release.name(), os.uname()[-1]),
            json.dumps({'version': '1', 'binary': ['pkg3', 'pkg4']}),
            ))
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector(tests.tmpdir + '/client')
        injector.api = client.api.value
        injector.seqno = 1

        release1 = conn.upload(['context'], self.zips(('topdir/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = Activity',
            'bundle_id = context',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            'requires = package1; package2',
            ]))), cmd='submit', initial=True)

        packages = []
        self.override(packagekit, 'install', lambda names: packages.extend(names))
        events = [i for i in injector.launch('context')]
        self.assertEqual({'event': 'launch', 'state': 'exit'}, events[-1])
        assert {'event': 'launch', 'state': 'install'} in events
        self.assertEqual(['pkg1', 'pkg2', 'pkg3', 'pkg4'], sorted(packages))

    def test_launch_Document(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector(tests.tmpdir + '/client')
        injector.api = client.api.value
        injector.seqno = 1

        book_context = conn.post(['context'], {'type': ['book'], 'title': {}, 'summary': {}, 'description': {}})
        book = conn.upload(['context'], 'book', cmd='submit', context=book_context, version='1', license='Public Domain')

        app = conn.upload(['context'], self.zips(
            ('topdir/activity/activity.info', '\n'.join([
                '[Activity]',
                'name = Activity',
                'bundle_id = app',
                'exec = runner',
                'icon = icon',
                'activity_version = 1',
                'license = Public Domain',
                ])),
            ('topdir/activity/runner', '\n'.join([
                '#!/bin/sh',
                'echo -n $@ > output',
                ])),
            ), cmd='submit', initial=True)

        self.assertEqual(
                {'event': 'launch', 'state': 'exit'},
                [i for i in injector.launch(book_context, activity_id='activity_id', app='app')][-1])

        self.assertEqual(
                '-b app -a activity_id -u %s/client/releases/%s' % (tests.tmpdir, book),
                file('client/releases/%s/output' % app).read())

    def test_launch_DocumentWithDetectingAppByMIMEType(self):
        self.fork_master()
        this.volume = LocalVolume('client')
        conn = Connection()
        injector = Injector(tests.tmpdir + '/client')
        injector.api = client.api.value
        injector.seqno = 1

        book_context = conn.post(['context'], {'type': ['book'], 'title': {}, 'summary': {}, 'description': {}})
        book = conn.upload(['context'], 'book', cmd='submit', context=book_context, version='1', license='Public Domain')

        app = conn.upload(['context'], self.zips(
            ('topdir/activity/activity.info', '\n'.join([
                '[Activity]',
                'name = Activity',
                'bundle_id = app',
                'exec = runner',
                'icon = icon',
                'activity_version = 1',
                'license = Public Domain',
                ])),
            ('topdir/activity/runner', '\n'.join([
                '#!/bin/sh',
                'echo -n $@ > output',
                ])),
            ), cmd='submit', initial=True)

        self.override(injector_, '_app_by_mimetype', lambda mime_type: 'app')
        self.assertEqual(
                {'event': 'launch', 'state': 'exit'},
                [i for i in injector.launch(book_context, activity_id='activity_id')][-1])

        self.assertEqual(
                '-b app -a activity_id -u %s/client/releases/%s' % (tests.tmpdir, book),
                file('client/releases/%s/output' % app).read())


if __name__ == '__main__':
    tests.main()
