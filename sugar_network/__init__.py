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

from active_toolkit import optparse
from sugar_network.toolkit import sugar, application
from sugar_network.client.bus import Client, ServerError
from sugar_network.local.activities import checkins
from sugar_network.local import api_url, server_mode
from sugar_network_webui import webui_port


def GlibClient():
    # Avoid importing Glib stuff for non-glib clients
    from sugar_network.client import glib_client
    return glib_client.GlibClient()
