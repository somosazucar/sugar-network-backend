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

import active_document as ad

from sugar_network.toolkit import router
from sugar_network.local import sugar


class Router(router.Router):

    def authenticate(self, request):
        return sugar.uid()

    def call(self, request, response):
        request.access_level = ad.ACCESS_LOCAL
        response.content_type = 'application/json'
        return router.Router.call(self, request, response)