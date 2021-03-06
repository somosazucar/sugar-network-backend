#!/usr/bin/env python
# sugar-lint: disable

import os
import time
import shutil
import signal
from cStringIO import StringIO
from contextlib import contextmanager
from os.path import exists, join, dirname, abspath

import rrdtool

from __init__ import tests, src_root

from sugar_network.client import Connection, keyfile
from sugar_network.toolkit.rrd import Rrd
from sugar_network.toolkit import coroutine, http


# /tmp might be on tmpfs wich returns 0 bytes for free mem all time
local_tmproot = join(abspath(dirname(__file__)), '.tmp')


class MasterPersonalTest(tests.Test):

    def setUp(self):
        tests.Test.setUp(self, tmp_root=local_tmproot)

        self.touch(('master/db/master', '127.0.0.1:8100'))

        self.master_pid = self.popen([join(src_root, 'sugar-network-node'), '-F', 'start',
            '--port=8100', '--data-root=master/db', '--cachedir=master/tmp',
            '-DDD', '--rundir=master/run', '--files-root=master/files',
            '--stats-root=master/stats', '--stats-user', '--stats-user-step=1',
            '--stats-user-rras=RRA:AVERAGE:0.5:1:100',
            '--index-flush-threshold=1', '--pull-timeout=1',
            '--obs-url=',
            ])
        self.client_pid = self.popen([join(src_root, 'sugar-network-client'), '-F', 'start',
            '--api=http://127.0.0.1:8100', '--cachedir=client/tmp',
            '-DDD', '--rundir=client/run', '--server-mode', '--layers=pilot',
            '--local-root=client',
            '--port=8101', '--index-flush-threshold=1',
            '--mounts-root=client/mnt', '--ipc-port=8102',
            '--stats-user', '--stats-user-step=1',
            '--stats-user-rras=RRA:AVERAGE:0.5:1:100',
            '--keyfile=%s' % keyfile.value,
            ])
        os.makedirs('client/mnt/disk/sugar-network')

        coroutine.sleep(2)
        ipc = Connection('http://127.0.0.1:8102')
        if ipc.get(cmd='whoami')['route'] == 'offline':
            self.wait_for_events(ipc, event='inline', state='online').wait()
        Connection('http://127.0.0.1:8100').get(cmd='whoami')
        Connection('http://127.0.0.1:8101').get(cmd='whoami')

    def tearDown(self):
        self.waitpid(self.master_pid, signal.SIGINT)
        self.waitpid(self.client_pid, signal.SIGINT)
        tests.Test.tearDown(self)

    def test_SyncMounts(self):
        auth = http.SugarAuth(keyfile.value)
        master = Connection('http://127.0.0.1:8100', auth=auth)
        client = Connection('http://127.0.0.1:8102', auth=auth)
        uid = auth.login

        # Create shared files on master
        self.touch(('master/files/1/1', '1'))
        self.touch(('master/files/2/2', '2'))

        # Create initial data on master
        guid_1 = master.post(['context'], {
            'type': 'activity',
            'title': 'title_1',
            'summary': 'summary',
            'description': 'description',
            'artefact_icon': 'artefact_icon',
            'logo': 'logo1',
            'layer': 'pilot',
            })
        guid_2 = master.post(['context'], {
            'type': 'activity',
            'title': 'title_2',
            'summary': 'summary',
            'description': 'description',
            'artefact_icon': 'artefact_icon',
            'logo': 'logo2',
            'layer': 'pilot',
            })

        # Create initial data on client
        guid_3 = client.post(['context'], {
            'type': 'activity',
            'title': 'title_3',
            'summary': 'summary',
            'description': 'description',
            'artefact_icon': 'artefact_icon',
            'logo': 'logo3',
            'layer': 'pilot',
            })
        guid_4 = client.post(['context'], {
            'type': 'activity',
            'title': 'title_4',
            'summary': 'summary',
            'description': 'description',
            'artefact_icon': 'artefact_icon',
            'logo': 'logo4',
            'layer': 'pilot',
            })
        stats_timestamp = int(time.time())
        client.post(['user', uid], {
            'name': 'db',
            'values': [(stats_timestamp, {'f': 1}), (stats_timestamp + 1, {'f': 2})],
            }, cmd='stats-upload')

        # Clone initial dump from master
        pid = self.popen('V=1 %s mnt/sugar-network-sync http://127.0.0.1:8100' % join(src_root, 'sugar-network-sync'), shell=True)
        self.waitpid(pid, 0)
        # Import cloned data on client
        trigger = self.wait_for_events(client, event='sync_complete')
        os.rename('mnt', 'client/mnt/1')
        trigger.wait()
        # Upload client initial data to master
        pid = self.popen('V=1 client/mnt/1/sugar-network-sync/sugar-network-sync', shell=True)
        self.waitpid(pid, 0)

        # Update data on master
        guid_5 = master.post(['context'], {
            'type': 'activity',
            'title': 'title_5',
            'summary': 'summary',
            'description': 'description',
            'artefact_icon': 'artefact_icon',
            'logo': 'logo5',
            'layer': 'pilot',
            })
        master.put(['context', guid_1, 'title'], 'title_1_')
        master.put(['context', guid_3, 'logo'], 'logo3_')

        # Update data on client
        guid_6 = client.post(['context'], {
            'type': 'activity',
            'title': 'title_6',
            'summary': 'summary',
            'description': 'description',
            'artefact_icon': 'artefact_icon',
            'logo': 'logo6',
            'layer': 'pilot',
            })
        client.put(['context', guid_3, 'title'], 'title_3_')
        client.put(['context', guid_1, 'logo'], 'logo1_')

        # Export client changes
        trigger = self.wait_for_events(client, event='sync_complete')
        os.rename('client/mnt/1', 'client/mnt/2')
        trigger.wait()
        # Sync them with master
        pid = self.popen('V=1 client/mnt/2/sugar-network-sync/sugar-network-sync', shell=True)
        self.waitpid(pid, 0)
        # Process master's reply
        trigger = self.wait_for_events(client, event='sync_complete')
        os.rename('client/mnt/2', 'client/mnt/3')
        trigger.wait()

        self.assertEqual(
                {'total': 6, 'result': [
                    {'guid': guid_1, 'title': 'title_1_'},
                    {'guid': guid_2, 'title': 'title_2'},
                    {'guid': guid_3, 'title': 'title_3_'},
                    {'guid': guid_4, 'title': 'title_4'},
                    {'guid': guid_5, 'title': 'title_5'},
                    {'guid': guid_6, 'title': 'title_6'},
                    ]},
                master.get(['context'], reply=['guid', 'title'], layer='pilot'))
        self.assertEqual(
                {'total': 6, 'result': [
                    {'guid': guid_1, 'title': 'title_1_'},
                    {'guid': guid_2, 'title': 'title_2'},
                    {'guid': guid_3, 'title': 'title_3_'},
                    {'guid': guid_4, 'title': 'title_4'},
                    {'guid': guid_5, 'title': 'title_5'},
                    {'guid': guid_6, 'title': 'title_6'},
                    ]},
                client.get(['context'], reply=['guid', 'title'], layer='pilot'))

        self.assertEqual('logo1_', master.request('GET', ['context', guid_1, 'logo']).content)
        self.assertEqual('logo2', master.request('GET', ['context', guid_2,  'logo']).content)
        self.assertEqual('logo3_', master.request('GET', ['context', guid_3, 'logo']).content)
        self.assertEqual('logo4', master.request('GET', ['context', guid_4,  'logo']).content)
        self.assertEqual('logo5', master.request('GET', ['context', guid_5,  'logo']).content)
        self.assertEqual('logo6', master.request('GET', ['context', guid_6,  'logo']).content)
        self.assertEqual('logo1_', client.request('GET', ['context', guid_1, 'logo']).content)
        self.assertEqual('logo2', client.request('GET', ['context', guid_2,  'logo']).content)
        self.assertEqual('logo3_', client.request('GET', ['context', guid_3, 'logo']).content)
        self.assertEqual('logo4', client.request('GET', ['context', guid_4,  'logo']).content)
        self.assertEqual('logo5', client.request('GET', ['context', guid_5,  'logo']).content)
        self.assertEqual('logo6', client.request('GET', ['context', guid_6,  'logo']).content)

        self.assertEqual('1', file('master/files/1/1').read())
        self.assertEqual('2', file('master/files/2/2').read())
        self.assertEqual('1', file('client/mnt/disk/sugar-network/files/1/1').read())
        self.assertEqual('2', file('client/mnt/disk/sugar-network/files/2/2').read())

        rrd = Rrd('master/stats/user/%s/%s' % (uid[:2], uid), 1)
        self.assertEqual([
            [('db', stats_timestamp, {'f': 1.0}), ('db', stats_timestamp + 1, {'f': 2.0})],
            ],
            [[(db.name,) + i for i in db.get(db.first, db.last)] for db in rrd])
        rrd = Rrd('client/mnt/disk/sugar-network/stats/user/%s/%s' % (uid[:2], uid), 1)
        self.assertEqual([
            [('db', stats_timestamp, {'f': 1.0}), ('db', stats_timestamp + 1, {'f': 2.0})],
            ],
            [[(db.name,) + i for i in db.get(db.first, db.last)] for db in rrd])


if __name__ == '__main__':
    tests.main()
