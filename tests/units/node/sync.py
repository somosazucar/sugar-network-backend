#!/usr/bin/env python
# sugar-lint: disable

import os
import uuid
from StringIO import StringIO
import cPickle as pickle
from os.path import exists

from __init__ import tests

from sugar_network.node import sync
from sugar_network.toolkit import BUFFER_SIZE


class SyncTest(tests.Test):

    def test_decode(self):
        stream = StringIO()
        pickle.dump({'foo': 'bar'}, stream)
        stream.seek(0)
        packets_iter = sync.decode(stream)
        self.assertRaises(EOFError, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

        pickle.dump({'packet': 1, 'bar': 'foo'}, stream)
        stream.seek(0)
        packets_iter = sync.decode(stream)
        with next(packets_iter) as packet:
            self.assertEqual(1, packet.name)
            self.assertEqual('foo', packet['bar'])
            packet_iter = iter(packet)
            self.assertRaises(EOFError, packet_iter.next)
        self.assertRaises(EOFError, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

        pickle.dump({'payload': 1}, stream)
        stream.seek(0)
        packets_iter = sync.decode(stream)
        with next(packets_iter) as packet:
            self.assertEqual(1, packet.name)
            packet_iter = iter(packet)
            self.assertEqual({'payload': 1}, next(packet_iter))
            self.assertRaises(EOFError, packet_iter.next)
        self.assertRaises(EOFError, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

        pickle.dump({'packet': 2}, stream)
        pickle.dump({'payload': 2}, stream)
        stream.seek(0)
        packets_iter = sync.decode(stream)
        with next(packets_iter) as packet:
            self.assertEqual(1, packet.name)
            packet_iter = iter(packet)
            self.assertEqual({'payload': 1}, next(packet_iter))
            self.assertRaises(StopIteration, packet_iter.next)
        with next(packets_iter) as packet:
            self.assertEqual(2, packet.name)
            packet_iter = iter(packet)
            self.assertEqual({'payload': 2}, next(packet_iter))
            self.assertRaises(EOFError, packet_iter.next)
        self.assertRaises(EOFError, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

        pickle.dump({'packet': 'last'}, stream)
        stream.seek(0)
        packets_iter = sync.decode(stream)
        with next(packets_iter) as packet:
            self.assertEqual(1, packet.name)
            packet_iter = iter(packet)
            self.assertEqual({'payload': 1}, next(packet_iter))
            self.assertRaises(StopIteration, packet_iter.next)
        with next(packets_iter) as packet:
            self.assertEqual(2, packet.name)
            packet_iter = iter(packet)
            self.assertEqual({'payload': 2}, next(packet_iter))
            self.assertRaises(StopIteration, packet_iter.next)
        self.assertRaises(StopIteration, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

    def test_decode_Empty(self):
        stream = StringIO()
        self.assertRaises(EOFError, sync.decode(stream).next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

        stream = StringIO()
        pickle.dump({'foo': 'bar'}, stream)
        stream.seek(0)
        packets_iter = sync.decode(stream)
        self.assertRaises(EOFError, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

        pickle.dump({'packet': 'last'}, stream)
        stream.seek(0)
        packets_iter = sync.decode(stream)
        self.assertRaises(StopIteration, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

    def test_decode_SkipPackets(self):
        stream = StringIO()
        pickle.dump({'packet': 1}, stream)
        pickle.dump({'payload': 1}, stream)
        pickle.dump({'payload': 11}, stream)
        pickle.dump({'payload': 111}, stream)
        pickle.dump({'packet': 2}, stream)
        pickle.dump({'payload': 2}, stream)
        pickle.dump({'packet': 'last'}, stream)

        stream.seek(0)
        packets_iter = sync.decode(stream)
        next(packets_iter)
        with next(packets_iter) as packet:
            self.assertEqual(2, packet.name)
            packet_iter = iter(packet)
            self.assertEqual({'payload': 2}, next(packet_iter))
            self.assertRaises(StopIteration, packet_iter.next)
        self.assertRaises(StopIteration, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

        stream.seek(0)
        packets_iter = sync.decode(stream)
        with next(packets_iter) as packet:
            self.assertEqual(1, packet.name)
            packet_iter = iter(packet)
            self.assertEqual({'payload': 1}, next(packet_iter))
        with next(packets_iter) as packet:
            self.assertEqual(2, packet.name)
            packet_iter = iter(packet)
            self.assertEqual({'payload': 2}, next(packet_iter))
            self.assertRaises(StopIteration, packet_iter.next)
        self.assertRaises(StopIteration, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

    def test_encode(self):
        self.assertEqual([
            pickle.dumps({'packet': 'last'}),
            ],
            [i for i in sync.encode()])

        self.assertEqual([
            pickle.dumps({'packet': None, 'foo': 'bar'}),
            pickle.dumps({'packet': 'last'}),
            ],
            [i for i in sync.encode((None, None, None), foo='bar')])

        self.assertEqual([
            pickle.dumps({'packet': 1}),
            pickle.dumps({'packet': '2', 'n': 2}),
            pickle.dumps({'packet': '3', 'n': 3}),
            pickle.dumps({'packet': 'last'}),
            ],
            [i for i in sync.encode(
                (1, {}, None),
                ('2', {'n': 2}, []),
                ('3', {'n': 3}, iter([])),
                )])

        self.assertEqual([
            pickle.dumps({'packet': 1}),
            pickle.dumps({1: 1}),
            pickle.dumps({'packet': 2}),
            pickle.dumps({2: 2}),
            pickle.dumps({2: 2}),
            pickle.dumps({'packet': 3}),
            pickle.dumps({3: 3}),
            pickle.dumps({3: 3}),
            pickle.dumps({3: 3}),
            pickle.dumps({'packet': 'last'}),
            ],
            [i for i in sync.encode(
                (1, None, [{1: 1}]),
                (2, None, [{2: 2}, {2: 2}]),
                (3, None, [{3: 3}, {3: 3}, {3: 3}]),
                )])

    def test_limited_encode(self):
        header_size = len(pickle.dumps({'packet': 'first'}))
        record_size = len(pickle.dumps({'record': 0}))

        def content():
            yield {'record': 1}
            yield {'record': 2}
            yield {'record': 3}

        i = sync.limited_encode(header_size + record_size, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'record': 1}, pickle.loads(i.send(header_size)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(header_size + record_size)))
        self.assertRaises(StopIteration, i.next)

        i = sync.limited_encode(header_size + record_size, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(header_size + 1)))
        self.assertRaises(StopIteration, i.next)

        i = sync.limited_encode(header_size + record_size * 2, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'record': 1}, pickle.loads(i.send(header_size)))
        self.assertEqual({'record': 2}, pickle.loads(i.send(header_size + record_size)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(header_size + record_size * 2)))
        self.assertRaises(StopIteration, i.next)

        i = sync.limited_encode(header_size + record_size * 2, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'record': 1}, pickle.loads(i.send(header_size)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(header_size + record_size + 1)))
        self.assertRaises(StopIteration, i.next)

    def test_limited_encode_FinalRecords(self):
        header_size = len(pickle.dumps({'packet': 'first'}))
        record_size = len(pickle.dumps({'record': 0}))

        def content():
            try:
                yield {'record': 1}
                yield {'record': 2}
                yield {'record': 3}
            except StopIteration:
                pass
            yield {'record': 4}
            yield {'record': 5}

        i = sync.limited_encode(header_size + record_size, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'record': 4}, pickle.loads(i.send(header_size + 1)))
        self.assertEqual({'record': 5}, pickle.loads(i.send(999999999)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(999999999)))
        self.assertRaises(StopIteration, i.next)

        i = sync.limited_encode(header_size + record_size, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'record': 1}, pickle.loads(i.send(header_size)))
        self.assertEqual({'record': 4}, pickle.loads(i.send(header_size + record_size * 2 - 1)))
        self.assertEqual({'record': 5}, pickle.loads(i.send(999999999)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(999999999)))
        self.assertRaises(StopIteration, i.next)

    def test_limited_encode_Blobs(self):
        header_size = len(pickle.dumps({'packet': 'first'}))
        blob_header_size = len(pickle.dumps({'blob_size': 100}))
        record_size = len(pickle.dumps({'record': 2}))
        self.touch(('blob', '*' * 100))

        def content():
            yield {'blob': 'blob'}
            yield {'record': 2}
            yield {'record': 3}

        i = sync.limited_encode(header_size + blob_header_size + 99, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(header_size)))
        self.assertRaises(StopIteration, i.next)

        i = sync.limited_encode(header_size + blob_header_size + 100, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'blob_size': 100}, pickle.loads(i.send(header_size)))
        self.assertEqual('*' * 100, i.send(header_size + blob_header_size))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(header_size + blob_header_size + 100)))
        self.assertRaises(StopIteration, i.next)

        i = sync.limited_encode(header_size + blob_header_size + 100 + record_size - 1, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'blob_size': 100}, pickle.loads(i.send(header_size)))
        self.assertEqual('*' * 100, i.send(header_size + blob_header_size))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(header_size + blob_header_size + 100)))
        self.assertRaises(StopIteration, i.next)

        i = sync.limited_encode(header_size + blob_header_size + 100 + record_size, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'blob_size': 100}, pickle.loads(i.send(header_size)))
        self.assertEqual('*' * 100, i.send(header_size + blob_header_size))
        self.assertEqual({'record': 2}, pickle.loads(i.send(header_size + blob_header_size + 100)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(header_size + blob_header_size + 100 + record_size)))
        self.assertRaises(StopIteration, i.next)

    def test_limited_encode_FinalBlobs(self):
        header_size = len(pickle.dumps({'packet': 'first'}))
        blob_header_size = len(pickle.dumps({'blob_size': 100}))
        record_size = len(pickle.dumps({'record': 2}))
        self.touch(('blob', '*' * 100))

        def content():
            try:
                yield {'record': 1}
            except StopIteration:
                pass
            yield {'blob': 'blob'}
            yield {'record': 3}

        i = sync.limited_encode(header_size, ('first', None, content()), ('second', None, content()))
        self.assertEqual({'packet': 'first'}, pickle.loads(i.send(None)))
        self.assertEqual({'blob_size': 100}, pickle.loads(i.send(header_size)))
        self.assertEqual('*' * 100, i.send(999999999))
        self.assertEqual({'record': 3}, pickle.loads(i.send(999999999)))
        self.assertEqual({'packet': 'last'}, pickle.loads(i.send(999999999)))
        self.assertRaises(StopIteration, i.next)

    def test_chunked_encode(self):
        output = sync.chunked_encode()
        self.assertEqual({'packet': 'last'}, pickle.loads(decode_chunked(output.read(100))))

        data = [{'foo': 1}, {'bar': 2}]
        data_stream = pickle.dumps({'packet': 'packet'})
        for record in data:
            data_stream += pickle.dumps(record)
        data_stream += pickle.dumps({'packet': 'last'})

        output = sync.chunked_encode(('packet', None, iter(data)))
        dump = StringIO()
        while True:
            chunk = output.read(1)
            if not chunk:
                break
            dump.write(chunk)
        self.assertEqual(data_stream, decode_chunked(dump.getvalue()))

        output = sync.chunked_encode(('packet', None, iter(data)))
        dump = StringIO()
        while True:
            chunk = output.read(2)
            if not chunk:
                break
            dump.write(chunk)
        self.assertEqual(data_stream, decode_chunked(dump.getvalue()))

        output = sync.chunked_encode(('packet', None, iter(data)))
        dump = StringIO()
        while True:
            chunk = output.read(1000)
            if not chunk:
                break
            dump.write(chunk)
        self.assertEqual(data_stream, decode_chunked(dump.getvalue()))

    def test_encode_Blobs(self):
        self.touch(('1', 'a'))
        self.touch(('2', 'bb'))
        self.touch(('3', 'ccc'))

        self.assertEqual([
            pickle.dumps({'packet': 1}),
            pickle.dumps({'num': 1, 'blob_size': 1}),
            'a',
            pickle.dumps({'num': 2, 'blob_size': 2}),
            'bb',
            pickle.dumps({'packet': 2}),
            pickle.dumps({'num': 3, 'blob_size': 3}),
            'ccc',
            pickle.dumps({'packet': 'last'}),
            ],
            [i for i in sync.encode(
                (1, None, [{'num': 1, 'blob': '1'}, {'num': 2, 'blob': '2'}]),
                (2, None, [{'num': 3, 'blob': '3'}]),
                )])

    def test_decode_Blobs(self):
        stream = StringIO()
        pickle.dump({'packet': 1}, stream)
        pickle.dump({'num': 1, 'blob_size': 1}, stream)
        stream.write('a')
        pickle.dump({'num': 2, 'blob_size': 2}, stream)
        stream.write('bb')
        pickle.dump({'packet': 2}, stream)
        pickle.dump({'num': 3, 'blob_size': 3}, stream)
        stream.write('ccc')
        pickle.dump({'packet': 'last'}, stream)
        stream.seek(0)

        packets_iter = sync.decode(stream)
        with next(packets_iter) as packet:
            self.assertEqual(1, packet.name)
            self.assertEqual([
                (1, 1, 'a'),
                (2, 2, 'bb'),
                ],
                [(i['num'], i['blob_size'], i['blob'].read()) for i in packet])
        with next(packets_iter) as packet:
            self.assertEqual(2, packet.name)
            self.assertEqual([
                (3, 3, 'ccc'),
                ],
                [(i['num'], i['blob_size'], i['blob'].read()) for i in packet])
        self.assertRaises(StopIteration, packets_iter.next)
        self.assertEqual(len(stream.getvalue()), stream.tell())

    def test_sneakernet_decode(self):
        sync.sneakernet_encode([
            ('first', {'packet_prop': 1}, [
                {'record': 1},
                {'record': 2},
                ]),
            ('second', {'packet_prop': 2}, [
                {'record': 3},
                {'record': 4},
                ]),
            ],
            root='.', package_prop=1, limit=999999999)
        sync.sneakernet_encode([
            ('third', {'packet_prop': 3}, [
                {'record': 5},
                {'record': 6},
                ]),
            ],
            root='.', package_prop=2, limit=999999999)

        self.assertEqual([
            ({'packet_prop': 1, 'package_prop': 1, 'packet': 'first'}, [{'record': 1}, {'record': 2}]),
            ({'packet_prop': 2, 'package_prop': 1, 'packet': 'second'}, [{'record': 3}, {'record': 4}]),
            ({'packet_prop': 3, 'package_prop': 2, 'packet': 'third'}, [{'record': 5}, {'record': 6}]),
            ],
            sorted([(packet.props, [i for i in packet]) for packet in sync.sneakernet_decode('.')]))

    def test_sneakernet_decode_CleanupOutdatedFiles(self):
        sync.sneakernet_encode([('first', None, None)], path='.package', src='node', session='session', limit=999999999)

        self.assertEqual(1, len([i for i in sync.sneakernet_decode('.')]))
        assert exists('.package')

        self.assertEqual(1, len([i for i in sync.sneakernet_decode('.', node='foo')]))
        assert exists('.package')

        self.assertEqual(0, len([i for i in sync.sneakernet_decode('.', node='node', session='session')]))
        assert exists('.package')

        self.assertEqual(0, len([i for i in sync.sneakernet_decode('.', node='node', session='session2')]))
        assert not exists('.package')

    def test_sneakernet_encode(self):
        payload = ''.join([str(uuid.uuid4()) for i in xrange(5000)])

        def content():
            yield {'record': payload}
            yield {'record': payload}

        class statvfs(object):
            f_bfree = None
            f_frsize = 1
        self.override(os, 'statvfs', lambda *args: statvfs())

        statvfs.f_bfree = sync._SNEAKERNET_RESERVED_SIZE
        self.assertEqual(False, sync.sneakernet_encode([('first', None, content())], root='1'))
        self.assertEqual(
                [({'packet': 'first'}, [])],
                [(packet.props, [i for i in packet]) for packet in sync.sneakernet_decode('1')])

        statvfs.f_bfree += len(payload) + len(payload) / 2
        self.assertEqual(False, sync.sneakernet_encode([('first', None, content())], root='2'))
        self.assertEqual(
                [({'packet': 'first'}, [{'record': payload}])],
                [(packet.props, [i for i in packet]) for packet in sync.sneakernet_decode('2')])

        statvfs.f_bfree += len(payload)
        self.assertEqual(True, sync.sneakernet_encode([('first', None, content())], root='3'))
        self.assertEqual(
                [({'packet': 'first'}, [{'record': payload}, {'record': payload}])],
                [(packet.props, [i for i in packet]) for packet in sync.sneakernet_decode('3')])


def decode_chunked(encdata):
    offset = 0
    newdata = ''
    while (encdata != ''):
        off = int(encdata[:encdata.index("\r\n")],16)
        if off == 0:
            break
        encdata = encdata[encdata.index("\r\n") + 2:]
        newdata = "%s%s" % (newdata, encdata[:off])
        encdata = encdata[off+2:]
    return newdata


if __name__ == '__main__':
    tests.main()
