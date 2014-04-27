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

import xapian

from sugar_network import db
from sugar_network.model.routes import FrontRoutes


ICON_SIZE = 55
LOGO_SIZE = 140

CONTEXT_TYPES = [
        'activity', 'group', 'package', 'book',
        ]

POST_TYPES = [
        'review',        # Review the Context
        'object',        # Object generated by Context application
        'question',      # Q&A request
        'answer',        # Q&A response
        'issue',         # Propblem with the Context
        'announce',      # General announcement
        'notification',  # Auto-generated Post for updates within the Context
        'feedback',      # Review parent Post
        'post',          # General purpose dependent Post
        ]

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
        return xapian.sortable_serialise(rating)
