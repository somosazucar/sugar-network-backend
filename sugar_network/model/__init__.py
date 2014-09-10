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
import gettext
from os.path import join

from sugar_network import db
from sugar_network.model.routes import FrontRoutes


ICON_SIZE = 55
LOGO_SIZE = 140

CONTEXT_TYPES = [
        'activity',     # Sugar application
        'book',         # books in various forms
        'group',        # a social group of related activities
        'talks',        # mix-in offline discussion forum
        'project',      # mix-in issue tracker and polling functionality
        ]

TOP_CONTEXT_TYPES = frozenset([
        'activity',
        'book',
        'group',
        ])

POST_TYPES = {
        # General purpose top-level Post
        'topic': None,
        # Object generated by Context application
        'artefact': frozenset(['activity', 'book']),
        # Bug with the Context
        'issue': frozenset(['project']),
        # A poll within the Context
        'poll': frozenset(['project']),
        # Dependent Post
        'post': None,
        }

POST_RESOLUTIONS = {
        'unconfirmed': 'issue',
        'new': 'issue',
        'needinfo': 'issue',
        'resolved': 'issue',
        'unrelated': 'issue',
        'obsolete': 'issue',
        'duplicate': 'issue',
        'open': 'poll',
        'closed': 'poll',
        }

POST_RESOLUTION_DEFAULTS = {
        'issue': 'unconfirmed',
        'poll': 'open',
        }

STABILITIES = [
        'insecure', 'buggy', 'developer', 'testing', 'stable',
        ]

RESOURCES = (
        'sugar_network.model.context',
        'sugar_network.model.post',
        'sugar_network.model.report',
        'sugar_network.model.user',
        )


class Rating(db.List):

    def __init__(self, **kwargs):
        db.List.__init__(self, db.Numeric(), default=[0, 0], **kwargs)

    def slotting(self, value):
        rating = float(value[1]) / value[0] if value[0] else 0
        return '%.3f%010d' % (rating, value[0])
