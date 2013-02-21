# Copyright (C) 2012-2013 Aleksey Lim
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
from os.path import join, exists, isdir

from sugar_network import node
from sugar_network.toolkit.rrd import Rrd
from sugar_network.toolkit import Option, util, pylru


stats_user = Option(
        'accept personalized users statistics',
        default=False, type_cast=Option.bool_cast, action='store_true')

stats_user_step = Option(
        'step interval in seconds for users\' RRD databases',
        default=60, type_cast=int)

stats_user_rras = Option(
        'space separated list of RRAs for users\' RRD databases',
        default=[
            'RRA:AVERAGE:0.5:1:4320',   # one day with 60s step
            'RRA:AVERAGE:0.5:5:2016',   # one week with 5min step
            ],
        type_cast=Option.list_cast, type_repr=Option.list_repr)

_logger = logging.getLogger('node.stats_user')
_user_cache = pylru.lrucache(32)


def get_rrd(user):
    if user in _user_cache:
        return _user_cache[user]
    else:
        rrd = _user_cache[user] = Rrd(_rrd_path(user),
                stats_user_step.value, stats_user_rras.value)
        return rrd


def diff(in_seq=None, packet=None):
    for user, rrd in _walk_rrd(join(node.stats_root.value, 'user')):
        in_seq.setdefault(user, {})

        for db in rrd:
            seq = in_seq[user].get(db.name)
            if seq is None:
                seq = in_seq[user][db.name] = util.PersistentSequence(
                        join(rrd.root, db.name + '.push'), [1, None])
            elif seq is not dict:
                seq = in_seq[user][db.name] = util.Sequence(seq)
            out_seq = util.Sequence()

            def dump():
                for start, end in seq:
                    for timestamp, values in \
                            db.get(max(start, db.first), end or db.last):
                        yield {'timestamp': timestamp, 'values': values}
                        seq.exclude(start, timestamp)
                        out_seq.include(start, timestamp)
                        start = timestamp

            packet.push(dump(), arcname=join('stats', user, db.name),
                    cmd='stats_push', user=user, db=db.name,
                    sequence=out_seq)


def merge(packet):
    return False


def commit(sequences):
    for user, dbs in sequences.items():
        for db, merged in dbs.items():
            seq = util.PersistentSequence(
                    _rrd_path(user, db + '.push'), [1, None])
            seq.exclude(merged)
            seq.commit()


def _walk_rrd(root):
    if not exists(root):
        return
    for users_dirname in os.listdir(root):
        users_dir = join(root, users_dirname)
        if not isdir(users_dir):
            continue
        for user in os.listdir(users_dir):
            yield user, Rrd(join(users_dir, user), stats_user_step.value)


def _rrd_path(user, *args):
    return join(node.stats_root.value, 'user', user[:2], user, *args)