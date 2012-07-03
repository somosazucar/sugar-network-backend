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

import logging
from gettext import gettext as _

import active_document as ad
from sugar_network.toolkit import sneakernet
from sugar_network.toolkit.collection import Sequences
from active_toolkit import coroutine, enforce


_DIFF_CHUNK = 1024

_logger = logging.getLogger('node.sync')


class SyncCommands(object):

    volume = None

    def __init__(self, api_url):
        self._api_url = api_url

    @ad.volume_command(method='POST', cmd='sync')
    def sync(self, request, response, accept_length=None):
        _logger.debug('Pushing %s bytes length packet', request.content_length)
        with sneakernet.InPacket(stream=request) as packet:
            enforce('src' in packet.header and \
                    packet.header['src'] != self._api_url,
                    _('Misaddressed packet'))
            enforce('dst' in packet.header and \
                    packet.header['dst'] == self._api_url,
                    _('Misaddressed packet'))

            if packet.header.get('type') == 'push':
                out_packet = sneakernet.OutPacket('ack')
                out_packet.header['dst'] = packet.header['src']
                out_packet.header['push_sequence'] = packet.header['sequence']
                out_packet.header['pull_sequence'] = self._push(packet)
            elif packet.header.get('type') == 'pull':
                out_packet = sneakernet.OutPacket('push', limit=accept_length)
                out_packet.header['sequence'] = out_seq = Sequences()
                self._pull(packet.header['sequence'], out_seq, out_packet)
            else:
                raise RuntimeError(_('Unrecognized packet'))

            if out_packet.closed:
                response.content_type = 'application/octet-stream'
                return

            out_packet.header['src'] = self._api_url
            content, response.content_length = out_packet.pop_content()
            return content

    def _push(self, packet):
        merged_seq = Sequences()
        for msg in packet:
            document = msg['document']
            seqno = self.volume[document].merge(msg['guid'], msg['diff'])
            merged_seq[document].include(seqno, seqno)
        return merged_seq

    def _pull(self, in_seq, out_seq, packet):
        for document, directory in self.volume.items():

            def patch():
                seq, patch = directory.diff(in_seq[document],
                        limit=_DIFF_CHUNK)
                try:
                    for header, diff in patch:
                        coroutine.dispatch()
                        header['diff'] = diff
                        yield header
                finally:
                    if seq:
                        out_seq[document].include(*seq)
                    else:
                        packet.clear()

            directory.commit()
            try:
                packet.push_messages(patch(), document=document)
            except sneakernet.DiskFull:
                _logger.debug('Reach package size limit')
