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

import logging

from active_document import env
from active_document.metadata import StoredProperty
from active_document.metadata import active_property
from active_toolkit import enforce


_logger = logging.getLogger('active_document.document')


class Document(dict):

    #: `Metadata` object that describes the document
    metadata = None

    def __init__(self, guid, record, cached_props=None):
        dict.__init__(self, cached_props or {})
        self.guid = guid
        self._record = record

    @active_property(slot=1000, prefix='IC', typecast=int,
            permissions=env.ACCESS_READ, default=0)
    def ctime(self, value):
        return value

    @active_property(slot=1001, prefix='IM', typecast=int,
            permissions=env.ACCESS_READ, default=0)
    def mtime(self, value):
        return value

    @active_property(slot=1002, prefix='IS', typecast=int,
            permissions=0, default=0)
    def seqno(self, value):
        return value

    def get(self, prop, accept_language=None):
        """Get document's property value.

        :param prop:
            property name to get value
        :returns:
            `prop` value

        """
        prop = self.metadata[prop]

        value = dict.get(self, prop.name)
        if value is None:
            enforce(isinstance(prop, StoredProperty),
                    'No way to get %r property from %s[%s]',
                    prop.name, self.metadata.name, self.guid)
            meta = self._record.get(prop.name)
            value = prop.default if meta is None else meta['value']
            self[prop.name] = value

        if accept_language and prop.localized:
            value = self._localize(value, accept_language)

        return value

    def meta(self, prop):
        return self._record.get(prop)

    def __getitem__(self, prop):
        return self.get(prop)

    def _localize(self, value, accept_language):
        if not value:
            return ''
        if not isinstance(value, dict):
            return value

        for lang in accept_language + [env.DEFAULT_LANG]:
            result = value.get(lang)
            if result is not None:
                return result
            lang = lang.split('-')
            if len(lang) == 1:
                continue
            result = value.get(lang[0])
            if result is not None:
                return result

        # TODO
        return value[sorted(value.keys())[0]]
