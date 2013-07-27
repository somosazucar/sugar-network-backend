# Copyright (C) 2012-2013 Aleksey Lim
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os
import logging
import httplib
from os.path import join

from sugar_network import db, client, node, toolkit, model
from sugar_network.client import journal, clones, injector
from sugar_network.node.slave import SlaveRoutes
from sugar_network.toolkit import netlink, mountpoints
from sugar_network.toolkit.router import ACL, Request, Response, Router
from sugar_network.toolkit.router import route, fallbackroute
from sugar_network.toolkit.spec import Spec
from sugar_network.toolkit import zeroconf, coroutine, http, enforce


# Top-level directory name to keep SN data on mounted devices
_SN_DIRNAME = 'sugar-network'
_LOCAL_PROPS = frozenset(['favorite', 'clone'])

# Flag file to recognize a directory as a synchronization directory
_SYNC_DIRNAME = 'sugar-network-sync'

_RECONNECT_TIMEOUT = 3
_RECONNECT_TIMEOUT_MAX = 60 * 15

_logger = logging.getLogger('client.routes')


class ClientRoutes(model.Routes, journal.Routes):

    def __init__(self, home_volume, api_url=None, no_subscription=False):
        model.Routes.__init__(self)
        if not client.no_dbus.value:
            journal.Routes.__init__(self)

        self._local = _LocalRoutes(home_volume)
        self._inline = coroutine.Event()
        self._inline_job = coroutine.Pool()
        self._remote_urls = []
        self._node = None
        self._jobs = coroutine.Pool()
        self._no_subscription = no_subscription
        self._server_mode = not api_url

        home_volume.broadcast = self.broadcast

        if self._server_mode:
            mountpoints.connect(_SN_DIRNAME,
                    self._found_mount, self._lost_mount)
        else:
            if client.discover_server.value:
                self._jobs.spawn(self._discover_node)
            else:
                self._remote_urls.append(api_url)
            self._jobs.spawn(self._wait_for_connectivity)

    def close(self):
        self._jobs.kill()
        self._got_offline()
        self._local.volume.close()

    @fallbackroute('GET', ['hub'])
    def hub(self, request, response):
        """Serve Hub via HTTP instead of file:// for IPC users.

        Since SSE doesn't support CORS for now.

        """
        if request.environ['PATH_INFO'] == '/hub':
            raise http.Redirect('/hub/')

        path = request.path[1:]
        if not path:
            path = ['index.html']
        path = join(client.hub_root.value, *path)

        mtime = os.stat(path).st_mtime
        if request.if_modified_since >= mtime:
            raise http.NotModified()

        if path.endswith('.js'):
            response.content_type = 'text/javascript'
        if path.endswith('.css'):
            response.content_type = 'text/css'
        response.last_modified = mtime

        return file(path, 'rb')

    @fallbackroute('GET', ['packages'])
    def route_packages(self, request, response):
        if self._inline.is_set():
            return self._node_call(request, response)
        else:
            # Let caller know that we are in offline and
            # no way to process specified request on the node
            raise http.ServiceUnavailable()

    @route('GET', cmd='status',
            mime_type='application/json')
    def status(self):
        result = {'route': 'proxy' if self._inline.is_set() else 'offline'}
        if self._inline.is_set():
            result['node'] = self._node.api_url
        return result

    @route('GET', cmd='inline',
            mime_type='application/json')
    def inline(self):
        if not self._server_mode and not self._inline.is_set():
            self._remote_connect()
        return self._inline.is_set()

    def whoami(self, request, response):
        if self._inline.is_set():
            return self._node_call(request, response)
        else:
            return {'roles': [], 'guid': client.sugar_uid()}

    @route('GET', [None],
            arguments={'reply': ('guid',), 'clone': int, 'favorite': bool},
            mime_type='application/json')
    def find(self, request, response, clone, favorite):
        if not self._inline.is_set() or clone or favorite:
            return self._local.call(request, response)
        else:
            return self._proxy_get(request, response)

    @route('GET', [None, None],
            arguments={'reply': list}, mime_type='application/json')
    def get(self, request, response):
        return self._proxy_get(request, response)

    @route('GET', [None, None, None], mime_type='application/json')
    def get_prop(self, request, response):
        return self._proxy_get(request, response)

    @route('GET', ['context', None], cmd='make')
    def make(self, request):
        for event in injector.make(request.guid):
            event['event'] = 'make'
            self.broadcast(event)

    @route('GET', ['context', None], cmd='launch',
            arguments={'args': list})
    def launch(self, request, args, activity_id=None,
            object_id=None, uri=None, color=None, no_spawn=None):

        def do_launch():
            for event in injector.launch(request.guid, args,
                    activity_id=activity_id, object_id=object_id, uri=uri,
                    color=color):
                event['event'] = 'launch'
                self.broadcast(event)

        if no_spawn:
            do_launch()
        else:
            self._jobs.spawn(do_launch)

    @route('PUT', ['context', None], cmd='clone',
            arguments={'force': False, 'nodeps': False, 'requires': list})
    def clone_context(self, request):
        enforce(self._inline.is_set(), 'Not available in offline')

        context_type = self._node_call(method='GET',
                path=['context', request.guid, 'type'])

        if 'activity' in context_type:
            self._clone_activity(request)
        elif 'content' in context_type:

            def get_props():
                impls = self._node_call(method='GET',
                        path=['implementation'], context=request.guid,
                        stability='stable', order_by='-version', limit=1,
                        reply=['guid'])['result']
                enforce(impls, http.NotFound, 'No implementations')
                impl_id = impls[0]['guid']
                props = self._node_call(method='GET',
                        path=['context', request.guid],
                        reply=['title', 'description'])
                props['preview'] = self._node_call(method='GET',
                        path=['context', request.guid, 'preview'])
                data_response = Response()
                props['data'] = self._node_call(response=data_response,
                        method='GET',
                        path=['implementation', impl_id, 'data'])
                props['mime_type'] = data_response.content_type or \
                        'application/octet'
                props['activity_id'] = impl_id
                return props

            self._clone_jobject(request, get_props)
        else:
            raise RuntimeError('No way to clone')

    @route('PUT', ['artifact', None], cmd='clone', arguments={'force': False})
    def clone_artifact(self, request):
        enforce(self._inline.is_set(), 'Not available in offline')

        def get_props():
            props = self._node_call(method='GET',
                    path=['artifact', request.guid],
                    reply=['title', 'description', 'context'])
            props['preview'] = self._node_call(method='GET',
                    path=['artifact', request.guid, 'preview'])
            props['data'] = self._node_call(method='GET',
                    path=['artifact', request.guid, 'data'])
            props['activity'] = props.pop('context')
            return props

        self._clone_jobject(request, get_props)

    @route('PUT', ['context', None], cmd='favorite')
    def favorite(self, request):
        if request.content or \
                self._local.volume['context'].exists(request.guid):
            self._checkin_context(request.guid, {'favorite': request.content})

    @route('GET', ['context', None], cmd='feed',
            mime_type='application/json')
    def feed(self, request, response):
        try:
            context = self._local.volume['context'].get(request.guid)
        except http.NotFound:
            context = None
        if context is None or context['clone'] != 2:
            if self._inline.is_set():
                return self._node_call(request, response)
            else:
                # Let caller know that we are in offline and
                # no way to process specified request on the node
                raise http.ServiceUnavailable()

        versions = []
        for path in clones.walk(context.guid):
            try:
                spec = Spec(root=path)
            except Exception:
                toolkit.exception(_logger, 'Failed to read %r spec file', path)
                continue
            versions.append({
                'guid': spec.root,
                'version': spec['version'],
                'arch': '*-*',
                'stability': 'stable',
                'commands': {
                    'activity': {
                        'exec': spec['Activity', 'exec'],
                        },
                    },
                'requires': spec.requires,
                })

        return {'name': context.get('title',
                    accept_language=request.accept_language),
                'implementations': versions,
                }

    @fallbackroute()
    def _node_call(self, request=None, response=None, method=None, path=None,
            **kwargs):
        if request is None:
            request = Request(method=method, path=path)
        request.update(kwargs)
        if self._inline.is_set():
            if client.layers.value and request.resource in \
                    ('context', 'implementation') and \
                    'layer' not in request:
                request['layer'] = client.layers.value
            try:
                reply = self._node.call(request, response)
                if hasattr(reply, 'read'):
                    return _ResponseStream(reply, self._restart_online)
                else:
                    return reply
            except (http.ConnectionError, httplib.IncompleteRead):
                self._restart_online()
                return self._local.call(request, response)
        else:
            return self._local.call(request, response)

    def _got_online(self):
        enforce(not self._inline.is_set())
        _logger.debug('Got online on %r', self._node)
        self._inline.set()
        self.broadcast({'event': 'inline', 'state': 'online'})

    def _got_offline(self):
        if self._inline.is_set():
            _logger.debug('Got offline on %r', self._node)
            self._node.close()
            self._inline.clear()
        self.broadcast({'event': 'inline', 'state': 'offline'})

    def _fall_offline(self):
        _logger.debug('Fall to offline on %r', self._node)
        self._inline_job.kill()

    def _restart_online(self):
        self._fall_offline()
        _logger.debug('Try to become online in %s seconds', _RECONNECT_TIMEOUT)
        self._remote_connect(_RECONNECT_TIMEOUT)

    def _discover_node(self):
        for host in zeroconf.browse_workstations():
            url = 'http://%s:%s' % (host, node.port.default)
            if url not in self._remote_urls:
                self._remote_urls.append(url)
            self._remote_connect()

    def _wait_for_connectivity(self):
        for i in netlink.wait_for_route():
            self._fall_offline()
            if i:
                self._remote_connect()

    def _remote_connect(self, timeout=0):

        def pull_events():
            for event in self._node.subscribe():
                if event.get('resource') == 'implementation':
                    mtime = event.get('mtime')
                    if mtime:
                        injector.invalidate_solutions(mtime)
                self.broadcast(event)

        def handshake(url):
            _logger.debug('Connecting to %r node', url)
            self._node = client.Connection(url)
            info = self._node.get(cmd='info')
            impl_info = info['documents'].get('implementation')
            if impl_info:
                injector.invalidate_solutions(impl_info['mtime'])
            if self._inline.is_set():
                _logger.info('Reconnected to %r node', url)
            else:
                self._got_online()

        def connect():
            timeout = _RECONNECT_TIMEOUT
            while True:
                self.broadcast({'event': 'inline', 'state': 'connecting'})
                for url in self._remote_urls:
                    while True:
                        try:
                            handshake(url)
                            if self._no_subscription:
                                return
                            pull_events()
                        except http.HTTPError, error:
                            if error.response.status_code in (502, 504):
                                _logger.debug('Retry %r on gateway error', url)
                                continue
                        except Exception:
                            toolkit.exception(_logger,
                                    'Connection to %r failed', url)
                        break
                self._got_offline()
                if not timeout:
                    break
                _logger.debug('Try to reconect in %s seconds', timeout)
                coroutine.sleep(timeout)
                timeout *= _RECONNECT_TIMEOUT
                timeout = min(timeout, _RECONNECT_TIMEOUT_MAX)

        if not self._inline_job:
            self._inline_job.spawn_later(timeout, connect)

    def _found_mount(self, root):
        if self._inline.is_set():
            _logger.debug('Found %r node mount but %r is already active',
                    root, self._node.volume.root)
            return

        _logger.debug('Found %r node mount', root)

        db_path = join(root, _SN_DIRNAME, 'db')
        node.data_root.value = db_path
        node.stats_root.value = join(root, _SN_DIRNAME, 'stats')
        node.files_root.value = join(root, _SN_DIRNAME, 'files')

        volume = db.Volume(db_path, model.RESOURCES)
        self._node = _NodeRoutes(join(db_path, 'node'), volume,
                self.broadcast)
        self._jobs.spawn(volume.populate)

        logging.info('Start %r node on %s port', volume.root, node.port.value)
        server = coroutine.WSGIServer(('0.0.0.0', node.port.value), self._node)
        self._inline_job.spawn(server.serve_forever)
        self._got_online()

    def _lost_mount(self, root):
        if not self._inline.is_set() or \
                not self._node.volume.root.startswith(root):
            return
        _logger.debug('Lost %r node mount', root)
        self._inline_job.kill()
        self._got_offline()

    def _checkin_context(self, guid, props):
        contexts = self._local.volume['context']

        if contexts.exists(guid):
            contexts.update(guid, props)
        else:
            copy = self._node_call(method='GET', path=['context', guid],
                    reply=[
                        'type', 'title', 'summary', 'description',
                        'homepage', 'mime_types', 'dependencies',
                        ])
            copy.update(props)
            copy['guid'] = guid
            contexts.create(copy)
            for prop in ('icon', 'artifact_icon', 'preview'):
                blob = self._node_call(method='GET',
                        path=['context', guid, prop])
                if blob is not None:
                    contexts.update(guid, {prop: {'blob': blob}})

    def _proxy_get(self, request, response):
        resource = request.resource
        if resource not in ('context', 'artifact'):
            return self._node_call(request, response)

        if not self._inline.is_set():
            return self._local.call(request, response)

        request_guid = request.guid if len(request.path) > 1 else None
        if request_guid and self._local.volume[resource].exists(request_guid):
            return self._local.call(request, response)

        if request.prop is not None:
            mixin = None
        else:
            reply = request.setdefault('reply', ['guid'])
            mixin = set(reply) & _LOCAL_PROPS
            if mixin:
                # Otherwise there is no way to mixin _LOCAL_PROPS
                if not request_guid and 'guid' not in reply:
                    reply.append('guid')
                if resource == 'context' and 'type' not in reply:
                    reply.append('type')

        result = self._node_call(request, response)
        if not mixin:
            return result

        if request_guid:
            items = [result]
        else:
            items = result['result']

        def mixin_jobject(props, guid):
            if 'clone' in mixin:
                props['clone'] = 2 if journal.exists(guid) else 0
            if 'favorite' in mixin:
                props['favorite'] = bool(int(journal.get(guid, 'keep') or 0))

        if resource == 'context':
            contexts = self._local.volume['context']
            for props in items:
                guid = request_guid or props['guid']
                if 'activity' in props['type']:
                    if contexts.exists(guid):
                        patch = contexts.get(guid).properties(mixin)
                    else:
                        patch = dict([(i, contexts.metadata[i].default)
                                for i in mixin])
                    props.update(patch)
                elif 'content' in props['type']:
                    mixin_jobject(props, guid)
        elif resource == 'artifact':
            for props in items:
                mixin_jobject(props, request_guid or props['guid'])

        return result

    def _clone_activity(self, request):
        if not request.content:
            clones.wipeout(request.guid)
            return
        for __ in clones.walk(request.guid):
            if not request.get('force'):
                return
            break
        self._checkin_context(request.guid, {'clone': 1})
        if request.get('nodeps'):
            pipe = injector.clone_impl(request.guid,
                    stability=request.get('stability'),
                    requires=request.get('requires'))
        else:
            pipe = injector.clone(request.guid)
        for event in pipe:
            event['event'] = 'clone'
            self.broadcast(event)
        for __ in clones.walk(request.guid):
            break
        else:
            # Cloning was failed
            self._checkin_context(request.guid, {'clone': 0})

    def _clone_jobject(self, request, get_props):
        if request.content:
            if request['force'] or not journal.exists(request.guid):
                self.journal_update(request.guid, **get_props())
                self.broadcast({
                    'event': 'show_journal',
                    'uid': request.guid,
                    })
        else:
            if journal.exists(request.guid):
                self.journal_delete(request.guid)


