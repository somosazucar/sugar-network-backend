# Copyright (C) 2011-2012, Aleksey Lim
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

import time
import uuid
import logging
from datetime import datetime
from gettext import gettext as _

from active_document import env, util
from active_document.document_class import DocumentClass
from active_document.storage import Storage
from active_document.metadata import Metadata
from active_document.metadata import ActiveProperty, StoredProperty
from active_document.metadata import GuidProperty, BlobProperty, SeqnoProperty
from active_document.metadata import AggregatorProperty, IndexedProperty
from active_document.index import IndexWriter
from active_document.index_proxy import IndexProxy
from active_document.sync import Seqno
from active_document.util import enforce


_logger = logging.getLogger('ad.document')


class Document(DocumentClass):

    #: `Metadata` object that describes the document
    metadata = None

    _initated = False
    _storage = None
    _index = None
    _seqno = None

    def __init__(self, guid=None, indexed_props=None, raw=None, **kwargs):
        """
        :param guid:
            GUID of existing document; if omitted, newly created object
            will be associated with new document; new document will be saved
            only after calling `post`
        :param indexed_props:
            property values got from index to populate the cache
        :param raw:
            list of property names to avoid any checks for
            users' visible properties; only for server local use
        :param kwargs:
            optional key arguments with new property values; specifing these
            arguments will mean the same as setting properties after `Document`
            object creation

        """
        self.init()
        self._raw = raw or []
        self._is_new = False
        self._cache = {}
        self._record = None

        if guid:
            self._guid = guid
            if not indexed_props:
                indexed_props = self._index.get_cache(guid)
            for prop_name, value in (indexed_props or {}).items():
                self._cache[prop_name] = (value, None)
            self.authorize_document(env.ACCESS_READ, self)
        else:
            self._is_new = True

            cache = {}
            self.on_create(kwargs, cache)
            for name, value in cache.items():
                self._cache[name] = (None, value)
            self._guid = cache['guid']

            for name, prop in self.metadata.items():
                if isinstance(prop, StoredProperty):
                    if name in kwargs or name in self._cache:
                        continue
                    enforce(prop.default is not None,
                            _('Property "%s" should be passed for ' \
                                    'new "%s" document'),
                            name, self.metadata.name)
                if prop.default is not None:
                    self._cache[name] = (None, prop.default)

        for prop_name, value in kwargs.items():
            self[prop_name] = value

    @property
    def guid(self):
        """Document GUID."""
        return self._guid

    def __getitem__(self, prop_name):
        """Get document's property value.

        :param prop_name:
            property name to get value
        :returns:
            `prop_name` value

        """
        prop = self.metadata[prop_name]
        self.authorize_property(env.ACCESS_READ, prop)

        orig, new = self._cache.get(prop_name, (None, None))
        if new is not None:
            return new
        if orig is not None:
            return orig

        if isinstance(prop, StoredProperty):
            if self._record is None:
                self._record = self._storage.get(self.guid)
            orig = self._record.get(prop_name)
        else:
            if isinstance(prop, IndexedProperty):
                self._get_not_storable_but_indexd_props()
                orig, __ = self._cache.get(prop_name, (None, None))
            if orig is None and isinstance(prop, AggregatorProperty):
                value = self._storage.is_aggregated(
                        self.guid, prop_name, prop.value)
                orig = env.value(value)
            enforce(orig is not None, _('Property "%s" in "%s" cannot be get'),
                    prop_name, self.metadata.name)

        self._cache[prop_name] = (orig, new)
        return orig

    def get_list(self, prop_name):
        """If property value contains several values, list them all.

        :param prop_name:
            property name to return value for
        :returns:
            list of value's portions; for not multiple properties,
            return singular value as the only part of the list

        """
        value = self[prop_name]
        prop = self.metadata[prop_name]
        if isinstance(prop, IndexedProperty):
            return prop.list_value(value)
        else:
            return [value]

    def __setitem__(self, prop_name, value):
        """set document's property value.

        :param prop_name:
            property name to set
        :param value:
            property value to set

        """
        if prop_name == 'guid':
            enforce(self._is_new, _('GUID can be set only for new documents'))

        prop = self.metadata[prop_name]
        if self._is_new:
            self.authorize_property(env.ACCESS_CREATE, prop)
        else:
            self.authorize_property(env.ACCESS_WRITE, prop)

        if isinstance(prop, StoredProperty):
            pass
        elif isinstance(prop, AggregatorProperty):
            enforce(value.isdigit(),
                    _('Property "%s" in "%s" should be either "0" or "1"'),
                    prop_name, self.metadata.name)
            value = env.value(bool(int(value)))
        else:
            raise RuntimeError(_('Property "%s" in "%s" cannot be set') % \
                    (prop_name, self.metadata.name))

        orig, __ = self._cache.get(prop_name, (None, None))
        self._cache[prop_name] = (orig, value)

        if prop_name == 'guid':
            self._guid = value

    def post(self):
        """Store changed properties."""
        changes = {}
        for prop_name, (__, new) in self._cache.items():
            if new is not None:
                changes[prop_name] = new
        if not changes:
            return

        if self._is_new:
            self.authorize_document(env.ACCESS_CREATE, self)
        else:
            self.authorize_document(env.ACCESS_WRITE, self)
            self.on_modify(changes)
        self.on_post(changes)

        for prop_name, value in changes.items():
            prop = self.metadata[prop_name]
            if not isinstance(prop, IndexedProperty) or \
                    prop.typecast is None:
                continue
            try:
                value_parts = []
                for part in prop.list_value(value):
                    if prop.typecast is int:
                        part = str(int(part))
                    elif prop.typecast is bool:
                        part = str(int(bool(int(part))))
                    else:
                        enforce(part in prop.typecast,
                                _('Value "%s" is not from "%s" list'),
                                part, ', '.join(prop.typecast))
                    value_parts.append(part)
                changes[prop_name] = (prop.separator or ' ').join(value_parts)
            except Exception:
                error = _('Value for "%s" property for "%s" is invalid') % \
                        (prop_name, self.metadata.name)
                util.exception(error)
                raise RuntimeError(error)

        if self._is_new:
            _logger.debug('Create new document "%s"', self.guid)

        self._index.store(self.guid, changes, self._is_new,
                self._pre_store, self._post_store)
        self._is_new = False

    def get_blob(self, prop_name):
        """Read BLOB property content.

        This function works in parallel to getting non-BLOB properties values.

        :param prop_name:
            property name
        :returns:
            generator that returns data by portions

        """
        prop = self.metadata[prop_name]
        self.authorize_property(env.ACCESS_READ, prop)
        enforce(isinstance(prop, BlobProperty),
                _('Property "%s" in "%s" is not a BLOB'),
                prop_name, self.metadata.name)
        return self._storage.get_blob(self.guid, prop_name)

    def set_blob(self, prop_name, stream, size=None):
        """Receive BLOB property from a stream.

        This function works in parallel to setting non-BLOB properties values
        and `post()` function.

        :param prop_name:
            property name
        :param stream:
            stream to receive property value from
        :param size:
            read only specified number of bytes; otherwise, read until the EOF

        """
        prop = self.metadata[prop_name]
        self.authorize_property(env.ACCESS_WRITE, prop)
        enforce(isinstance(prop, BlobProperty),
                _('Property "%s" in "%s" is not a BLOB'),
                prop_name, self.metadata.name)
        self._storage.set_blob(self.guid, prop_name, stream, size)

    def on_create(self, properties, cache):
        """Call back to call on document creation.

        Function needs to be re-implemented in child classes.

        :param properties:
            dictionary with new document properties values
        :param cache:
            properties to use as predefined values

        """
        cache['guid'] = str(uuid.uuid1())

        ts = str(int(time.mktime(datetime.utcnow().timetuple())))
        cache['ctime'] = ts
        cache['mtime'] = ts

        self._set_seqno(cache)

    def on_modify(self, properties):
        """Call back to call on existing document modification.

        Function needs to be re-implemented in child classes.

        :param properties:
            dictionary with document properties updates

        """
        ts = str(int(time.mktime(datetime.utcnow().timetuple())))
        properties['mtime'] = ts

        self._set_seqno(properties)

    def on_post(self, properties):
        """Call back to call on exery `post()` call.

        Function needs to be re-implemented in child classes.

        :param properties:
            dictionary with document properties updates

        """
        pass

    def authorize_property(self, mode, prop):
        """Does caller have permissions to access to the specified property.

        If caller does not have permissions, function should raise
        `active_document.Forbidden` exception.

        :param mode:
            one of `active_document.ACCESS_*` constants
            to specify the access mode
        :param prop:
            property to check access for

        """
        enforce(prop.name in self._raw or \
                mode & prop.permissions, env.Forbidden,
                _('%s access is disabled for "%s" property in "%s"'),
                env.ACCESS_NAMES[mode], prop.name, self.metadata.name)

    def _get_not_storable_but_indexd_props(self):
        prop_names = []
        for name, prop in self.metadata.items():
            if not isinstance(prop, StoredProperty) and \
                    isinstance(prop, IndexedProperty):
                prop_names.append(name)
        documents, __ = self.find(0, 1,
                request={'guid': self.guid}, reply=prop_names)
        for doc in documents:
            for name in prop_names:
                __, new = self._cache.get(name, (None, None))
                self._cache[name] = (doc[name], new)

    def _set_seqno(self, properties):
        if 'seqno' not in self._raw:
            properties['seqno'] = self._seqno.next()
