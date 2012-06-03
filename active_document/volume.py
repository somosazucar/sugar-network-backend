# Copyright (C) 2011-2012 Aleksey Lim
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
import json
import urllib2
import logging
from cStringIO import StringIO
from functools import partial
from os.path import exists, join, abspath, isdir
from gettext import gettext as _

from active_document import env
from active_document.directory import Directory
from active_document.index import IndexWriter
from active_document.commands import document_command, directory_command
from active_document.commands import CommandsProcessor, property_command
from active_document.commands import volume_command, Request
from active_document.metadata import BlobProperty
from active_toolkit import sockets, enforce


_logger = logging.getLogger('active_document.volume')


class _Volume(dict):

    def __init__(self, root, document_classes, index_class):
        self._subscriptions = set()

        self._root = abspath(root)
        if not exists(root):
            os.makedirs(root)

        _logger.info(_('Opening documents in %r'), self._root)


        for cls in document_classes:
            if [i for i in document_classes \
                    if i is not cls and issubclass(i, cls)]:
                _logger.warning(_('Skip not final %r document class'), cls)
                continue
            name = cls.__name__.lower()
            directory = Directory(join(self._root, name), cls, index_class,
                    partial(self._notification_cb, document=name))
            self[name] = directory

    def close(self):
        """Close operations with the server."""
        _logger.info(_('Closing documents in %r'), self._root)

        while self:
            __, cls = self.popitem()
            cls.close()

    def connect(self, callback):
        self._subscriptions.add(callback)

    def _notification_cb(self, event, document):
        for callback in self._subscriptions:
            if event['event'] == 'update' and \
                    'props' in event and \
                    'deleted' in event['props'].get('layer', []):
                event['event'] = 'delete'
                del event['props']
            event['document'] = document
            callback(event)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __getitem__(self, name):
        enforce(name in self, _('Unknow %r document'), name)
        return self.get(name)


class SingleVolume(_Volume):

    def __init__(self, root, document_classes):
        enforce(env.index_write_queue.value > 0,
                _('The active_document.index_write_queue.value should be > 0'))
        _Volume.__init__(self, root, document_classes, IndexWriter)


class VolumeCommands(CommandsProcessor):

    def __init__(self, volume):
        CommandsProcessor.__init__(self, volume)
        self.volume = volume

    @volume_command(method='GET', cmd='stat')
    def stat(self):
        documents = {}
        for name, directory in self.volume.items():
            documents[name] = {
                    'seqno': directory.seqno,
                    }
        return {'documents': documents}

    @directory_command(method='POST',
            permissions=env.ACCESS_AUTH)
    def create(self, document, request):
        directory = self.volume[document]
        props = request.content
        for i in props.keys():
            directory.metadata[i].assert_access(env.ACCESS_CREATE)
        props['user'] = [request.principal] if request.principal else []
        return directory.create(props)

    @directory_command(method='GET')
    def find(self, document, offset=None, limit=None, query=None, reply=None,
            order_by=None, **kwargs):
        directory = self.volume[document]
        offset = _to_int('offset', offset)
        limit = _to_int('limit', limit)
        reply = _to_list(reply) or []
        reply.append('guid')

        for i in reply:
            directory.metadata[i].assert_access(env.ACCESS_READ)

        # TODO until implementing layers support
        kwargs['layer'] = 'public'

        documents, total = directory.find(offset=offset, limit=limit,
                query=query, reply=reply, order_by=order_by, **kwargs)
        result = [i.properties(reply) for i in documents]

        return {'total': total.value, 'result': result}

    @document_command(method='GET', cmd='exists')
    def exists(self, document, guid):
        directory = self.volume[document]
        return directory.exists(guid)

    @document_command(method='PUT',
            permissions=env.ACCESS_AUTH | env.ACCESS_AUTHOR)
    def update(self, document, guid, request):
        directory = self.volume[document]
        props = request.content
        for i in props.keys():
            directory.metadata[i].assert_access(env.ACCESS_WRITE)
        directory.update(guid, props)

    @property_command(method='PUT',
            permissions=env.ACCESS_AUTH | env.ACCESS_AUTHOR)
    def update_prop(self, document, guid, prop, request, url=None):
        directory = self.volume[document]

        directory.metadata[prop].assert_access(env.ACCESS_WRITE)

        if not isinstance(directory.metadata[prop], BlobProperty):
            directory.update(guid, {prop: request.content})
            return

        if url is not None:
            _logger.info(_('Download BLOB for %r from %r'), prop, url)
            stream = urllib2.urlopen(url)
            try:
                directory.set_blob(guid, prop, stream)
            finally:
                stream.close()
        elif request.content is not None:
            # TODO Avoid double JSON processins
            content_stream = StringIO()
            json.dump(request.content, content_stream)
            content_stream.seek(0)
            directory.set_blob(guid, prop, content_stream, None)
        else:
            directory.set_blob(guid, prop, request.content_stream,
                    request.content_length)

    @document_command(method='DELETE',
            permissions=env.ACCESS_AUTH | env.ACCESS_AUTHOR)
    def delete(self, document, guid):
        directory = self.volume[document]
        directory.delete(guid)

    @document_command(method='PUT', cmd='hide',
            permissions=env.ACCESS_AUTH | env.ACCESS_AUTHOR)
    def hide(self, document, guid):
        directory = self.volume[document]
        directory.update(guid, {'layer': ['deleted']})

    @document_command(method='GET')
    def get(self, document, guid, reply=None):
        directory = self.volume[document]
        doc = directory.get(guid)

        reply = _to_list(reply)
        if reply:
            for i in reply:
                directory.metadata[i].assert_access(env.ACCESS_READ)

        enforce('deleted' not in doc['layer'], env.NotFound,
                _('Document is not found'))

        return doc.properties(reply)

    @property_command(method='GET')
    def get_prop(self, document, guid, prop, response, seqno=None):
        directory = self.volume[document]
        doc = directory.get(guid)

        directory.metadata[prop].assert_access(env.ACCESS_READ)

        if not isinstance(directory.metadata[prop], BlobProperty):
            return doc[prop]

        seqno = _to_int('seqno', seqno)
        if seqno is not None and seqno >= doc.get_seqno(prop):
            response.content_length = 0
            response.content_type = directory.metadata[prop].mime_type
            return None

        stat = directory.stat_blob(guid, prop)
        response.content_length = stat.get('size') or 0
        response.content_type = directory.metadata[prop].mime_type

        path = stat.get('path')
        enforce(path, env.NotFound, _('Property does not exist'))

        if isdir(path):
            dir_info, dir_reader = sockets.encode_directory(path)
            response.content_length = dir_info.content_length
            response.content_type = dir_info.content_type
            return dir_reader

        def file_reader(path):
            with file(path, 'rb') as f:
                while True:
                    chunk = f.read(sockets.BUFFER_SIZE)
                    if not chunk:
                        break
                    yield chunk

        return file_reader(path)

    @property_command(method='GET', cmd='stat-blob')
    def stat_blob(self, document, guid, prop, request):
        directory = self.volume[document]

        directory.metadata[prop].assert_access(env.ACCESS_READ)

        stat = directory.stat_blob(guid, prop)
        if not stat:
            return None

        if request.access_level < Request.ACCESS_REMOTE:
            return stat
        else:
            return {'size': stat['size'], 'sha1sum': stat['sha1sum']}


def _to_int(name, value):
    if isinstance(value, basestring):
        enforce(value.isdigit(),
                _('Argument %r should be an integer value'), name)
        value = int(value)
    return value


def _to_list(value):
    if isinstance(value, basestring):
        value = value.split(',')
    return value
