# Copyright (C) 2011-2012, Aleksey Lim
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

import json
import logging
from os.path import isabs
from gettext import gettext as _

from zeroinstall.injector import model

import sweets_recipe
from local_document import activities, util
from zerosugar.config import config


_logger = logging.getLogger('zerosugar')


def read(context):
    feed = _Feed(context)
    feed_content = {}

    for path in activities.checkins(context):
        try:
            spec = sweets_recipe.Spec(root=path)
        except Exception, error:
            util.exception(_logger, _('Failed to read %r spec file: %s'),
                    path, error)
            continue

        feed_content[spec['version']] = {
                '*-*': {
                    'guid': spec.root,
                    'stability': 'stable',
                    'commands': {
                        'activity': {
                            'exec': spec['Activity', 'exec'],
                            },
                        },
                    },
                }

    if not feed_content:
        try:
            with config.client.Context(context).blobs['feed'] as f:
                feed_content = json.load(f)
        except Exception:
            util.exception(_logger,
                    _('Failed to fetch feed for "%s" context'), context)
            return None

    if not feed_content:
        _logger.warning(_('No feed for "%s" context'), context)
        return None

    for version, version_data in feed_content.items():
        for arch, impl_data in version_data.items():
            impl_id = impl_data['guid']

            impl = model.ZeroInstallImplementation(feed, impl_id, None)
            impl.version = model.parse_version(version)
            impl.released = 0
            impl.arch = arch
            impl.upstream_stability = \
                    model.stability_levels[impl_data['stability']]
            impl.requires.extend(_read_requires(impl_data.get('requires')))

            if isabs(impl_id):
                impl.local_path = impl_id
            else:
                impl.add_download_source(impl_id,
                        impl_data['size'], impl_data['extract'])

            for name, command in impl_data['commands'].items():
                impl.commands[name] = _Command(name, command)

            for name, insert, mode in impl_data.get('bindings') or []:
                binding = model.EnvironmentBinding(name, insert, mode=mode)
                impl.bindings.append(binding)

            feed.implementations[impl_id] = impl

    return feed


class _Feed(model.ZeroInstallFeed):
    # pylint: disable-msg=E0202

    def __init__(self, context):
        self.context = context
        self.local_path = None
        self.implementations = {}
        self.last_modified = None
        self.feeds = []
        self.metadata = []
        self.last_checked = None
        self._package_implementations = []

    @property
    def url(self):
        return self.context

    @property
    def feed_for(self):
        return set([self.context])

    @property
    def name(self):
        return self.context

    @property
    def summaries(self):
        # TODO i18n
        return {}

    @property
    def first_summary(self):
        return self.context

    @property
    def descriptions(self):
        # TODO i18n
        return {}

    @property
    def first_description(self):
        return self.context


class _Dependency(model.InterfaceDependency):

    def __init__(self, guid, data):
        self._importance = data.get('importance', model.Dependency.Essential)
        self._metadata = {}
        self.qdom = None
        self.interface = guid
        self.restrictions = []
        self.bindings = []

        for not_before, before in data.get('restrictions') or []:
            restriction = model.VersionRangeRestriction(
                    not_before=not_before and model.parse_version(not_before),
                    before=before and model.parse_version(before))
            self.restrictions.append(restriction)

    @property
    def context(self):
        return self.interface

    @property
    def metadata(self):
        return self._metadata

    @property
    def importance(self):
        return self._importance

    def get_required_commands(self):
        return []

    @property
    def command(self):
        pass


class _Command(model.Command):

    def __init__(self, name, data):
        self.qdom = None
        self.name = name
        self._path = data['exec']
        self._requires = _read_requires(data.get('requires'))

    @property
    def path(self):
        return self._path

    @property
    def requires(self):
        return self._requires

    def get_runner(self):
        pass

    def __str__(self):
        return ''

    @property
    def bindings(self):
        return []


def _read_requires(data):
    result = []
    for guid, dep_data in (data or {}).items():
        result.append(_Dependency(guid, dep_data))
    return result
