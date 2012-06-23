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

"""Server synchronization routines.

PUSH packet:

    `type`: `push`
    `sender`: sender's GUID to push from
    `[receiver]`: reseiver's GUID to push to, optional for packets from master
    `sequence`: `Sequence` associated with packet's payload

ACK packet:

    `type`: `ack`
    `sender`: master's GUID
    `receiver`: reseiver's GUID ack is intended for
    `push_sequence`: original PUSH packet's `sequence`
    `pull_sequence`: `Sequence` after merging original PUSH packet

PULL packet:

    `type`: `pull`
    `sender`: sender's GUID to pull to
    `receiver`: reseiver's GUID to pull from
    `sequence`: `Sequence` to pull for

"""
import os
import json
import logging
from os.path import exists
from gettext import gettext as _

import active_document as ad
from sugar_network import local
from sugar_network.node.sequence import Sequence
from sugar_network.node import sneakernet
from active_toolkit import util, coroutine, enforce


_logger = logging.getLogger('node.sync')


class Node(object):

    volume = None

    def __init__(self, guid, master_guid):
        self._guid = guid
        self._master_guid = master_guid
        self._push_seq = _PersistentSequence('push.sequence', [1, None])
        self._pull_seq = _PersistentSequence('pull.sequence', [1, None])

    @ad.volume_command(method='POST', cmd='sync',
            access_level=ad.ACCESS_LOCAL)
    def sync(self, path):
        to_push_seq = _Sequence()
        to_push_seq.update(self._push_seq)

        while True:
            self._import(path)
            with sneakernet.OutPacket('push', root=path,
                    sender=self._guid, receiver=self._master_guid) as packet:
                packet['sequence'] = pushed_seq = _Sequence()
                try:
                    self._export(to_push_seq, pushed_seq, packet)
                except sneakernet.DiskFull:
                    path = sneakernet.switch_disk(path)
                except Exception:
                    packet.clear()
                    raise
                else:
                    break

    def _import(self, path):
        for packet in sneakernet.walk(path):
            if packet['type'] == 'push':
                if packet['sender'] != self._guid:
                    _logger.info('Processing %r PUSH packet', packet)
                    for msg in packet:
                        directory = self.volume[msg['document']]
                        directory.merge(msg['guid'], msg['diff'])
                    if packet['sender'] == self._master_guid:
                        self._pull_seq.exclude(packet['sequence'])
                else:
                    _logger.info('Remove our previous %r PUSH packet', packet)
                    os.unlink(packet.path)
            elif packet['type'] == 'ack':
                if packet['sender'] == self._master_guid and \
                        packet['receiver'] == self._guid:
                    _logger.info('Processing %r ACK packet', packet)
                    self._push_seq.exclude(packet['push_sequence'])
                    self._pull_seq.exclude(packet['pull_sequence'])
                    _logger.debug('Remove processed %r ACK packet', packet)
                    os.unlink(packet.path)
                else:
                    _logger.info('Ignore misaddressed %r ACK packet', packet)
            else:
                _logger.info('No need to process %r packet', packet)

    def _export(self, to_push_seq, pushed_seq, packet):
        for document, directory in self.volume.items():

            def patch():
                for seq, guid, diff in directory.diff(to_push_seq[document]):
                    coroutine.dispatch()
                    yield {'guid': guid, 'diff': diff}
                    to_push_seq[document].exclude(seq)
                    pushed_seq[document].include(seq)

            directory.commit()
            packet.push_messages(patch(), document=document)


class Master(object):

    volume = None

    def __init__(self, guid):
        self._guid = guid

    @ad.volume_command(method='POST', cmd='sync')
    def sync(self, request, response, accept_length=None):
        _logger.info(_('Pushing %s bytes length packet'),
                request.content_length)
        with sneakernet.InPacket(stream=request) as packet:
            enforce(packet['sender'] and packet['sender'] != self._guid,
                    _('Misaddressed packet'))
            enforce(packet['receiver'] and packet['receiver'] == self._guid,
                    _('Misaddressed packet'))

            if packet['type'] == 'push':
                out_packet = sneakernet.OutPacket('ack')
                out_packet['receiver'] = packet['sender']
                out_packet['push_sequence'] = packet['sequence']
                out_packet['pull_sequence'] = self._push(packet)
            elif packet['type'] == 'pull':
                out_packet = sneakernet.OutPacket('push', limit=accept_length)
                out_packet['sequence'] = out_seq = _Sequence()
                self._pull(packet['sequence'], out_seq, out_packet)
            else:
                raise RuntimeError(_('Unrecognized packet'))

            out_packet['sender'] = self._guid
            content, response.content_length = out_packet.pop_content()
            return content

    def _push(self, packet):
        merged_seq = _Sequence()
        for msg in packet:
            document = msg['document']
            seqno = self.volume[document].merge(msg['guid'], msg['diff'])
            merged_seq[document].include(seqno, seqno)
        return merged_seq

    def _pull(self, in_seq, out_seq, packet):
        for document, directory in self.volume.items():

            def patch():
                for seq, guid, diff in directory.diff(in_seq[document]):
                    coroutine.dispatch()
                    yield guid, diff
                    out_seq[document].include(seq)

            directory.commit()
            packet.push_messages(patch(), document=document)


class _Sequence(dict):

    def __init__(self, **kwargs):
        dict.__init__(self)
        self._new_item_kwargs = kwargs

    def __getitem__(self, key):
        value = self.get(key)
        if value is None:
            value = self[key] = Sequence(**self._new_item_kwargs)
        return value

    def exclude(self, other):
        for key, seq in other.items():
            if key in self:
                self[key].exclude(seq)


class _PersistentSequence(_Sequence):

    def __init__(self, name, empty_value):
        _Sequence.__init__(self, empty_value=empty_value)
        self._path = local.path(name)
        if exists(self._path):
            with file(self._path) as f:
                self.update(json.load(f))

    def exclude(self, other):
        _Sequence.exclude(self, other)
        self._commit()

    def update(self, other):
        for key, seq in other.items():
            self[key] = Sequence(seq)

    def _commit(self):
        with util.new_file(self._path) as f:
            json.dump(self, f)
            f.flush()
            os.fsync(f.fileno())
