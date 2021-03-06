# Copyright (C) 2012 Aleksey Lim
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
import sys
import logging
from shutil import copyfileobj
from tempfile import NamedTemporaryFile

from sugar_network import client
from sugar_network.toolkit.router import route, Request, File
from sugar_network.toolkit.coroutine import this
from sugar_network.toolkit import enforce


_logger = logging.getLogger('client.journal')
_ds_root = client.profile_path('datastore')


def exists(guid):
    return os.path.exists(_ds_path(guid))


def get(guid, prop):
    path = _prop_path(guid, prop)
    if not os.path.exists(path):
        return None
    with file(path, 'rb') as f:
        return f.read()


class Routes(object):

    _ds = None

    def __init__(self):
        import dbus
        try:
            self._ds = dbus.Interface(
                dbus.SessionBus().get_object(
                    'org.laptop.sugar.DataStore',
                    '/org/laptop/sugar/DataStore'),
                'org.laptop.sugar.DataStore')
        except dbus.DBusException:
            _logger.info(
                    'Cannot connect to sugar-datastore, '
                    'Journal integration is disabled')

    @route('GET', ['journal'], mime_type='application/json', arguments={
            'offset': int,
            'limit': int,
            'reply': ['uid', 'title', 'description', 'preview'],
            'order_by': list,
            })
    def journal_find(self):
        enforce(self._ds is not None, 'Journal is inaccessible')

        import dbus

        request = this.request
        reply = request.pop('reply')
        has_preview = False
        if 'preview' in reply:
            reply.remove('preview')
            has_preview = True
        has_data = False
        if 'data' in reply:
            reply.remove('data')
            has_data = True
        for key in ('timestamp', 'filesize', 'creation_time'):
            value = request.get(key)
            if not value or '..' not in value:
                continue
            start, end = value.split('..', 1)
            value = {'start': start or '0', 'end': end or str(sys.maxint)}
            request[key] = dbus.Dictionary(value)
        if 'filesize' not in request:
            request['filesize'] = {'start': 1}
        if 'uid' not in reply:
            reply.append('uid')

        result, total = self._ds.find(request, reply, byte_arrays=True)

        for item in result:
            # Do not break SN like API
            guid = item['guid'] = item.pop('uid')
            if has_preview:
                item['preview'] = _url(guid, 'preview')
            if has_data:
                item['data'] = _url(guid, 'data')

        return {'result': result, 'total': int(total)}

    @route('GET', ['journal', None], mime_type='application/json')
    def journal_get(self):
        guid = this.request.guid
        return {'guid': guid,
                'title': get(guid, 'title'),
                'description': get(guid, 'description'),
                'preview': _url(guid, 'preview'),
                }

    @route('GET', ['journal', None, 'preview'])
    def journal_get_preview(self):
        return File(_prop_path(this.request.guid, 'preview'), meta={
            'content-type': 'image/png',
            })

    @route('GET', ['journal', None, 'data'])
    def journal_get_data(self):
        return File(_ds_path(this.request.guid, 'data'), meta={
            'content-type': get(this.request.guid, 'mime_type') or
                    'application/octet',
            })

    @route('GET', ['journal', None, None], mime_type='application/json')
    def journal_get_prop(self):
        return get(this.request.guid, this.request.prop)

    @route('PUT', ['journal', None], cmd='share')
    def journal_share(self):
        enforce(self._ds is not None, 'Journal is inaccessible')

        guid = this.request.guid
        preview_path = _prop_path(guid, 'preview')
        enforce(os.access(preview_path, os.R_OK), 'No preview')
        data_path = _ds_path(guid, 'data')
        enforce(os.access(data_path, os.R_OK), 'No data')

        subrequest = Request(method='POST', document='artifact')
        subrequest.content = this.request.content
        subrequest.content_type = 'application/json'
        # pylint: disable-msg=E1101
        subguid = self.fallback(subrequest)

        subrequest = Request(method='PUT', document='artifact',
                guid=subguid, prop='preview')
        subrequest.content_type = 'image/png'
        with file(preview_path, 'rb') as subrequest.content:
            self.fallback(subrequest)

        subrequest = Request(method='PUT', document='artifact',
                guid=subguid, prop='data')
        subrequest.content_type = get(guid, 'mime_type') or 'application/octet'
        with file(data_path, 'rb') as subrequest.content:
            self.fallback(subrequest)

    def journal_update(self, guid, data=None, **kwargs):
        enforce(self._ds is not None, 'Journal is inaccessible')

        preview = kwargs.get('preview')
        if preview:
            if hasattr(preview, 'read'):
                preview = preview.read()
                if hasattr(preview, 'close'):
                    preview.close()
            elif isinstance(preview, dict):
                with file(preview['blob'], 'rb') as f:
                    preview = f.read()
            import dbus
            kwargs['preview'] = dbus.ByteArray(preview)

        if hasattr(data, 'read'):
            with NamedTemporaryFile(delete=False) as f:
                copyfileobj(data, f)
                data = f.name
                transfer_ownership = True
        elif isinstance(data, dict):
            data = data['blob']
            transfer_ownership = False
        elif data is not None:
            with NamedTemporaryFile(delete=False) as f:
                f.write(data)
                data = f.name
                transfer_ownership = True

        self._ds.update(guid, kwargs, data or '', transfer_ownership)

    def journal_delete(self, guid):
        enforce(self._ds is not None, 'Journal is inaccessible')
        self._ds.delete(guid)


def _ds_path(guid, *args):
    return os.path.join(_ds_root, guid[:2], guid, *args)


def _prop_path(guid, prop):
    return _ds_path(guid, 'metadata', prop)


def _url(guid, prop):
    return 'http://127.0.0.1:%s/journal/%s/%s' % \
            (client.ipc_port.value, guid, prop)
