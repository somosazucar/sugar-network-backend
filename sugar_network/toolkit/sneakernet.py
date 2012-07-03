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
import json
import time
import tarfile
import logging
import tempfile
from cStringIO import StringIO
from contextlib import contextmanager
from os.path import join, exists, relpath
from gettext import gettext as _

import active_document as ad
from active_toolkit.sockets import BUFFER_SIZE
from active_toolkit import util, enforce


_RESERVED_SIZE = 1024 * 1024
_MAX_PACKET_SIZE = 1024 * 1024 * 100
_PACKET_COMPRESS_MODE = 'gz'

_logger = logging.getLogger('node.sneakernet')


def walk(path):
    for root, __, files in os.walk(path):
        for filename in files:
            if not filename.endswith('.packet'):
                continue
            with InPacket(join(root, filename)) as packet:
                yield packet


class DiskFull(Exception):
    pass


class InPacket(object):

    def __init__(self, path=None, stream=None):
        self._file = None
        self._tarball = None
        self.header = {}

        try:
            if stream is None:
                self._file = stream = file(path, 'rb')
            elif not hasattr(stream, 'seek'):
                # tarfile/gzip/zip might require seeking
                self._file = tempfile.TemporaryFile()
                while True:
                    chunk = stream.read(BUFFER_SIZE)
                    if not chunk:
                        self._file.flush()
                        self._file.seek(0)
                        break
                    self._file.write(chunk)
                stream = self._file

            self._tarball = tarfile.open('r', fileobj=stream)
            with self._extractfile('header') as f:
                self.header = json.load(f)
            enforce(type(self.header) is dict, _('Incorrect header'))
        except Exception, error:
            self.close()
            util.exception()
            raise RuntimeError(_('Malformed packet: %s') % error)

    @property
    def path(self):
        if self._file is not None:
            return self._file.name

    @property
    def basename(self):
        if self.path is not None:
            return relpath(self.path, join(self.path, '..', '..'))

    def __repr__(self):
        return '<InPacket path=%r header=%r>' % (self.path, self.header)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __iter__(self):
        for info in self._tarball:
            if not info.isfile() or info.name == 'header' or \
                    info.name.endswith('.meta'):
                continue

            try:
                with self._extractfile(info.name + '.meta') as f:
                    meta = json.load(f)
            except KeyError:
                _logger.debug('No .meta file for %r', info.name)
                continue

            if meta.get('type') == 'messages':
                with self._extractfile(info) as f:
                    for line in f:
                        item = json.loads(line)
                        item.update(meta)
                        yield item
            elif meta.get('type') == 'blob':
                with self._extractfile(info) as f:
                    meta['blob'] = f
                    yield meta
            else:
                _logger.info(_('Ignore unknown %r record'), meta)

    def close(self):
        if self._tarball is not None:
            self._tarball.close()
            self._tarball = None
        if self._file is not None:
            self._file.close()
            self._file = None

    @contextmanager
    def _extractfile(self, arcname):
        f = self._tarball.extractfile(arcname)
        try:
            yield f
        finally:
            f.close()


class OutPacket(object):

    def __init__(self, packet_type, root=None, stream=None, limit=None,
            **kwargs):
        self._stream = None
        self._file = None
        self._tarball = None
        self.header = kwargs
        self.header['type'] = packet_type
        self._path = None
        self._size_to_flush = 0
        self._file_num = 0
        self._empty = True

        if root is not None:
            root = join(root, packet_type)
            if not exists(root):
                os.makedirs(root)
            self._path = join(root, '%s.%s.packet' % (ad.uuid(), packet_type))
            self._file = stream = file(self._path, 'w')
        else:
            limit = min(_MAX_PACKET_SIZE, limit or _MAX_PACKET_SIZE)
        self._limit = limit

        if stream is None:
            stream = StringIO()
        self._tarball = tarfile.open(
                mode='w:' + _PACKET_COMPRESS_MODE, fileobj=stream)
        self._stream = stream

    @property
    def path(self):
        return self._path

    @property
    def basename(self):
        if self._path is not None:
            return relpath(self._path, join(self._path, '..', '..'))

    @property
    def closed(self):
        return self._tarball is None

    @property
    def empty(self):
        return self._empty

    def __repr__(self):
        return '<OutPacket path=%r header=%r>' % (self.path, self.header)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        if self._tarball is not None:
            self._commit()
            self._tarball = None
        if self._file is not None:
            self._file.close()
            self._file = None

    def clear(self):
        if self._tarball is not None:
            self._tarball.close()
            self._tarball = None
        if self._file is not None:
            self._file.close()
            os.unlink(self._file.name)
            self._file = None

    @contextmanager
    def push_messages(self, items, arcname=None, **meta):
        if not hasattr(items, 'next'):
            items = iter(items)
        try:
            chunk = json.dumps(items.next())
        except StopIteration:
            return

        meta['type'] = 'messages'

        while chunk is not None:
            self._flush(0, True)
            limit = self._enforce_limit()

            with tempfile.TemporaryFile() as arcfile:
                while True:
                    limit -= len(chunk)
                    if limit <= 0:
                        break
                    arcfile.write(chunk)
                    arcfile.write('\n')

                    try:
                        chunk = json.dumps(items.next())
                    except StopIteration:
                        chunk = None
                        break

                if not arcfile.tell():
                    if chunk is not None:
                        _logger.debug('Reach size limit for %r packet', self)
                        raise DiskFull()
                    break

                arcfile.seek(0)
                self._add(arcname, arcfile, meta)

    def push_blob(self, stream, arcname=None, **meta):
        meta['type'] = 'blob'
        self._add(arcname, stream, meta)

    def pop_content(self):
        self.close()
        length = self._stream.tell()
        self._stream.seek(0)
        return self._stream, length

    def _add(self, arcname, data, meta):
        if not arcname:
            self._file_num += 1
            arcname = '%08d' % self._file_num
        self._addfile(arcname, data, False)
        self._addfile(arcname + '.meta', meta, True)
        self._empty = False

    def _commit(self):
        self._addfile('header', self.header, True)
        self._tarball.close()
        self._tarball = None

    def _addfile(self, arcname, data, force):
        info = tarfile.TarInfo(arcname)
        info.mtime = time.time()

        if hasattr(data, 'fileno'):
            info.size = os.fstat(data.fileno()).st_size
            fileobj = data
        else:
            data = json.dumps(data)
            info.size = len(data)
            fileobj = StringIO(data)

        self._flush(info.size, False)
        if not force:
            self._enforce_limit(info.size)

        self._tarball.addfile(info, fileobj=fileobj)

    def _flush(self, size, force):
        if force or self._size_to_flush >= _RESERVED_SIZE:
            self._tarball.fileobj.flush()
            self._size_to_flush = 0
        self._size_to_flush += size

    def _enforce_limit(self, size=0):
        if self._limit is None:
            stat = os.statvfs(self.path)
            free = stat.f_bfree * stat.f_frsize
        else:
            free = self._limit - self._stream.tell()
        free -= _RESERVED_SIZE
        if free - size <= 0:
            _logger.debug('Reach size limit for %r packet', self)
            raise DiskFull()
        return free
