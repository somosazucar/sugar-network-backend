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

import uuid
import logging
import threading
from Queue import Queue
from gettext import gettext as _

from active_document import env, storage
from active_document.metadata import Metadata, Property, GuidProperty
from active_document.index import get_index, connect_to_index
from active_document.util import enforce


_initating_lock = threading.Lock()


class Document(object):

    #: `Metadata` object that describes the document
    metadata = None

    _initated = False
    _pool = None

    def __init__(self, guid=None, **kwargs):
        """
        :param guid:
            GUID of existing document; if omitted, newly created object
            will be associated with new document; new document will be saved
            only after calling `post`
        :param kwargs:
            optional key arguments with new property values; specifing these
            arguments will mean the same as setting properties after `Document`
            object creation

        """
        self._init()
        self._is_new = False

        if guid:
            self._guid = guid
            self._record = storage.get(self.metadata.name, self.guid)
        else:
            self._is_new = True
            self._guid = str(uuid.uuid1())
            kwargs['guid'] = self.guid

            defaults = {}
            for name, prop in self.metadata.items():
                if name in kwargs or prop.blob:
                    continue
                enforce(prop.default is not None,
                        _('Property "%s" should be passed while creating ' \
                                'new %s document'),
                        name, self.metadata.name)
                defaults[name] = prop.default

            self._record = storage.get(self.metadata.name, self.guid, defaults)

            """
            for name, value in kwargs.items():
                prop = self.metadata.get(name)
                if prop is not None and prop.write_access:
                    authorized_props[name] = value
                else:
                    props[name] = value

                    enforce(prop.default is not None,
                            _('Property "%s" should be passed while ' \
                                    'creating new %s document'),
                            name, self.metadata.name)
                    kwargs[name] = prop.default

            # Initialize record with `kwargs` since further properties setting
            # might rely on existing some properties from `kwargs`
            """

        for prop, value in kwargs.items():
            self[prop] = value

    @property
    def guid(self):
        """Document GUID."""
        return self._guid

    def __getitem__(self, prop):
        """Get document's property value.

        :param prop:
            property name to get value
        :returns:
            `prop` value

        """
        enforce(prop not in self.metadata or not self.metadata[prop].blob,
                _('Property "%s" in %s is a BLOB and cannot be get'),
                prop, self.metadata.name)
        return self._record.get(prop)

    def __setitem__(self, prop, value):
        """set document's property value.

        :param prop:
            property name to set
        :param value:
            property value to set

        """
        if prop in self.metadata:
            enforce(not self.metadata[prop].blob,
                    _('Property "%s" in %s is a BLOB and cannot be set'),
                    prop, self.metadata.name)
            enforce(self.authorize(prop),
                    _('You are not permitted to change "%s" property in %s'),
                    prop, self.metadata.name)
            enforce(self._is_new or not self.metadata[prop].construct_only,
                    _('Property "%s" in %s can be set only on document ' \
                            'creation'),
                    prop, self.metadata.name)
        return self._record.set(prop, value)

    def post(self):
        """Store changed properties."""
        if not self._record.modified:
            return

        index = self._pool.get(True)
        try:
            index.store(self.guid, self._record, self._is_new)
            self._is_new = False
        finally:
            self._pool.put(index, True)

        storage.put(self.metadata.name, self.guid, self._record)

    def send(self, prop, stream):
        """Send BLOB property to a stream.

        This function works in parallel to getting non-BLOB properties values.

        :param prop:
            property name
        :param stream:
            stream to send property value to

        """
        enforce(prop not in self.metadata or self.metadata[prop].blob,
                _('Property "%s" in %s is not a BLOB'),
                prop, self.metadata.name)
        self._record.send(prop, stream)

    def receive(self, prop, stream):
        """Receive BLOB property from a stream.

        This function works in parallel to setting non-BLOB properties values
        and `post()` function.

        :param prop:
            property name
        :param stream:
            stream to receive property value from

        """
        enforce(self.metadata[prop].blob,
                _('Property "%s" in %s is not a BLOB'),
                prop, self.metadata.name)
        self._record.receive(prop, stream)

    def authorize(self, prop):
        return True

    @classmethod
    def create(cls, properties):
        """Create new document.

        :param properties:
            new document properties
        :returns:
            created `Document` object

        """
        doc = cls(**properties)
        doc.post()
        return doc

    @classmethod
    def update(cls, guid, properties):
        """Update properties for an existing document.

        :param guid:
            document GUID to store
        :param properties:
            properties to store, not necessary all document's properties

        """
        doc = cls(guid, **properties)
        doc.post()

    @classmethod
    def delete(cls, guid):
        """Delete document.

        :param guid:
            document GUID to delete

        """
        storage.delete(cls.metadata.name, guid)

        index = cls._pool.get(True)
        try:
            index.delete(guid)
        finally:
            cls._pool.put(index, True)

    @classmethod
    def find(cls, offset, limit, request=None, query='',
            reply=None, order_by=None, group_by=None):
        """Search documents.

        The result will be an array of dictionaries with found documents'
        properties.

        :param offset:
            the resulting list should start with this offset;
            0 by default
        :param limit:
            the resulting list will be at least `limit` size;
            the `--find-limit` will be used by default
        :param request:
            a dictionary with property values to restrict the search
        :param query:
            a string in Xapian serach format, empty to avoid text search
        :param reply:
            an array of property names to use only in the resulting list;
            only GUID property will be used by default
        :param order_by:
            array of properties to sort resulting list; property names might be
            prefixed with ``+`` (or without any prefixes) for ascending order,
            and ``-`` for descending order
        :param group_by:
            a property name to group resulting list by; if was specified,
            every resulting list item will contain `grouped` with
            a number of documents that are represented by the current one;
            no groupping by default
        :returns:
            a tuple of (`documents`, `total_count`); where the `total_count` is
            the total number of documents conforming the search parameters,
            i.e., not only documents that are included to the resulting list

        """
        if limit > env.find_limit.value:
            logging.warning(_('The find limit for %s is restricted to %s'),
                    cls.metadata.name, env.find_limit.value)
            limit = env.find_limit.value
        if request is None:
            request = {}
        if not reply:
            reply = ['guid']
        if order_by is None and 'ctime' in cls.metadata:
            order_by = ['+ctime']

        for prop in cls.metadata.values():
            enforce(not prop.large and not prop.blob,
                    _('Property "%s" in %s is not suitable for find requests'),
                    prop.name, cls.metadata.name)

        index = cls._pool.get(True)
        try:
            return index.find(offset, limit, request, query, reply,
                    order_by, group_by)
        finally:
            cls._pool.put(index, True)

    @classmethod
    def connect(cls, cb, *args):
        """Connect to changes in index.

        Callback function will be triggered on GObject signals when something
        was changed in the index and clients need to retry requests.

        :param cb:
            callback to call on index changes
        :param args:
            optional arguments to pass to `cb`

        """
        connect_to_index(cls.metadata, cb, *args)

    @classmethod
    def _init(cls):
        # This `if` should be atomic
        # http://effbot.org/zone/thread-synchronization.htm#atomic-operations
        if cls._initated:
            return

        _initating_lock.acquire()
        try:
            # Since the first `if` is not synchronized
            if cls._initated:
                return

            cls.metadata = Metadata()
            cls.metadata.name = cls.__name__.lower()
            cls.metadata.crawler = lambda: storage.walk(cls.metadata.name)
            cls.metadata.to_document = lambda guid, props: cls(guid, **props)
            cls.metadata['guid'] = GuidProperty()

            for attr in [getattr(cls, i) for i in dir(cls)]:
                if hasattr(attr, '_is_active_property'):
                    if attr.prop.writable:
                        setattr(cls, attr.name, property(attr, attr.writer))
                    else:
                        setattr(cls, attr.name, property(attr))
                    cls.metadata[attr.prop.name] = attr.prop

            if env.index_pool.value > 0:
                pool_size = env.index_pool.value or 1
                cls._pool = Queue([], pool_size)
                for i in range(pool_size):
                    cls._pool.put(get_index(cls.metadata))
            else:
                cls._pool = _FakeQueue(get_index(cls.metadata))

            cls._initated = True
        finally:
            _initating_lock.release()


def active_property(*args, **kwargs):

    def getter(func, self):
        value = self[func.__name__]
        return func(self, value)

    def setter(func, self, value):
        value = func(self, value)
        self[func.__name__] = value

    def decorate_setter(func, attr):
        attr.prop.writable = True
        attr.writer = lambda self, value: setter(func, self, value)
        return attr

    def decorate_getter(func):
        attr = lambda self, * args: getter(func, self)
        attr.setter = lambda func: decorate_setter(func, attr)
        attr._is_active_property = True
        attr.name = func.__name__
        attr.prop = Property(attr.name, *args, **kwargs)
        return attr

    return decorate_getter


class _FakeQueue(object):

    def __init__(self, index):
        self._index = index

    def get(self, *args):
        return self._index

    def put(self, index, *args):
        pass
