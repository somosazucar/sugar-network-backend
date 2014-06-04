#!/usr/bin/env python
# sugar-lint: disable

from __init__ import tests

from sugar_network import db, model
from sugar_network.client import Connection, keyfile
from sugar_network.model.context import Context
from sugar_network.node.auth import RootAuth
from sugar_network.model.post import Post
from sugar_network.toolkit.coroutine import this
from sugar_network.toolkit import http


class PostTest(tests.Test):

    def test_ShiftContextRating(self):
        volume = db.Volume('db', [Context, Post])
        this.volume = volume

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })
        self.assertEqual([0, 0], volume['context'][context]['rating'])

        volume['post'].create({
            'context': context,
            'type': 'topic',
            'title': {},
            'message': {},
            })
        self.assertEqual([0, 0], volume['context'][context]['rating'])

        volume['post'].create({
            'context': context,
            'type': 'topic',
            'title': {},
            'message': {},
            'vote': 0,
            })
        self.assertEqual([0, 0], volume['context'][context]['rating'])

        volume['post'].create({
            'context': context,
            'type': 'topic',
            'title': {},
            'message': {},
            'vote': 1,
            })
        self.assertEqual([1, 1], volume['context'][context]['rating'])

        volume['post'].create({
            'context': context,
            'type': 'topic',
            'title': {},
            'message': {},
            'vote': 2,
            })
        self.assertEqual([2, 3], volume['context'][context]['rating'])

    def test_ShiftContextRatingOnDeletes(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })

        post1 = this.call(method='POST', path=['post'], content={'context': context, 'type': 'review', 'title': '', 'message': '', 'vote': 1})
        post2 = this.call(method='POST', path=['post'], content={'context': context, 'type': 'review', 'title': '', 'message': '', 'vote': 2})
        self.assertEqual([2, 3], volume['context'][context]['rating'])

        this.call(method='DELETE', path=['post', post1])
        self.assertEqual([1, 2], volume['context'][context]['rating'])

        this.call(method='DELETE', path=['post', post2])
        self.assertEqual([0, 0], volume['context'][context]['rating'])

    def test_DoNotShiftRatingOnZeroVotes(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })

        post1 = this.call(method='POST', path=['post'], content={'context': context, 'type': 'review', 'title': '', 'message': ''})
        self.assertEqual([0, 0], volume['context'][context]['rating'])

        post2 = this.call(method='POST', path=['post'], content={'context': context, 'type': 'review', 'title': '', 'message': ''})
        self.assertEqual([0, 0], volume['context'][context]['rating'])

        this.call(method='DELETE', path=['post', post1])
        self.assertEqual([0, 0], volume['context'][context]['rating'])

        this.call(method='DELETE', path=['post', post2])
        self.assertEqual([0, 0], volume['context'][context]['rating'])

    def test_ShiftTopicRating(self):
        volume = db.Volume('db2', [Context, Post])
        this.volume = volume

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })
        topic = volume['post'].create({
            'context': context,
            'type': 'topic',
            'title': {},
            'message': {},
            })
        self.assertEqual([0, 0], volume['context'][context]['rating'])
        self.assertEqual([0, 0], volume['post'][topic]['rating'])

        volume['post'].create({
            'context': context,
            'topic': topic,
            'type': 'post',
            'title': {},
            'message': {},
            })
        self.assertEqual([0, 0], volume['context'][context]['rating'])
        self.assertEqual([0, 0], volume['post'][topic]['rating'])

        volume['post'].create({
            'context': context,
            'topic': topic,
            'type': 'post',
            'title': {},
            'message': {},
            'vote': 0,
            })
        self.assertEqual([0, 0], volume['context'][context]['rating'])
        self.assertEqual([0, 0], volume['post'][topic]['rating'])

        volume['post'].create({
            'context': context,
            'topic': topic,
            'type': 'post',
            'title': {},
            'message': {},
            'vote': 1,
            })
        self.assertEqual([0, 0], volume['context'][context]['rating'])
        self.assertEqual([1, 1], volume['post'][topic]['rating'])

        volume['post'].create({
            'context': context,
            'topic': topic,
            'type': 'post',
            'title': {},
            'message': {},
            'vote': 2,
            })
        self.assertEqual([0, 0], volume['context'][context]['rating'])
        self.assertEqual([2, 3], volume['post'][topic]['rating'])

    def test_ShiftTopicRatingOnDeletes(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })
        topic = this.call(method='POST', path=['post'], content={'context': context, 'type': 'topic', 'title': '', 'message': ''})

        post1 = this.call(method='POST', path=['post'], content={'context': context, 'topic': topic, 'type': 'post', 'title': '', 'message': '', 'vote': 1})
        post2 = this.call(method='POST', path=['post'], content={'context': context, 'topic': topic, 'type': 'post', 'title': '', 'message': '', 'vote': 2})
        self.assertEqual([2, 3], volume['post'][topic]['rating'])

        this.call(method='DELETE', path=['post', post1])
        self.assertEqual([1, 2], volume['post'][topic]['rating'])

        this.call(method='DELETE', path=['post', post2])
        self.assertEqual([0, 0], volume['post'][topic]['rating'])

    def test_ContextExistance(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['talks'],
            'title': {},
            'summary': {},
            'description': {},
            })

        self.assertRaises(http.BadRequest, this.call, method='POST', path=['post'],
                content={'context': 'absent', 'type': 'topic', 'title': '', 'message': ''})
        assert this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'topic', 'title': '', 'message': ''})

        volume['context'].update(context, {'state': 'deleted'})
        self.assertRaises(http.BadRequest, this.call, method='POST', path=['post'],
                content={'context': context, 'type': 'topic', 'title': '', 'message': ''})

    def test_InappropriateType(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['talks'],
            'title': {},
            'summary': {},
            'description': {},
            })

        self.assertRaises(http.BadRequest, this.call, method='POST', path=['post'],
                content={'context': context, 'type': 'poll', 'title': '', 'message': ''})
        assert this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'topic', 'title': '', 'message': ''})

    def test_InappropriateRelation(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['talks'],
            'title': {},
            'summary': {},
            'description': {},
            })
        topic = this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'topic', 'title': '', 'message': ''})

        self.assertRaises(http.BadRequest, this.call, method='POST', path=['post'],
                content={'context': context, 'type': 'post', 'title': '', 'message': ''})
        assert this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'post', 'topic': topic, 'title': '', 'message': ''})

    def test_DefaultResolution(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })

        topic = this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'topic', 'title': '', 'message': ''})
        self.assertEqual('', volume['post'][topic]['resolution'])

        question = this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'question', 'title': '', 'message': ''})
        self.assertEqual('', volume['post'][question]['resolution'])

        issue = this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'issue', 'title': '', 'message': ''})
        self.assertEqual('new', volume['post'][issue]['resolution'])

        poll = this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'poll', 'title': '', 'message': ''})
        self.assertEqual('open', volume['post'][poll]['resolution'])

    def test_InappropriateResolution(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })
        topic = this.call(method='POST', path=['post'],
                content={'context': context, 'type': 'issue', 'title': '', 'message': ''})

        self.assertRaises(http.BadRequest, this.call, method='PUT', path=['post', topic, 'resolution'], content='closed')
        this.call(method='PUT', path=['post', topic, 'resolution'], content='resolved')
        self.assertEqual('resolved', volume['post'][topic]['resolution'])

    def test_ShiftReplies(self):
        volume = db.Volume('.', [Context, Post])
        this.volume = volume

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })
        topic = volume['post'].create({
            'context': context,
            'type': 'topic',
            'title': {},
            'message': {},
            })
        self.assertEqual(0, volume['post'][topic]['replies'])

        volume['post'].create({
            'context': context,
            'topic': topic,
            'type': 'post',
            'title': {},
            'message': {},
            })
        self.assertEqual(1, volume['post'][topic]['replies'])

        volume['post'].create({
            'context': context,
            'topic': topic,
            'type': 'post',
            'title': {},
            'message': {},
            })
        self.assertEqual(2, volume['post'][topic]['replies'])

    def test_ShiftRepliesOnDeletes(self):
        volume = self.start_master(auth=RootAuth())

        context = volume['context'].create({
            'type': ['activity', 'book', 'talks', 'project'],
            'title': {},
            'summary': {},
            'description': {},
            })
        topic = this.call(method='POST', path=['post'], content={'context': context, 'type': 'topic', 'title': '', 'message': ''})

        post1 = this.call(method='POST', path=['post'], content={'context': context, 'topic': topic, 'type': 'post', 'title': '', 'message': ''})
        post2 = this.call(method='POST', path=['post'], content={'context': context, 'topic': topic, 'type': 'post', 'title': '', 'message': ''})
        self.assertEqual(2, volume['post'][topic]['replies'])

        this.call(method='DELETE', path=['post', post1])
        self.assertEqual(1, volume['post'][topic]['replies'])

        this.call(method='DELETE', path=['post', post2])
        self.assertEqual(0, volume['post'][topic]['replies'])


if __name__ == '__main__':
    tests.main()