class CachedClientRoutes(ClientRoutes):

    def __init__(self, home_volume, api_url=None, no_subscription=False):
        ClientRoutes.__init__(self, home_volume, api_url, no_subscription)
        self._push_seq = toolkit.PersistentSequence(
                join(home_volume.root, 'push.sequence'), [1, None])
        self._push_job = coroutine.Pool()

    def _got_online(self):
        ClientRoutes._got_online(self)
        self._push_job.spawn(self._push)

    def _got_offline(self):
        self._push_job.kill()
        ClientRoutes._got_offline(self)

    def _push(self):
        pushed_seq = toolkit.Sequence()
        skiped_seq = toolkit.Sequence()

        def push(request, seq):
            try:
                self._node.call(request)
            except Exception:
                toolkit.exception(_logger,
                        'Cannot push %r, will postpone', request)
                skiped_seq.include(seq)
            else:
                pushed_seq.include(seq)

        for document, directory in self._local.volume.items():
            if directory.mtime <= self._push_seq.mtime:
                continue

            _logger.debug('Check %r local cache to push', document)

            for guid, patch in directory.diff(self._push_seq, layer='local'):
                diff = {}
                diff_seq = toolkit.Sequence()
                post_requests = []
                for prop, meta, seqno in patch:
                    if 'blob' in meta:
                        request = Request(method='PUT',
                                path=[document, guid, prop])
                        request.content_type = meta['mime_type']
                        request.content_length = os.stat(meta['blob']).st_size
                        request.content_stream = \
                                toolkit.iter_file(meta['blob'])
                        post_requests.append((request, seqno))
                    elif 'url' in meta:
                        request = Request(method='PUT',
                                path=[document, guid, prop])
                        request.content_type = 'application/json'
                        request.content = meta
                        post_requests.append((request, seqno))
                    else:
                        diff[prop] = meta['value']
                        diff_seq.include(seqno, seqno)
                if not diff:
                    continue
                if 'guid' in diff:
                    request = Request(method='POST', path=[document])
                    access = ACL.CREATE | ACL.WRITE
                else:
                    request = Request(method='PUT', path=[document, guid])
                    access = ACL.WRITE
                for name in diff.keys():
                    if not (directory.metadata[name].acl & access):
                        del diff[name]
                request.content_type = 'application/json'
                request.content = diff
                push(request, diff_seq)
                for request, seqno in post_requests:
                    push(request, [[seqno, seqno]])

        if not pushed_seq:
            self.broadcast({'event': 'push'})
            return

        _logger.info('Pushed %r local cache', pushed_seq)

        self._push_seq.exclude(pushed_seq)
        if not skiped_seq:
            self._push_seq.stretch()
            # No any decent reasons to keep fail reports after uploding.
            # TODO The entire offlile synchronization should be improved,
            # for now, it is possible to have a race here
            self._local.volume['report'].wipe()
        self._push_seq.commit()
        self.broadcast({'event': 'push'})


