# Copyright (C) 2012-2014 Aleksey Lim
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
import time
import logging
import hashlib
from ConfigParser import ConfigParser
from os.path import join, exists

from sugar_network import db, node
from sugar_network.model import FrontRoutes, load_bundle
from sugar_network.node import model
# pylint: disable-msg=W0611
from sugar_network.toolkit.router import route, preroute, postroute, ACL, File
from sugar_network.toolkit.router import Unauthorized, Request, fallbackroute
from sugar_network.toolkit.spec import parse_requires, parse_version
from sugar_network.toolkit.bundle import Bundle
from sugar_network.toolkit.coroutine import this
from sugar_network.toolkit import pylru, http, coroutine, exception, enforce


_MAX_STAT_RECORDS = 100
_AUTH_POOL_SIZE = 1024

_logger = logging.getLogger('node.routes')


class NodeRoutes(db.Routes, FrontRoutes):

    def __init__(self, guid, **kwargs):
        db.Routes.__init__(self, **kwargs)
        FrontRoutes.__init__(self)
        self._guid = guid
        self._auth_pool = pylru.lrucache(_AUTH_POOL_SIZE)
        self._auth_config = None
        self._auth_config_mtime = 0

    @property
    def guid(self):
        return self._guid

    @route('GET', cmd='whoami', mime_type='application/json')
    def whoami(self, request, response):
        roles = []
        if self.authorize(request.principal, 'root'):
            roles.append('root')
        return {'roles': roles, 'guid': request.principal, 'route': 'direct'}

    @route('GET', cmd='status', mime_type='application/json')
    def status(self):
        return {'guid': self.guid,
                'seqno': {
                    'db': self.volume.seqno.value,
                    'releases': self.volume.releases_seqno.value,
                    },
                }

    @route('POST', ['user'], mime_type='application/json')
    def register(self, request):
        # To avoid authentication while registering new user
        self.create(request)

    @fallbackroute('GET', ['packages'])
    def route_packages(self, request, response):
        path = this.request.path
        if path and path[-1] == 'updates':
            result = []
            last_modified = 0
            for blob in this.volume.blobs.diff(
                    [[this.request.if_modified_since + 1, None]],
                    join(*path[:-1]), recursive=False):
                if '.' in blob.name:
                    continue
                result.append(blob.name)
                last_modified = max(last_modified, blob.mtime)
            response.content_type = 'application/json'
            if last_modified:
                response.last_modified = last_modified
            return result

        blob = this.volume.blobs.get(join(*path))
        if isinstance(blob, File):
            return blob
        else:
            response.content_type = 'application/json'
            return [i.name for i in blob if '.' not in i.name]

    @route('POST', ['context'], cmd='submit',
            arguments={'initial': False},
            mime_type='application/json', acl=ACL.AUTH)
    def submit_release(self, initial):
        blob = this.volume.blobs.post(
                this.request.content_stream, this.request.content_type)
        try:
            context, release = load_bundle(blob, initial=initial)
        except Exception:
            this.volume.blobs.delete(blob.digest)
            raise
        this.call(method='POST', path=['context', context, 'releases'],
                content_type='application/json', content=release)
        return blob.digest

    @route('PUT', [None, None], cmd='attach', acl=ACL.AUTH | ACL.SUPERUSER)
    def attach(self, request):
        # TODO Reading layer here is a race
        directory = self.volume[request.resource]
        doc = directory.get(request.guid)
        layer = list(set(doc['layer']) | set(request.content))
        directory.update(request.guid, {'layer': layer})

    @route('PUT', [None, None], cmd='detach', acl=ACL.AUTH | ACL.SUPERUSER)
    def detach(self, request):
        # TODO Reading layer here is a race
        directory = self.volume[request.resource]
        doc = directory.get(request.guid)
        layer = list(set(doc['layer']) - set(request.content))
        directory.update(request.guid, {'layer': layer})

    @route('GET', ['context', None], cmd='solve',
            arguments={'requires': list, 'stability': list},
            mime_type='application/json')
    def solve(self, request):
        solution = model.solve(self.volume, request.guid, **request)
        enforce(solution is not None, 'Failed to solve')
        return solution

    @route('GET', ['context', None], cmd='clone',
            arguments={'requires': list})
    def get_clone(self, request, response):
        solution = self.solve(request)
        return this.volume.blobs.get(solution[request.guid]['blob'])

    @preroute
    def preroute(self, op, request, response):
        if op.acl & ACL.AUTH and request.principal is None:
            if not request.authorization:
                enforce(self.authorize(None, 'user'),
                        Unauthorized, 'No credentials')
            else:
                if request.authorization not in self._auth_pool:
                    self.authenticate(request.authorization)
                    self._auth_pool[request.authorization] = True
                enforce(not request.authorization.nonce or
                        request.authorization.nonce >= time.time(),
                        Unauthorized, 'Credentials expired')
                request.principal = request.authorization.login

        if op.acl & ACL.AUTHOR and request.guid:
            self._enforce_authority(request)

        if op.acl & ACL.SUPERUSER:
            enforce(self.authorize(request.principal, 'root'), http.Forbidden,
                    'Operation is permitted only for superusers')

    def on_create(self, request, props):
        if request.resource == 'user':
            with file(this.volume.blobs.get(props['pubkey']).path) as f:
                props['guid'] = str(hashlib.sha1(f.read()).hexdigest())
        db.Routes.on_create(self, request, props)

    def on_aggprop_update(self, request, prop, value):
        if prop.acl & ACL.AUTHOR:
            self._enforce_authority(request)
        elif value is not None:
            self._enforce_authority(request, value.get('author'))

    def authenticate(self, auth):
        enforce(auth.scheme == 'sugar', http.BadRequest,
                'Unknown authentication scheme')
        if not self.volume['user'].exists(auth.login):
            raise Unauthorized('Principal does not exist', auth.nonce)

        from M2Crypto import RSA

        pubkey = self.volume['user'][auth.login]['pubkey']
        key = RSA.load_pub_key(this.volume.blobs.get(pubkey).path)
        data = hashlib.sha1('%s:%s' % (auth.login, auth.nonce)).digest()
        enforce(key.verify(data, auth.signature.decode('hex')),
                http.Forbidden, 'Bad credentials')

    def authorize(self, user, role):
        if role == 'user' and user:
            return True

        config_path = join(node.data_root.value, 'authorization.conf')
        if exists(config_path):
            mtime = os.stat(config_path).st_mtime
            if mtime > self._auth_config_mtime:
                self._auth_config_mtime = mtime
                self._auth_config = ConfigParser()
                self._auth_config.read(config_path)
        if self._auth_config is None:
            return False

        if not user:
            user = 'anonymous'
        if not self._auth_config.has_section(user):
            user = 'DEFAULT'
        if self._auth_config.has_option(user, role):
            return self._auth_config.get(user, role).strip().lower() in \
                    ('true', 'on', '1', 'allow')

    def _enforce_authority(self, request, author=None):
        if request.resource == 'user':
            allowed = (request.principal == request.guid)
        else:
            if author is None:
                doc = self.volume[request.resource].get(request.guid)
                author = doc['author']
            allowed = request.principal in author
        enforce(allowed or self.authorize(request.principal, 'root'),
                http.Forbidden, 'Operation is permitted only for authors')
