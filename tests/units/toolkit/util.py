#!/usr/bin/env python
# sugar-lint: disable

import copy
from os.path import exists

from __init__ import tests

from sugar_network.toolkit.util import Seqno, Sequence


class UtilTest(tests.Test):

    def test_Seqno_commit(self):
        seqno = Seqno('seqno')

        self.assertEqual(False, seqno.commit())
        assert not exists('seqno')

        seqno.next()
        self.assertEqual(True, seqno.commit())
        assert exists('seqno')
        self.assertEqual(False, seqno.commit())
        seqno.next()

        seqno = Seqno('seqno')
        self.assertEqual(1, seqno.value)
        self.assertEqual(False, seqno.commit())

    def test_Sequence_empty(self):
        scale = Sequence(empty_value=[1, None])
        self.assertEqual(
                [[1, None]],
                scale)
        assert scale.empty
        scale.exclude(1, 1)
        assert not scale.empty

        scale = Sequence()
        self.assertEqual(
                [],
                scale)
        assert scale.empty
        scale.include(1, None)
        assert not scale.empty

    def test_Sequence_exclude(self):
        scale = Sequence(empty_value=[1, None])
        scale.exclude(1, 10)
        self.assertEqual(
                [[11, None]],
                scale)

        scale = Sequence(empty_value=[1, None])
        scale.exclude(5, 10)
        self.assertEqual(
                [[1, 4], [11, None]],
                scale)

        scale.exclude(2, 2)
        self.assertEqual(
                [[1, 1], [3, 4], [11, None]],
                scale)

        scale.exclude(1, 1)
        self.assertEqual(
                [[3, 4], [11, None]],
                scale)

        scale.exclude(3, 3)
        self.assertEqual(
                [[4, 4], [11, None]],
                scale)

        scale.exclude(1, 20)
        self.assertEqual(
                [[21, None]],
                scale)

        scale.exclude(21, 21)
        self.assertEqual(
                [[22, None]],
                scale)

    def test_Sequence_include_JoinExistingItems(self):
        scale = Sequence()

        scale.include(1, None)
        self.assertEqual(
                [[1, None]],
                scale)

        scale.include(2, None)
        self.assertEqual(
                [[1, None]],
                scale)

        scale.include(4, 5)
        self.assertEqual(
                [[1, None]],
                scale)

        scale.exclude(2, 2)
        scale.exclude(4, 4)
        scale.exclude(6, 6)
        scale.exclude(9, 9)
        self.assertEqual(
                [[1, 1],
                    [3, 3],
                    [5, 5],
                    [7, 8],
                    [10, None]],
                scale)

        scale.include(10, 20)
        self.assertEqual(
                [[1, 1],
                    [3, 3],
                    [5, 5],
                    [7, 8],
                    [10, None]],
                scale)

        scale.include(8, 20)
        self.assertEqual(
                [[1, 1],
                    [3, 3],
                    [5, 5],
                    [7, None]],
                scale)

        scale.include(5, None)
        self.assertEqual(
                [[1, 1],
                    [3, 3],
                    [5, None]],
                scale)

        scale.include(1, None)
        self.assertEqual(
                [[1, None]],
                scale)

    def test_Sequence_include_InsertNewItems(self):
        scale = Sequence()

        scale.include(8, 10)
        scale.include(3, 3)
        self.assertEqual(
                [[3, 3],
                    [8, 10]],
                scale)

        scale.include(9, 11)
        self.assertEqual(
                [[3, 3],
                    [8, 11]],
                scale)

        scale.include(7, 12)
        self.assertEqual(
                [[3, 3],
                    [7, 12]],
                scale)

        scale.include(5, 5)
        self.assertEqual(
                [[3, 3],
                    [5, 5],
                    [7, 12]],
                scale)

        scale.include(4, 4)
        self.assertEqual(
                [[3, 5],
                    [7, 12]],
                scale)

        scale.include(1, 1)
        self.assertEqual(
                [[1, 1],
                    [3, 5],
                    [7, 12]],
                scale)

        scale.include(2, None)
        self.assertEqual(
                [[1, None]],
                scale)

    def teste_Sequence_Invert(self):
        scale_1 = Sequence(empty_value=[1, None])
        scale_1.exclude(2, 2)
        scale_1.exclude(5, 10)

        scale_2 = copy.deepcopy(scale_1[:])
        scale_2[-1][1] = 20

        self.assertEqual(
                [
                    [1, 1],
                    [3, 4],
                    [11, None],
                    ],
                scale_1)
        scale_1.exclude(scale_2)
        self.assertEqual(
                [[21, None]],
                scale_1)

    def test_Sequence_contains(self):
        scale = Sequence(empty_value=[1, None])
        scale.exclude(2, 2)
        scale.exclude(5, 10)

        assert 1 in scale
        assert 2 not in scale
        assert 3 in scale
        assert 5 not in scale
        assert 10 not in scale
        assert 11 in scale

    def test_Sequence_first(self):
        scale = Sequence()
        self.assertEqual(0, scale.first)

        scale = Sequence(empty_value=[1, None])
        self.assertEqual(1, scale.first)
        scale.exclude(1, 3)
        self.assertEqual(4, scale.first)

    def test_Sequence_include(self):
        rng = Sequence()
        rng.include(2, 2)
        self.assertEqual(
                [[2, 2]],
                rng)
        rng.include(7, 10)
        self.assertEqual(
                [[2, 2], [7, 10]],
                rng)
        rng.include(5, 5)
        self.assertEqual(
                [[2, 2], [5, 5], [7, 10]],
                rng)
        rng.include(15, None)
        self.assertEqual(
                [[2, 2], [5, 5], [7, 10], [15, None]],
                rng)
        rng.include(3, 5)
        self.assertEqual(
                [[2, 5], [7, 10], [15, None]],
                rng)
        rng.include(11, 14)
        self.assertEqual(
                [[2, 5], [7, None]],
                rng)

        rng = Sequence()
        rng.include(10, None)
        self.assertEqual(
                [[10, None]],
                rng)
        rng.include(7, 8)
        self.assertEqual(
                [[7, 8], [10, None]],
                rng)
        rng.include(2, 2)
        self.assertEqual(
                [[2, 2], [7, 8], [10, None]],
                rng)

    def test_Sequence_Union(self):
        seq_1 = Sequence()
        seq_1.include(1, 2)
        seq_2 = Sequence()
        seq_2.include(3, 4)
        seq_1.include(seq_2)
        self.assertEqual(
                [[1, 4]],
                seq_1)

        seq_1 = Sequence()
        seq_1.include(1, None)
        seq_2 = Sequence()
        seq_2.include(3, 4)
        seq_1.include(seq_2)
        self.assertEqual(
                [[1, None]],
                seq_1)

        seq_2 = Sequence()
        seq_2.include(1, None)
        seq_1 = Sequence()
        seq_1.include(3, 4)
        seq_1.include(seq_2)
        self.assertEqual(
                [[1, None]],
                seq_1)

        seq_1 = Sequence()
        seq_1.include(1, None)
        seq_2 = Sequence()
        seq_2.include(2, None)
        seq_1.include(seq_2)
        self.assertEqual(
                [[1, None]],
                seq_1)

        seq_1 = Sequence()
        seq_2 = Sequence()
        seq_2.include(seq_1)
        self.assertEqual([], seq_2)

        seq_1 = Sequence()
        seq_2 = Sequence()
        seq_2.include(1, None)
        seq_2.include(seq_1)
        self.assertEqual([[1, None]], seq_2)

        seq = Sequence()
        seq.include(10, 11)
        seq.include(None)
        self.assertEqual([[10, 11]], seq)

    def test_Sequence_last(self):
        seq = Sequence()
        self.assertEqual(None, seq.last)

        seq = Sequence()
        seq.include(10, None)
        self.assertEqual(None, seq.last)

        seq = Sequence()
        seq.include(1, 1)
        seq.include(3, 5)
        seq.include(10, 11)
        self.assertEqual(11, seq.last)


if __name__ == '__main__':
    tests.main()
