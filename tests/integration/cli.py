#!/usr/bin/env python
# sugar-lint: disable

import os
import json
import signal
import shutil
import zipfile
from os.path import exists

import requests

from __init__ import tests

import active_document as ad
from sugar_network import IPCClient
from active_toolkit import coroutine, util


class SyncTest(tests.Test):

    def setUp(self):
        tests.Test.setUp(self)

        os.makedirs('mnt')
        util.cptree('../../data/node', 'node')
        self.service_pid = None

        self.node_pid = self.popen(['sugar-network-server', '-F', 'start',
            '--port=8100', '--data-root=node', '--tmpdir=tmp', '-DDD',
            ])
        coroutine.sleep(3)

    def tearDown(self):
        self.waitpid(self.node_pid, signal.SIGINT)
        if not self.service_pid:
            self.waitpid(self.service_pid, signal.SIGINT)
        tests.Test.tearDown(self)

    def test_Clone(self):
        context = self.call(['POST', '/context'], stdin={
            'type': 'activity',
            'title': 'title1',
            'summary': 'summary',
            'description': 'description',
            })
        impl = self.call(['POST', '/implementation'], stdin={
            'context': context,
            'license': 'GPLv3+',
            'version': '1',
            'date': 0,
            'stability': 'stable',
            'notes': '',
            'spec': {
                '*-*': {
                    'commands': {
                        'activity': {
                            'exec': 'true',
                            },
                        },
                    'extract': 'topdir',
                    },
                },
            })
        bundle = zipfile.ZipFile('bundle', 'w')
        bundle.writestr('/topdir/probe', 'ok')
        bundle.close()
        self.call(['PUT', '/implementation/%s/data' % impl, '--post-file=bundle'])

        self.call(['PUT', 'cmd=clone'], stdin=context)
        assert exists('service/Activities/topdir/probe')
        self.assertEqual('ok', file('service/Activities/topdir/probe').read())

    def test_Keep(self):
        context = self.call(['POST', '/context'], stdin={
            'type': 'activity',
            'title': 'title1',
            'summary': 'summary',
            'description': 'description',
            })

        path = 'service/local/context/%s/%s/keep' % (context[:2], context)
        assert not exists(path)

        self.call(['PUT', 'cmd=keep'], stdin=context)

        assert exists(path)
        self.assertEqual(True, json.load(file(path))['value'])

    def test_UsecaseOOB(self):
        privkey_path = '.sugar/default/owner.key'
        os.unlink(privkey_path)

        self.call(['PUT', 'cmd=clone', '--anonymous'], stdin='context')
        self.call(['PUT', 'cmd=keep', '--anonymous'], stdin='context')

        assert not exists(privkey_path)
        assert exists('Activities/Chat.activity/activity/activity.info')
        self.assertEqual(True, json.load(file('service/local/context/co/context/keep'))['value'])

    def call(self, cmd, stdin=None):
        cmd = ['sugar-network', '--local-root=service', '--ipc-port=5101', '--api-url=http://localhost:8100', '-DDD'] + cmd

        if '--anonymous' not in cmd and not self.service_pid:
            self.service_pid = self.popen(['sugar-network-service', 'debug',
                '--activity-dirs=service/Activities', '--local-root=service',
                '--mounts-root=mnt', '--tmpdir=tmp', '--ipc-port=5101',
                '--api-url=http://localhost:8100', '-DDD',
                ])
            while True:
                try:
                    with IPCClient(mountpoint='/') as client:
                        if client.get(cmd='mounted'):
                            break
                except requests.ConnectionError:
                    coroutine.sleep(1)

        result = util.assert_call(cmd, stdin=json.dumps(stdin))
        if result:
            return json.loads(result)


if __name__ == '__main__':
    tests.main()
