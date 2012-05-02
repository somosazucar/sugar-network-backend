# Copyright (C) 2012, Aleksey Lim
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
import logging
from os.path import exists
from gettext import gettext as _

from gevent import socket
from gevent.server import StreamServer

from local_document import ipc, env
from local_document.socket import SocketFile
from active_document import util, enforce


_logger = logging.getLogger('local_document.server')


class Server(object):

    def __init__(self, mounts):
        self._mounts = mounts
        self._server = None

    def serve_forever(self):
        accept_path = env.ensure_path('run', 'accept')
        if exists(accept_path):
            os.unlink(accept_path)
        # pylint: disable-msg=E1101
        accept = socket.socket(socket.AF_UNIX)
        accept.bind(accept_path)
        accept.listen(5)

        # Clients write to rendezvous named pipe, in block mode,
        # to make sure that server is started
        rendezvous = ipc.rendezvous(server=True)

        self._server = StreamServer(accept, self._serve_client)
        try:
            self._server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            os.close(rendezvous)
            os.unlink(accept_path)
            self._server = None

    def stop(self):
        if self._server is not None:
            self._server.stop()

    def _serve_client(self, conn, address):
        conn_file = SocketFile(conn)

        _logger.debug('New client: connection=%r', conn_file)

        def process_message(message):
            _logger.debug('Got a call: connection=%r %r', conn_file, message)

            enforce('cmd' in message, _('Argument "cmd" was not specified'))
            cmd = message.pop('cmd')

            if 'mountpoint' in message:
                mountpoint = message.pop('mountpoint')
            else:
                mountpoint = '/'

            reply = self._mounts.call(conn_file, cmd, mountpoint, message)
            conn_file.write_message(reply)

            _logger.debug('Send reply: connection=%r %r', conn_file, reply)

        try:
            while True:
                try:
                    message = conn_file.read_message()
                    if message is None:
                        break
                    process_message(message)
                except Exception, error:
                    util.exception(_('Fail to process message: %s'), error)
                    conn_file.write_message({'error': str(error)})
        finally:
            _logger.debug('Quit client: connection=%r', conn_file)
            conn_file.close()
