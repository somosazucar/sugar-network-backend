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
import hashlib
import tempfile
from os.path import exists, join
from gettext import gettext as _

import active_document as ad
from sugar_network import node
from sugar_network.toolkit.sneakernet import InPacket, OutBufferPacket, \
        OutFilePacket, DiskFull
from sugar_network.toolkit.collection import Sequence
from active_toolkit import util, enforce


_logger = logging.getLogger('node.commands')


class NodeCommands(ad.VolumeCommands):

    def __init__(self, volume, subscriber=None, master_url=None):
        ad.VolumeCommands.__init__(self, volume)
        self._subscriber = subscriber
        self._is_master = bool(master_url)

        if self._is_master:
            self._guid = master_url
        else:
            guid_path = join(volume.root, 'node')
            if exists(guid_path):
                with file(guid_path) as f:
                    self._guid = f.read().strip()
            else:
                self._guid = ad.uuid()
                with file(guid_path, 'w') as f:
                    f.write(self._guid)

    @ad.volume_command(method='GET')
    def hello(self, response):
        response.content_type = 'text/html'
        return _HELLO_HTML

    @ad.volume_command(method='GET', cmd='stat')
    def stat(self):
        return {'guid': self._guid,
                'master': self._is_master,
                'seqno': self.volume.seqno.value,
                }

    @ad.volume_command(method='POST', cmd='subscribe',
            permissions=ad.ACCESS_AUTH)
    def subscribe(self):
        enforce(self._subscriber is not None, _('Subscription is disabled'))
        return self._subscriber.new_ticket()

    @ad.document_command(method='DELETE',
            permissions=ad.ACCESS_AUTH | ad.ACCESS_AUTHOR)
    def delete(self, document, guid):
        # Servers data should not be deleted immediately
        # to let master-node synchronization possible
        directory = self.volume[document]
        directory.update(guid, {'layer': ['deleted']})

    @ad.directory_command(method='GET')
    def find(self, document, request, offset=None, limit=None, query=None,
            reply=None, order_by=None, **kwargs):
        if limit is None:
            limit = node.find_limit.value
        elif limit > node.find_limit.value:
            _logger.warning(_('The find limit is restricted to %s'),
                    node.find_limit.value)
            limit = node.find_limit.value
        return ad.VolumeCommands.find(self, document, request, offset, limit,
                query, reply, order_by, **kwargs)

    def resolve(self, request):
        cmd = ad.VolumeCommands.resolve(self, request)
        if cmd is None:
            return

        if cmd.permissions & ad.ACCESS_AUTH:
            enforce(request.principal is not None, node.Unauthorized,
                    _('User is not authenticated'))

        if cmd.permissions & ad.ACCESS_AUTHOR and 'guid' in request:
            doc = self.volume[request['document']].get(request['guid'])
            enforce(request.principal in doc['user'], ad.Forbidden,
                    _('Operation is permitted only for authors'))

        return cmd

    def before_create(self, request, props):
        if request['document'] == 'user':
            props['guid'], props['pubkey'] = _load_pubkey(props['pubkey'])
            props['user'] = [props['guid']]
        else:
            props['user'] = [request.principal]
            self._set_author(props)
        ad.VolumeCommands.before_create(self, request, props)

    def before_update(self, request, props):
        if 'user' in props:
            self._set_author(props)
        ad.VolumeCommands.before_update(self, request, props)

    def _set_author(self, props):
        users = self.volume['user']
        authors = []
        for user_guid in props['user']:
            if not users.exists(user_guid):
                _logger.warning(_('No %r user to set author property'),
                        user_guid)
                continue
            user = users.get(user_guid)
            if user['name']:
                authors.append(user['name'])
        props['author'] = authors


