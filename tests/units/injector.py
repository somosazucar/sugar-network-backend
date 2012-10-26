#!/usr/bin/env python
# sugar-lint: disable

import os
import shutil
import zipfile
import cPickle as pickle
from cStringIO import StringIO
from os.path import exists, dirname

from __init__ import tests

from active_toolkit import coroutine, enforce
from sugar_network import zeroinstall
from sugar_network.client import journal
from sugar_network.toolkit import pipe
from sugar_network.resources.user import User
from sugar_network.resources.context import Context
from sugar_network.resources.implementation import Implementation
from sugar_network.zerosugar import lsb_release, packagekit, injector, clones
from sugar_network import IPCClient, client as local


class InjectorTest(tests.Test):

    def test_clone_Online(self):
        self.start_ipc_and_restful_server([User, Context, Implementation])
        remote = IPCClient(mountpoint='/')

        context = remote.post(['context'], {
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })

        pipe = injector.clone('/', context)
        log_path = tests.tmpdir +  '/.sugar/default/logs/%s.log' % context
        self.assertEqual([
            {'state': 'fork', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'analyze', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'failure', 'error': "Interface '%s' has no usable implementations" % context, 'mountpoint': '/', 'context': context, 'log_path': log_path},
            ],
            [i for i in pipe])

        impl = remote.post(['implementation'], {
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
                            'exec': 'echo',
                            },
                        },
                    'stability': 'stable',
                    'size': 0,
                    'extract': 'topdir',
                    },
                },
            })

        pipe = injector.clone('/', context)
        log_path = tests.tmpdir +  '/.sugar/default/logs/%s_1.log' % context
        self.assertEqual([
            {'state': 'fork', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'analyze', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'download', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'failure', 'error': 'BLOB does not exist', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            ],
            [i for i in pipe])
        assert not exists('cache/implementation/%s' % impl)

        blob_path = 'remote/implementation/%s/%s/data' % (impl[:2], impl)
        self.touch((blob_path, '{}'))
        bundle = zipfile.ZipFile(blob_path + '.blob', 'w')
        bundle.writestr('topdir/probe', 'probe')
        bundle.close()

        pipe = injector.clone('/', context)
        log_path = tests.tmpdir +  '/.sugar/default/logs/%s_2.log' % context
        self.assertEqual([
            {'state': 'fork', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'analyze', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'download', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'ready', 'implementation': impl, 'mountpoint': '/', 'context': context, 'log_path': log_path},
            ],
            [i for i in pipe])
        assert exists('cache/implementation/%s' % impl)
        assert exists('Activities/topdir/probe')
        self.assertEqual('probe', file('Activities/topdir/probe').read())

        os.unlink(blob_path)
        os.unlink(blob_path + '.blob')
        shutil.rmtree('Activities')

        pipe = injector.clone('/', context)
        log_path = tests.tmpdir +  '/.sugar/default/logs/%s_3.log' % context
        self.assertEqual([
            {'state': 'fork', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'analyze', 'mountpoint': '/', 'context': context, 'log_path': log_path},
            {'state': 'ready', 'implementation': impl, 'mountpoint': '/', 'context': context, 'log_path': log_path},
            ],
            [i for i in pipe])
        assert exists('cache/implementation/%s' % impl)
        assert exists('Activities/topdir/probe')
        self.assertEqual('probe', file('Activities/topdir/probe').read())

    def test_launch_Online(self):
        self.start_ipc_and_restful_server([User, Context, Implementation])
        remote = IPCClient(mountpoint='/')

        context = remote.post(['context'], {
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })
        impl = remote.post(['implementation'], {
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
                    'stability': 'stable',
                    'size': 0,
                    'extract': 'TestActivitry',
                    },
                },
            })

        blob_path = 'remote/implementation/%s/%s/data' % (impl[:2], impl)
        self.touch((blob_path, '{}'))
        bundle = zipfile.ZipFile(blob_path + '.blob', 'w')
        bundle.writestr('TestActivitry/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = TestActivitry',
            'bundle_id = %s' % context,
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license=Public Domain',
            ]))
        bundle.close()

        pipe = injector.launch('/', context)

        log_path = tests.tmpdir +  '/.sugar/default/logs/%s.log' % context
        self.assertEqual([
            {'state': 'fork', 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            {'state': 'analyze', 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            {'state': 'download', 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            {'state': 'ready', 'implementation': impl, 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            {'state': 'exec', 'implementation': impl, 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            ],
            [i for i in pipe])

        impl_2 = remote.post(['implementation'], {
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
                    'stability': 'stable',
                    'size': 0,
                    'extract': 'TestActivitry',
                    },
                },
            })

        blob_path = 'remote/implementation/%s/%s/data' % (impl_2[:2], impl_2)
        self.touch((blob_path, '{}'))
        bundle = zipfile.ZipFile(blob_path + '.blob', 'w')
        bundle.writestr('TestActivitry/activity/activity.info', '\n'.join([
            '[Activity]',
            'name = TestActivitry',
            'bundle_id = %s' % context,
            'exec = true',
            'icon = icon',
            'activity_version = 2',
            'license=Public Domain',
            ]))
        bundle.close()

        shutil.rmtree('cache', ignore_errors=True)
        pipe = injector.launch('/', context)
        log_path = tests.tmpdir +  '/.sugar/default/logs/%s_1.log' % context
        self.assertEqual([
            {'state': 'fork', 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            {'state': 'analyze', 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            {'state': 'download', 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            {'state': 'ready', 'implementation': impl_2, 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            {'state': 'exec', 'implementation': impl_2, 'mountpoint': '/', 'context': context, 'log_path': log_path, 'activity_id': None, 'color': None},
            ],
            [i for i in pipe])

    def test_launch_Offline(self):
        self.touch(('Activities/activity/activity/activity.info', [
            '[Activity]',
            'name = TestActivity',
            'bundle_id = bundle_id',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            ]))

        self.start_server()
        monitor = coroutine.spawn(clones.monitor,
                self.mounts.volume['context'], ['Activities'])
        coroutine.sleep()

        context = 'bundle_id'
        impl = tests.tmpdir + '/Activities/activity'

        pipe = injector.launch('~', context, activity_id='activity_id')
        log_path = tests.tmpdir +  '/.sugar/default/logs/%s.log' % context
        self.assertEqual([
            {'state': 'fork', 'mountpoint': '~', 'context': context, 'log_path': log_path, 'color': None, 'activity_id': 'activity_id'},
            {'state': 'analyze', 'mountpoint': '~', 'context': context, 'log_path': log_path, 'color': None, 'activity_id': 'activity_id'},
            {'state': 'ready', 'implementation': impl, 'mountpoint': '~', 'context': context, 'log_path': log_path, 'color': None, 'activity_id': 'activity_id'},
            {'state': 'exec', 'implementation': impl, 'mountpoint': '~', 'context': context, 'log_path': log_path, 'color': None, 'activity_id': 'activity_id'},
            ],
            [i for i in pipe])

    def test_InstallDeps(self):
        self.touch(('Activities/activity/activity/activity.info', [
            '[Activity]',
            'name = TestActivity',
            'bundle_id = bundle_id',
            'exec = true',
            'icon = icon',
            'activity_version = 1',
            'license = Public Domain',
            'requires = dep1; dep2',
            ]))

        self.touch('remote/master')
        self.start_ipc_and_restful_server([User, Context, Implementation])
        remote = IPCClient(mountpoint='/')
        monitor = coroutine.spawn(clones.monitor,
                self.mounts.volume['context'], ['Activities'])
        coroutine.sleep()

        remote.post(['context'], {
            'type': 'package',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            'implement': 'dep1',
            'packages': {
                lsb_release.distributor_id(): {
                    'binary': ['dep1.bin'],
                    },
                },
            })

        remote.post(['context'], {
            'type': 'package',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            'implement': 'dep2',
            'packages': {
                lsb_release.distributor_id(): {
                    'binary': ['dep2.bin'],
                    },
                },
            })

        def resolve(names):
            with file('resolve', 'w') as f:
                pickle.dump(names, f)
            return dict([(i, {'name': i, 'pk_id': i, 'version': '0', 'arch': '*', 'installed': i == 'dep1.bin'}) for i in names])

        def install(packages):
            with file('install', 'w') as f:
                pickle.dump([i['name'] for i in packages], f)

        self.override(packagekit, 'resolve', resolve)
        self.override(packagekit, 'install', install)

        context = 'bundle_id'
        pipe = injector.launch('~', context)
        self.assertEqual('exec', [i for i in pipe][-1].get('state'))
        self.assertEqual(['dep1.bin', 'dep2.bin'], pickle.load(file('resolve')))
        self.assertEqual(['dep2.bin'], pickle.load(file('install')))

    def test_SolutionsCache_Set(self):
        self.override(zeroinstall, 'solve', lambda *args: 'solved')

        self.assertEqual('solved', injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', 'solved'], pickle.load(file('cache/solutions/~/co/context')))

        self.assertEqual('solved', injector._solve('/', 'context'))
        self.assertEqual(['http://localhost:8800', 'solved'], pickle.load(file('cache/solutions/#/co/context')))

        self.assertEqual('solved', injector._solve('/foo/bar', 'context'))
        self.assertEqual(['http://localhost:8800', 'solved'], pickle.load(file('cache/solutions/#foo#bar/co/context')))

    def test_SolutionsCache_InvalidateByAPIUrl(self):
        self.override(zeroinstall, 'solve', lambda *args: 'solved')
        cached_path = 'cache/solutions/~/co/context'

        self.touch((cached_path, pickle.dumps(["http://localhost:8800", [{}]])))
        self.assertEqual([{}], injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', [{}]], pickle.load(file(cached_path)))

        local.api_url.value = 'fake'
        self.assertEqual('solved', injector._solve('~', 'context'))
        self.assertEqual(['fake', 'solved'], pickle.load(file(cached_path)))

    def test_SolutionsCache_InvalidateByMtime(self):
        self.override(zeroinstall, 'solve', lambda *args: 'solved')
        cached_path = 'cache/solutions/~/co/context'

        injector.invalidate_solutions(1)
        self.touch((cached_path, pickle.dumps(["http://localhost:8800", [{}]])))
        os.utime(cached_path, (1, 1))
        self.assertEqual([{}], injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', [{}]], pickle.load(file(cached_path)))

        os.utime(cached_path, (2, 2))
        self.assertEqual([{}], injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', [{}]], pickle.load(file(cached_path)))

        injector.invalidate_solutions(3)
        self.assertEqual('solved', injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', 'solved'], pickle.load(file(cached_path)))

    def test_SolutionsCache_InvalidateByPMSMtime(self):
        self.override(zeroinstall, 'solve', lambda *args: 'solved')
        cached_path = 'cache/solutions/~/co/context'

        injector._pms_path = 'pms'
        self.touch('pms')
        os.utime('pms', (1, 1))
        self.touch((cached_path, pickle.dumps(["http://localhost:8800", [{}]])))
        os.utime(cached_path, (1, 1))
        self.assertEqual([{}], injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', [{}]], pickle.load(file(cached_path)))

        os.utime(cached_path, (2, 2))
        self.assertEqual([{}], injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', [{}]], pickle.load(file(cached_path)))

        os.utime('pms', (3, 3))
        self.assertEqual('solved', injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', 'solved'], pickle.load(file(cached_path)))

    def test_SolutionsCache_InvalidateBySpecMtime(self):
        self.override(zeroinstall, 'solve', lambda *args: 'solved')
        cached_path = 'cache/solutions/~/co/context'

        self.touch('spec')
        os.utime('spec', (1, 1))
        self.touch((cached_path, pickle.dumps(["http://localhost:8800", [{"spec": "spec"}]])))
        os.utime(cached_path, (1, 1))
        self.assertEqual([{"spec": "spec"}], injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', [{"spec": "spec"}]], pickle.load(file(cached_path)))

        os.utime(cached_path, (2, 2))
        self.assertEqual([{"spec": "spec"}], injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', [{"spec": "spec"}]], pickle.load(file(cached_path)))

        os.utime('spec', (3, 3))
        self.assertEqual('solved', injector._solve('~', 'context'))
        self.assertEqual(['http://localhost:8800', 'solved'], pickle.load(file(cached_path)))

    def test_CacheReuseOnSolveFails(self):
        self.override(zeroinstall, 'solve', lambda *args: enforce(False))
        cached_path = 'cache/solutions/~/co/context'

        self.assertRaises(RuntimeError, injector._solve, '~', 'context')

        injector.invalidate_solutions(1)
        self.touch((cached_path, pickle.dumps(["http://localhost:8800", [{}]])))
        os.utime(cached_path, (1, 1))
        self.assertEqual([{}], injector._solve('~', 'context'))
        self.assertEqual(["http://localhost:8800", [{}]], pickle.load(file(cached_path)))

        injector.invalidate_solutions(3)
        self.assertEqual([{}], injector._solve('~', 'context'))
        self.assertEqual(["http://localhost:8800", [{}]], pickle.load(file(cached_path)))

    def test_clone_SetExecPermissionsForActivities(self):
        self.start_ipc_and_restful_server([User, Context, Implementation])
        remote = IPCClient(mountpoint='/')

        context = remote.post(['context'], {
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            })
        impl = remote.post(['implementation'], {
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
                            'exec': 'echo',
                            },
                        },
                    'stability': 'stable',
                    'size': 0,
                    'extract': 'topdir',
                    },
                },
            })
        blob_path = 'remote/implementation/%s/%s/data' % (impl[:2], impl)
        self.touch((blob_path, '{}'))
        bundle = zipfile.ZipFile(blob_path + '.blob', 'w')
        bundle.writestr('topdir/activity/foo', '')
        bundle.writestr('topdir/bin/bar', '')
        bundle.writestr('topdir/bin/probe', '')
        bundle.writestr('topdir/file1', '')
        bundle.writestr('topdir/test/file2', '')
        bundle.close()

        pipe = injector.clone('/', context)
        log_path = tests.tmpdir +  '/.sugar/default/logs/%s_2.log' % context
        self.assertEqual('ready', [i for i in pipe][-1]['state'])
        assert os.access('Activities/topdir/activity/foo', os.X_OK)
        assert os.access('Activities/topdir/bin/bar', os.X_OK)
        assert os.access('Activities/topdir/bin/probe', os.X_OK)
        assert not os.access('Activities/topdir/file1', os.X_OK)
        assert not os.access('Activities/topdir/test/file2', os.X_OK)

    def test_launch_Arguments(self):
        forks = []
        self.override(pipe, 'fork', lambda callback, logname, session, args, **kwargs: forks.append(args))
        self.override(journal, 'create_activity_id', lambda: 'new_activity_id')

        injector.launch('/', 'app')
        injector.launch('/', 'app', ['foo'])
        injector.launch('/', 'app', ['foo'], activity_id='activity_id', object_id='object_id', uri='uri')

        self.assertEqual([
            ['-b', 'app', '-a', 'new_activity_id'],
            ['foo', '-b', 'app', '-a', 'new_activity_id'],
            ['foo', '-b', 'app', '-a', 'activity_id', '-o', 'object_id', '-u', 'uri'],
            ],
            forks)

    def test_ProcessCommonDependencies(self):
        self.touch('remote/master')
        self.start_ipc_and_restful_server([User, Context, Implementation])
        remote = IPCClient(mountpoint='/')

        context = remote.post(['context'], {
            'type': 'activity',
            'title': 'title',
            'summary': 'summary',
            'description': 'description',
            'dependencies': ['dep1', 'dep2'],
            })
        impl = remote.post(['implementation'], {
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
                            'exec': 'echo',
                            },
                        },
                    'requires': {
                        'dep2': {'restrictions': [['1', '2']]},
                        'dep3': {},
                    },
                },
            }})
        remote.post(['context'], {
            'implement': 'dep1',
            'type': 'package',
            'title': 'title1',
            'summary': 'summary',
            'description': 'description',
            'packages': {
                lsb_release.distributor_id(): {
                    'binary': ['dep1.bin'],
                    },
                },
            })
        remote.post(['context'], {
            'implement': 'dep2',
            'type': 'package',
            'title': 'title2',
            'summary': 'summary',
            'description': 'description',
            'packages': {
                lsb_release.distributor_id(): {
                    'binary': ['dep2.bin'],
                    },
                },
            })
        remote.post(['context'], {
            'implement': 'dep3',
            'type': 'package',
            'title': 'title3',
            'summary': 'summary',
            'description': 'description',
            'packages': {
                lsb_release.distributor_id(): {
                    'binary': ['dep3.bin'],
                    },
                },
            })

        def resolve(names):
            return dict([(i, {'name': i, 'pk_id': i, 'version': '1', 'arch': '*', 'installed': True}) for i in names])

        self.override(packagekit, 'resolve', resolve)

        self.assertEqual(
                sorted([
                    {'version': '1', 'id': 'dep1', 'context': 'dep1', 'name': 'title1'},
                    {'version': '1', 'id': 'dep2', 'context': 'dep2', 'name': 'title2'},
                    {'version': '1', 'id': 'dep3', 'context': 'dep3', 'name': 'title3'},
                    {'name': 'title', 'version': '1', 'command': ['echo'], 'context': context, 'mountpoint': '/', 'id': impl},
                    ]),
                sorted(zeroinstall.solve('/', context)))


if __name__ == '__main__':
    tests.main()
