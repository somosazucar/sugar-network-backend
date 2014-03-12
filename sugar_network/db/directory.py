# Copyright (C) 2011-2014 Aleksey Lim
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

import shutil
import logging
from os.path import exists, join

from sugar_network import toolkit
from sugar_network.db.storage import Storage
from sugar_network.db.metadata import Metadata, Guid
from sugar_network.toolkit.coroutine import this
from sugar_network.toolkit import http, exception, enforce


# To invalidate existed index on stcuture changes
_LAYOUT_VERSION = 4

_logger = logging.getLogger('db.directory')


class Directory(object):

    def __init__(self, root, resource, index_class, seqno):
        """
        :param index_class:
            what class to use to access to indexes, for regular casses
            (using `Master` and `Node`, it will be all time ProxyIndex to
            keep writer in separate process).

        """
        if resource.metadata is None:
            # Metadata cannot be recreated
            resource.metadata = Metadata(resource)
            resource.metadata['guid'] = Guid()
        self.metadata = resource.metadata

        self.resource = resource
        self._index_class = index_class
        self._root = root
        self._seqno = seqno
        self._storage = None
        self._index = None

        self._open()

    def wipe(self):
        self.close()
        _logger.debug('Wipe %r directory', self.metadata.name)
        shutil.rmtree(join(self._root, 'index', self.metadata.name),
                ignore_errors=True)
        shutil.rmtree(join(self._root, 'db', self.metadata.name),
                ignore_errors=True)
        self._open()

    def close(self):
        """Flush index write pending queue and close the index."""
        if self._index is None:
            return
        self._index.close()
        self._storage = None
        self._index = None

    def commit(self):
        """Flush pending chnages to disk."""
        self._index.commit()

    def create(self, props):
        """Create new document.

        If `guid` property is not specified, it will be auto set.

        :param props:
            new document properties
        :returns:
            GUID of newly created document

        """
        guid = props.get('guid')
        if not guid:
            guid = props['guid'] = toolkit.uuid()
        _logger.debug('Create %s[%s]: %r', self.metadata.name, guid, props)
        event = {'event': 'create', 'guid': guid}
        self._index.store(guid, props, self._prestore, self._broadcast, event)
        return guid

    def update(self, guid, props):
        """Update properties for an existing document.

        :param guid:
            document GUID to store
        :param kwargs:
            properties to store, not necessary all document's properties

        """
        _logger.debug('Update %s[%s]: %r', self.metadata.name, guid, props)
        event = {'event': 'update', 'guid': guid}
        self._index.store(guid, props, self._prestore, self._broadcast, event)

    def delete(self, guid):
        """Delete document.

        :param guid:
            document GUID to delete

        """
        _logger.debug('Delete %s[%s]', self.metadata.name, guid)
        event = {'event': 'delete', 'guid': guid}
        self._index.delete(guid, self._postdelete, guid, event)

    def exists(self, guid):
        return self._storage.get(guid).consistent

    def get(self, guid):
        cached_props = self._index.get_cached(guid)
        record = self._storage.get(guid)
        enforce(cached_props or record.exists, http.NotFound,
                'Resource %r does not exist in %r',
                guid, self.metadata.name)
        return self.resource(guid, record, cached_props)

    def __getitem__(self, guid):
        return self.get(guid)

    def find(self, **kwargs):
        mset = self._index.find(**kwargs)

        def iterate():
            for hit in mset:
                guid = hit.document.get_value(0)
                record = self._storage.get(guid)
                yield self.resource(guid, record)

        return iterate(), mset.get_matches_estimated()

    def populate(self):
        """Populate the index.

        This function needs be called right after `init()` to pickup possible
        pending changes made during the previous session when index was not
        propertly closed.

        :returns:
            function is a generator that will be iterated after picking up
            every object to let the caller execute urgent tasks

        """
        found = False
        migrate = (self._index.mtime == 0)

        for guid in self._storage.walk(self._index.mtime):
            if not found:
                _logger.info('Start populating %r index', self.metadata.name)
                found = True

            if migrate:
                self._storage.migrate(guid)

            record = self._storage.get(guid)
            try:
                props = {}
                for name in self.metadata:
                    meta = record.get(name)
                    if meta is not None:
                        props[name] = meta['value']
                self._index.store(guid, props)
                yield
            except Exception:
                exception('Cannot populate %r in %r, invalidate it',
                        guid, self.metadata.name)
                record.invalidate()

        if found:
            self._save_layout()
            self.commit()

    def patch(self, guid, patch, seqno=None):
        """Apply changes for documents."""
        doc = self.resource(guid, self._storage.get(guid))

        for prop, meta in patch.items():
            orig_meta = doc.meta(prop)
            if orig_meta and orig_meta['mtime'] >= meta['mtime']:
                continue
            if doc.post_seqno is None:
                if seqno is None:
                    seqno = self._seqno.next()
                doc.post_seqno = seqno
            doc.post(prop, **meta)

        if doc.post_seqno is not None and doc.exists:
            # No need in after-merge event, further commit event
            # is enough to avoid increasing events flow
            self._index.store(guid, doc.origs, self._preindex)

        return seqno

    def _open(self):
        index_path = join(self._root, 'index', self.metadata.name)
        if self._is_layout_stale():
            if exists(index_path):
                _logger.warning('%r layout is stale, remove index',
                        self.metadata.name)
                shutil.rmtree(index_path, ignore_errors=True)
            self._save_layout()
        self._index = self._index_class(index_path, self.metadata,
                self._postcommit)
        self._storage = Storage(join(self._root, 'db', self.metadata.name))
        _logger.debug('Open %r resource', self.resource)

    def _broadcast(self, event):
        event['resource'] = self.metadata.name
        this.broadcast(event)

    def _preindex(self, guid, changes):
        doc = self.resource(guid, self._storage.get(guid), changes)
        for prop in self.metadata:
            enforce(doc[prop] is not None, 'Empty %r property', prop)
        return doc

    def _prestore(self, guid, changes, event):
        doc = self.resource(guid, self._storage.get(guid), posts=changes)
        doc.post_seqno = self._seqno.next()
        for prop in changes.keys():
            doc.post(prop, changes[prop])
        for prop in self.metadata.keys():
            enforce(doc[prop] is not None, 'Empty %r property', prop)
        return doc

    def _postdelete(self, guid, event):
        self._storage.delete(guid)
        self._broadcast(event)

    def _postcommit(self):
        self._seqno.commit()
        self._broadcast({'event': 'commit', 'mtime': self._index.mtime})

    def _save_layout(self):
        path = join(self._root, 'index', self.metadata.name, 'layout')
        with toolkit.new_file(path) as f:
            f.write(str(_LAYOUT_VERSION))

    def _is_layout_stale(self):
        path = join(self._root, 'index', self.metadata.name, 'layout')
        if not exists(path):
            return True
        with file(path) as f:
            version = f.read()
        return not version.isdigit() or int(version) != _LAYOUT_VERSION