class MasterCommands(NodeCommands):

    def __init__(self, master_url, volume, subscriber=None):
        NodeCommands.__init__(self, volume, subscriber, master_url=master_url)
        self._api_url = master_url

    @ad.volume_command(method='POST', cmd='push')
    def push(self, request, response):
        with InPacket(stream=request) as in_packet:
            enforce('src' in in_packet.header and \
                    in_packet.header['src'] != self._api_url,
                    _('Misaddressed packet'))
            enforce('dst' in in_packet.header and \
                    in_packet.header['dst'] == self._api_url,
                    _('Misaddressed packet'))

            out_packet = OutBufferPacket(src=self._api_url,
                    dst=in_packet.header['src'])
            continue_packet = OutBufferPacket(
                    src=in_packet.header['src'], dst=self._api_url)
            pull_to_forward = Sequence()
            merged_in_seq = Sequence()
            merged_out_seq = Sequence()

            for record in in_packet.records(dst=self._api_url):
                cmd = record.get('cmd')
                if cmd == 'sn_push':
                    if record.get('content_type') == 'blob':
                        record['diff'] = record['blob']
                    seqno = self.volume[record['document']].merge(**record)
                    merged_out_seq.include(seqno, seqno)
                    if 'range' in record:
                        merged_in_seq.include(*record['range'])
                elif cmd == 'sn_pull':
                    # Nodes create singular packet, forward PULLs
                    # to process them in `pull()` later
                    pull_to_forward.include(record['sequence'])

            if merged_out_seq:
                _logger.debug('Merged push with %r seqnos', merged_out_seq)
                out_packet.push(cmd='sn_ack', in_sequence=merged_in_seq,
                        out_sequence=merged_out_seq)
                pull_to_forward.exclude(merged_out_seq)

            if pull_to_forward:
                _logger.debug('Forward %r pull', pull_to_forward)
                continue_packet.push(cmd='sn_pull', sequence=pull_to_forward)

        return self._reply(response, out_packet, continue_packet)

    @ad.volume_command(method='POST', cmd='pull')
    def pull(self, request, response, accept_length=None):
        with OutFilePacket(src=self._api_url,
                limit=accept_length) as out_packet:
            continue_packet = OutBufferPacket(dst=self._api_url)
            pull_seq = Sequence()

            if not request.content_length:
                _logger.debug('Return full synchronization dump')
                pull_seq.include(1, None)
            else:
                with InPacket(stream=request) as in_packet:
                    enforce(in_packet.header.get('src') != self._api_url,
                            _('Misaddressed packet'))
                    enforce('dst' in in_packet.header and \
                            in_packet.header['dst'] == self._api_url,
                            _('Misaddressed packet'))
                    for record in in_packet.records():
                        if record.get('cmd') == 'sn_pull':
                            pull_seq.include(record['sequence'])

            _logger.debug('Writing %r pull', pull_seq)
            try:
                self.volume.diff(pull_seq, out_packet)
            except DiskFull:
                _logger.debug('Postpone %r pull', pull_seq)
                continue_packet.push(cmd='sn_pull', sequence=pull_seq)

            return self._reply(response, out_packet, continue_packet)

    def _reply(self, response, out_packet, continue_packet):
        if out_packet.empty and continue_packet.empty:
            return
        out_packet.header['empty'] = out_packet.empty
        out_packet.header['continue'] = not continue_packet.empty
        if not continue_packet.empty:
            out_packet.push(continue_packet, arcname='continue', force=True)
        response.content_type = out_packet.content_type
        return out_packet.pop()


def _load_pubkey(pubkey):
    pubkey = pubkey.strip()
    try:
        with tempfile.NamedTemporaryFile() as key_file:
            key_file.file.write(pubkey)
            key_file.file.flush()
            # SSH key needs to be converted to PKCS8 to ket M2Crypto read it
            pubkey_pkcs8 = util.assert_call(
                    ['ssh-keygen', '-f', key_file.name, '-e', '-m', 'PKCS8'])
    except Exception:
        message = _('Cannot read DSS public key gotten for registeration')
        util.exception(message)
        if node.trust_users.value:
            logging.warning(_('Failed to read registration pubkey, ' \
                    'but we trust users'))
            # Keep SSH key for further converting to PKCS8
            pubkey_pkcs8 = pubkey
        else:
            raise ad.Forbidden(message)

    return str(hashlib.sha1(pubkey.split()[1]).hexdigest()), pubkey_pkcs8


_HELLO_HTML = """\
<h2>Welcome to Sugar Network API!</h2>
Consult <a href="http://wiki.sugarlabs.org/go/Platform_Team/Sugar_Network/API">
Sugar Labs Wiki</a> to learn how it can be used.
"""