class _LocalRoutes(db.Routes, Router):

    def __init__(self, volume):
        db.Routes.__init__(self, volume)
        Router.__init__(self, self)

    def on_create(self, request, props, event):
        props['layer'] = tuple(props['layer']) + ('local',)
        db.Routes.on_create(self, request, props, event)


class _NodeRoutes(SlaveRoutes, Router):

    def __init__(self, key_path, volume, localcast):
        SlaveRoutes.__init__(self, key_path, volume)
        Router.__init__(self, self)

        self.api_url = 'http://127.0.0.1:%s' % node.port.value
        self._localcast = localcast
        self._mounts = toolkit.Pool()
        self._jobs = coroutine.Pool()

        users = volume['user']
        if not users.exists(client.sugar_uid()):
            profile = client.sugar_profile()
            profile['guid'] = client.sugar_uid()
            users.create(profile)

        mountpoints.connect(_SYNC_DIRNAME,
                self.__found_mountcb, self.__lost_mount_cb)

    def preroute(self, op, request):
        request.principal = client.sugar_uid()

    def whoami(self, request, response):
        return {'roles': [], 'guid': client.sugar_uid()}

    def broadcast(self, event=None, request=None):
        SlaveRoutes.broadcast(self, event, request)
        self._localcast(event)

    def close(self):
        self.volume.close()

    def __repr__(self):
        return '<LocalNode path=%s api_url=%s>' % \
                (self.volume.root, self.api_url)

    def _sync_mounts(self):
        self._localcast({'event': 'sync_start'})

        for mountpoint in self._mounts:
            self._localcast({'event': 'sync_next', 'path': mountpoint})
            try:
                self._offline_session = self._offline_sync(
                        join(mountpoint, _SYNC_DIRNAME),
                        **(self._offline_session or {}))
            except Exception, error:
                toolkit.exception(_logger,
                        'Failed to complete synchronization')
                self._localcast({'event': 'sync_abort', 'error': str(error)})
                self._offline_session = None
                raise

        if self._offline_session is None:
            _logger.debug('Synchronization completed')
            self._localcast({'event': 'sync_complete'})
        else:
            _logger.debug('Postpone synchronization with %r session',
                    self._offline_session)
            self._localcast({'event': 'sync_paused'})

    def __found_mountcb(self, path):
        self._mounts.add(path)
        if self._jobs:
            _logger.debug('Found %r sync mount, pool it', path)
        else:
            _logger.debug('Found %r sync mount, start synchronization', path)
            self._jobs.spawn(self._sync_mounts)

    def __lost_mount_cb(self, path):
        if self._mounts.remove(path) == toolkit.Pool.ACTIVE:
            _logger.warning('%r was unmounted, break synchronization', path)
            self._jobs.kill()


class _ResponseStream(object):

    def __init__(self, stream, on_fail_cb):
        self._stream = stream
        self._on_fail_cb = on_fail_cb

    def __hasattr__(self, key):
        return hasattr(self._stream, key)

    def __getattr__(self, key):
        return getattr(self._stream, key)

    def read(self, size=None):
        try:
            return self._stream.read(size)
        except (http.ConnectionError, httplib.IncompleteRead):
            self._on_fail_cb()
            raise
