# Copyright (C) 2012-2014 Aleksey Lim
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
import json
import bisect
import hashlib
import logging
import gettext
import mimetypes
from copy import deepcopy
from os.path import join

from sugar_network import db, toolkit
from sugar_network.model import context as _context, user as _user
from sugar_network.model import ICON_SIZE, LOGO_SIZE
from sugar_network.node import obs
from sugar_network.node.auth import Principal
from sugar_network.toolkit.router import ACL, File, Request, Response
from sugar_network.toolkit.coroutine import Queue, this
from sugar_network.toolkit.spec import EMPTY_LICENSE, ensure_version
from sugar_network.toolkit.spec import parse_requires, parse_version
from sugar_network.toolkit.bundle import Bundle
from sugar_network.toolkit import sat, http, i18n, ranges, packets
from sugar_network.toolkit import svg_to_png, enforce


BATCH_SUFFIX = '.meta'

_logger = logging.getLogger('node.model')
_presolve_queue = None


class User(_user.User):

    def created(self):
        self.posts['guid'] = str(hashlib.sha1(self['pubkey']).hexdigest())


class _ReleaseValue(dict):

    guid = None


class _Release(object):

    _package_subcast = db.Dict(db.List())

    def typecast(self, value):
        if isinstance(value, _ReleaseValue):
            return value.guid, value
        doc = this.volume['context'][this.request.guid]
        if 'package' in doc['type']:
            value = _ReleaseValue(self._package_subcast.typecast(value))
            value.guid = this.request.key
            _resolve_package_alias(doc, value)
            return value
        bundle = this.volume.blobs.post(value, this.request.content_type)
        __, value = load_bundle(bundle, context=this.request.guid)
        return value.guid, value

    def encode(self, value):
        return []

    def teardown(self, value):
        if 'bundles' in value:
            for bundle in value['bundles'].values():
                this.volume.blobs.delete(bundle['blob'])
        # TODO Delete presolved files


class Context(_context.Context):

    @db.stored_property(db.Aggregated, subtype=_Release(),
            acl=ACL.READ | ACL.INSERT | ACL.REMOVE | ACL.REPLACE | ACL.LOCAL)
    def releases(self, value):
        return value

    def created(self):
        _context.Context.created(self)
        self._invalidate_solutions()

    def updated(self):
        _context.Context.updated(self)
        self._invalidate_solutions()

    def _invalidate_solutions(self):
        if self['releases'] and \
                [i for i in ('state', 'releases', 'dependencies')
                    if i in self.posts and self.posts[i] != self.orig(i)]:
            this.broadcast({
                'event': 'release',
                'seqno': this.volume.release_seqno.next(),
                })


class Volume(db.Volume):

    def __init__(self, root, resources, **kwargs):
        db.Volume.__init__(self, root, resources, **kwargs)
        self.release_seqno = toolkit.Seqno(join(root, 'var', 'seqno-release'))

    def close(self):
        db.Volume.close(self)
        self.release_seqno.commit()


def diff_volume(r, exclude=None, files=None, blobs=True, one_way=False):
    volume = this.volume
    if exclude:
        include = deepcopy(r)
        ranges.exclude(include, exclude)
    else:
        include = r
    last_seqno = None
    found = False

    try:
        for resource, directory in volume.items():
            if one_way and directory.resource.one_way:
                continue
            yield {'resource': resource}
            for doc in directory.diff(r):
                patch = doc.diff(include)
                if patch:
                    yield {'guid': doc.guid, 'patch': patch}
                    found = True
                last_seqno = max(last_seqno, doc['seqno'])
        if blobs:
            for blob in volume.blobs.diff(include):
                seqno = int(blob.meta.pop('x-seqno'))
                yield blob
                found = True
                last_seqno = max(last_seqno, seqno)
        for dirpath in files or []:
            for blob in volume.blobs.diff(include, dirpath):
                seqno = int(blob.meta.pop('x-seqno'))
                yield blob
                found = True
                last_seqno = max(last_seqno, seqno)
    except StopIteration:
        pass

    if found:
        commit_r = include if exclude else deepcopy(r)
        ranges.exclude(commit_r, last_seqno + 1, None)
        ranges.exclude(r, None, last_seqno)
        yield {'commit': commit_r}


def patch_volume(records, shift_seqno=True):
    volume = this.volume
    directory = None
    committed = []
    seqno = None if shift_seqno else False

    for record in records:
        if isinstance(record, File):
            if seqno is None:
                seqno = volume.seqno.next()
            volume.blobs.patch(record, seqno or 0)
            continue
        resource = record.get('resource')
        if resource:
            directory = volume[resource]
            continue
        guid = record.get('guid')
        if guid is not None:
            seqno = directory.patch(guid, record['patch'], seqno)
            continue
        commit = record.get('commit')
        if commit is not None:
            ranges.include(committed, commit)
            continue
        raise http.BadRequest('Malformed patch')

    return seqno, committed


def diff_resource(in_r):
    request = this.request
    enforce(request.resource != 'user', http.BadRequest,
            'Not allowed for User resource')
    doc = this.volume[request.resource][request.guid]
    enforce(doc.exists, http.NotFound, 'Resource not found')

    out_r = []
    if in_r is None:
        in_r = [[1, None]]
    patch = doc.diff(in_r, out_r)
    if not patch:
        return packets.encode([], compresslevel=0)
    blobs = []

    def add_blob(blob):
        if not isinstance(blob, File):
            return
        seqno = int(blob.meta['x-seqno'])
        ranges.include(out_r, seqno, seqno)
        blobs.append(blob)

    for prop, meta in patch.items():
        prop = doc.metadata[prop]
        value = prop.reprcast(meta['value'])
        if isinstance(prop, db.Aggregated):
            for __, aggvalue in value:
                add_blob(aggvalue)
        else:
            add_blob(value)

    return packets.encode(blobs, patch=patch, ranges=out_r, compresslevel=0)


def apply_batch(path):
    with file(path + BATCH_SUFFIX) as f:
        meta = json.load(f)
    principal = Principal(meta['principal'])
    principal.cap_create_with_guid = True
    only_nums = meta.get('failed')
    guid_map = meta.setdefault('guid_map', {})
    failed = meta['failed'] = []
    volume = this.volume

    def map_guid(remote_guid):
        local_guid = guid_map.get(remote_guid)
        if not local_guid:
            if volume[request.resource][remote_guid].exists:
                return remote_guid
            local_guid = guid_map[remote_guid] = toolkit.uuid()
        return local_guid

    with file(path, 'rb') as batch:
        num = 0
        for record in packets.decode(batch):
            num += 1
            if only_nums and not ranges.contains(only_nums, num):
                continue
            if isinstance(record, File):
                request = Request(**record.meta.pop('op'))
                request.content = record
            else:
                request = Request(**record['op'])
                props = record['content']
                keys = record.get('keys') or []
                enforce('guid' not in props or 'guid' in keys,
                        http.BadRequest, 'Guid values is not mapped')
                for key in keys:
                    enforce(key in props, http.BadRequest,
                            'No mapped property value')
                    props[key] = map_guid(props[key])
                request.content = props
            if request.guid and \
                    not volume[request.resource][request.guid].exists:
                request.guid = map_guid(request.guid)
            request.principal = principal
            try:
                this.call(request, Response())
            except Exception:
                _logger.exception('Failed to apply %r', request)
                ranges.include(failed, num, num)

    if failed:
        with toolkit.new_file(path + BATCH_SUFFIX) as f:
            json.dump(meta, f)
    else:
        os.unlink(path + BATCH_SUFFIX)
        os.unlink(path)


def solve(volume, top_context, command=None, lsb_id=None, lsb_release=None,
        stability=None, requires=None):
    top_context = volume['context'][top_context]
    if stability is None:
        stability = ['stable']
    if isinstance(stability, basestring):
        stability = [stability]
    top_cond = []
    top_requires = {}
    if isinstance(requires, basestring):
        top_requires.update(parse_requires(requires))
    elif requires:
        for i in requires:
            top_requires.update(parse_requires(i))
    if top_context['dependencies']:
        top_requires.update(parse_requires(top_context['dependencies']))
    if top_context.guid in top_requires:
        top_cond = top_requires.pop(top_context.guid)

    lsb_distro = '-'.join([lsb_id, lsb_release]) if lsb_release else None
    varset = [None]
    context_clauses = {}
    clauses = []

    _logger.debug('Solve %r lsb_id=%r lsb_release=%r stability=%r requires=%r',
            top_context.guid, lsb_id, lsb_release, stability, top_requires)

    def rate_release(digest, release):
        return [command in release.get('commands', []),
                _STABILITY_RATES.get(release['stability']) or 0,
                release['version'],
                digest,
                ]

    def add_deps(v_usage, deps):
        for dep, cond in deps.items():
            dep_clause = [-v_usage]
            for v_release in add_context(dep):
                if ensure_version(varset[v_release][1]['version'], cond):
                    dep_clause.append(v_release)
            clauses.append(dep_clause)

    def add_context(context):
        if context in context_clauses:
            return context_clauses[context]
        context = volume['context'][context]
        enforce(context.available, http.NotFound, 'Context not found')
        releases = context['releases']
        clause = []

        if 'package' in context['type']:
            pkg_lst = None
            pkg_ver = []
            pkg = releases.get('resolves', {}).get(lsb_distro)
            if pkg:
                pkg_ver = pkg['version']
                pkg_lst = pkg['packages']
            else:
                alias = releases.get(lsb_id) or releases.get('*')
                if alias:
                    alias = alias['value']
                    pkg_lst = alias.get('binary', []) + alias.get('devel', [])
            if pkg_lst:
                clause.append(len(varset))
                varset.append((
                    context.guid,
                    {'version': pkg_ver, 'packages': pkg_lst},
                    ))
        else:
            candidates = []
            for digest, release in releases.items():
                if 'value' not in release:
                    continue
                release = release['value']
                if release['stability'] not in stability or \
                        context.guid == top_context.guid and \
                            not ensure_version(release['version'], top_cond):
                    continue
                bisect.insort(candidates, rate_release(digest, release))
            for release in reversed(candidates):
                digest = release[-1]
                release = releases[digest]['value']
                release_info = {
                        'title': i18n.decode(context['title'],
                            this.request.accept_language),
                        'version': release['version'],
                        'blob': digest,
                        }
                blob = volume.blobs.get(digest)
                if blob is not None:
                    release_info['size'] = blob.size
                    release_info['content-type'] = blob.meta['content-type']
                unpack_size = release['bundles']['*-*'].get('unpack_size')
                if unpack_size is not None:
                    release_info['unpack_size'] = unpack_size
                requires = release.get('requires') or {}
                if top_requires and context.guid == top_context.guid:
                    requires.update(top_requires)
                if context.guid == top_context.guid and 'commands' in release:
                    cmd = release['commands'].get(command)
                    if cmd is None:
                        cmd_name, cmd = release['commands'].items()[0]
                    else:
                        cmd_name = command
                    release_info['command'] = (cmd_name, cmd['exec'])
                    requires.update(cmd.get('requires') or {})
                v_release = len(varset)
                varset.append((context.guid, release_info))
                clause.append(v_release)
                add_deps(v_release, requires)

        if clause:
            context_clauses[context.guid] = clause
        else:
            _logger.trace('No candidates for %r', context.guid)
        return clause

    top_clause = add_context(top_context.guid)
    if not top_clause:
        _logger.debug('No versions for %r', top_context.guid)
        return None
    result = sat.solve(clauses + [top_clause], context_clauses)
    if not result:
        _logger.debug('Failed to solve %r', top_context.guid)
        return None
    if not top_context.guid in result:
        _logger.debug('No top versions for %r', top_context.guid)
        return None
    solution = dict([varset[i] for i in result.values()])

    _logger.debug('Solution for %r: %r', top_context.guid, solution)

    return solution


def presolve(presolve_path):
    global _presolve_queue
    _presolve_queue = Queue()

    for repo_name, pkgs in _presolve_queue:
        obs.presolve(repo_name, pkgs, presolve_path)


def load_bundle(blob, context=None, initial=False, extra_deps=None):
    context_type = None
    context_meta = None
    release_notes = None
    version = None
    release = _ReleaseValue()
    release.guid = blob.digest

    try:
        bundle = Bundle(blob.path, mime_type='application/zip')
    except Exception:
        context_type = 'book'
        if not context:
            context = this.request['context']
        version = this.request['version']
        if 'license' in this.request:
            release['license'] = this.request['license']
            if isinstance(release['license'], basestring):
                release['license'] = [release['license']]
        release['stability'] = 'stable'
        release['bundles'] = {
                '*-*': {
                    'blob': blob.digest,
                    },
                }
    else:
        context_type = 'activity'
        unpack_size = 0

        with bundle:
            changelog = join(bundle.rootdir, 'CHANGELOG')
            for arcname in bundle.get_names():
                if changelog and arcname == changelog:
                    with bundle.extractfile(changelog) as f:
                        release_notes = f.read()
                    changelog = None
                unpack_size += bundle.getmember(arcname).size
            spec = bundle.get_spec()
            context_meta = _load_context_metadata(bundle, spec)

        if not context:
            context = spec['context']
        else:
            enforce(context == spec['context'],
                    http.BadRequest, 'Wrong context')
        if extra_deps:
            spec.requires.update(parse_requires(extra_deps))

        version = spec['version']
        release['stability'] = spec['stability']
        if spec['license'] is not EMPTY_LICENSE:
            release['license'] = spec['license']
        release['commands'] = spec.commands
        release['requires'] = spec.requires
        release['bundles'] = {
                '*-*': {
                    'blob': blob.digest,
                    'unpack_size': unpack_size,
                    },
                }
        blob.meta['content-type'] = 'application/vnd.olpc-sugar'

    enforce(context, http.BadRequest, 'Context is not specified')
    enforce(version, http.BadRequest, 'Version is not specified')
    release['version'] = parse_version(version)

    doc = this.volume['context'][context]
    if initial and not doc.exists:
        enforce(context_meta, http.BadRequest, 'No way to initate context')
        context_meta['guid'] = context
        context_meta['type'] = [context_type]
        with this.principal as principal:
            principal.cap_create_with_guid = True
            this.call(method='POST', path=['context'], content=context_meta,
                    principal=principal)
    else:
        enforce(doc.available, http.NotFound, 'No context')
        enforce(context_type in doc['type'],
                http.BadRequest, 'Inappropriate bundle type')

    if 'license' not in release:
        releases = doc['releases'].values()
        enforce(releases, http.BadRequest, 'License is not specified')
        recent = max(releases, key=lambda x: x.get('value', {}).get('release'))
        enforce(recent, http.BadRequest, 'License is not specified')
        release['license'] = recent['value']['license']

    _logger.debug('Load %r release: %r', context, release)

    if this.principal in doc['author']:
        patch = doc.format_patch(context_meta)
        if patch:
            this.call(method='PUT', path=['context', context], content=patch,
                    principal=this.principal)
            doc.posts.update(patch)
        # TRANS: Release notes title
        title = i18n._('%(name)s %(version)s release')
    else:
        # TRANS: 3rd party release notes title
        title = i18n._('%(name)s %(version)s third-party release')
    release['announce'] = this.call(method='POST', path=['post'],
            content={
                'context': context,
                'type': 'notification',
                'title': i18n.encode(title,
                    name=doc['title'],
                    version=version,
                    ),
                'message': release_notes or '',
                },
            content_type='application/json', principal=this.principal)

    blob.meta['content-disposition'] = 'attachment; filename="%s-%s%s"' % (
            ''.join(i18n.decode(doc['title']).split()), version,
            mimetypes.guess_extension(blob.meta.get('content-type')) or '',
            )
    this.volume.blobs.update(blob.digest, blob.meta)

    return context, release


def generate_node_stats(volume):

    def calc_rating(**kwargs):
        rating = [0, 0]
        alldocs, __ = volume['post'].find(**kwargs)
        for post in alldocs:
            if post['vote']:
                rating[0] += 1
                rating[1] += post['vote']
        return rating

    alldocs, __ = volume['context'].find()
    for context in alldocs:
        rating = calc_rating(type='review', context=context.guid)
        volume['context'].update(context.guid, {'rating': rating})

    alldocs, __ = volume['post'].find(topic='')
    for topic in alldocs:
        rating = calc_rating(type='feedback', topic=topic.guid)
        volume['post'].update(topic.guid, {'rating': rating})


def _load_context_metadata(bundle, spec):
    result = {}
    for prop in ('homepage', 'mime_types'):
        if spec[prop]:
            result[prop] = spec[prop]
    result['guid'] = spec['context']

    try:
        from sugar_network.toolkit.sugar import color_svg

        icon_file = bundle.extractfile(join(bundle.rootdir, spec['icon']))
        svg = color_svg(icon_file.read(), result['guid'])
        blobs = this.volume.blobs

        result['artefact_icon'] = \
                blobs.post(svg, 'image/svg+xml').digest
        result['icon'] = \
                blobs.post(svg_to_png(svg, ICON_SIZE), 'image/png').digest
        result['logo'] = \
                blobs.post(svg_to_png(svg, LOGO_SIZE), 'image/png').digest

        icon_file.close()
    except Exception:
        _logger.exception('Failed to load icon')

    msgids = {}
    for prop, confname in [
            ('title', 'name'),
            ('summary', 'summary'),
            ('description', 'description'),
            ]:
        if spec[confname]:
            msgids[prop] = spec[confname]
            result[prop] = {'en': spec[confname]}
    with toolkit.mkdtemp() as tmpdir:
        for path in bundle.get_names():
            if not path.endswith('.mo'):
                continue
            mo_path = path.strip(os.sep).split(os.sep)
            if len(mo_path) != 5 or mo_path[1] != 'locale':
                continue
            lang = mo_path[2]
            bundle.extract(path, tmpdir)
            try:
                translation = gettext.translation(spec['context'],
                        join(tmpdir, *mo_path[:2]), [lang])
                for prop, value in msgids.items():
                    msgstr = translation.gettext(value).decode('utf8')
                    if lang == 'en' or msgstr != value:
                        result[prop][lang] = msgstr
            except Exception:
                _logger.exception('Gettext failed to read %r', mo_path[-1])

    return result


def _resolve_package_alias(doc, value):
    enforce(value.get('binary'), http.BadRequest, 'No binary aliases')

    distro = this.request.key
    enforce(distro, http.BadRequest, 'No distro in path')
    if distro == '*':
        lsb_id = None
        lsb_release = None
    elif '-' in this.request.key:
        lsb_id, lsb_release = distro.split('-', 1)
    else:
        lsb_id = distro
        lsb_release = None
    releases = doc['releases']
    resolves = releases.get('resolves') or {}
    to_presolve = []

    for repo in obs.get_repos():
        if lsb_id and lsb_id != repo['lsb_id'] or \
                lsb_release and lsb_release != repo['lsb_release']:
            continue
        # Make sure there are no alias overrides
        if not lsb_id and repo['lsb_id'] in releases or \
                not lsb_release and repo['name'] in releases:
            continue
        pkgs = sum([value.get(i, []) for i in ('binary', 'devel')], [])
        version = None
        try:
            for arch in repo['arches']:
                version = obs.resolve(repo['name'], arch, pkgs)['version']
        except Exception, error:
            _logger.warning('Failed to resolve %r on %s',
                    pkgs, repo['name'])
            resolve = {'status': str(error)}
        else:
            to_presolve.append((repo['name'], pkgs))
            resolve = {
                    'version': parse_version(version),
                    'packages': pkgs,
                    'status': 'success',
                    }
        resolves.setdefault(repo['name'], {}).update(resolve)

    if to_presolve and _presolve_queue is not None:
        _presolve_queue.put(to_presolve)
    doc.post('releases', {'resolves': resolves})


_STABILITY_RATES = {
        'insecure': 0,
        'buggy': 1,
        'developer': 2,
        'testing': 3,
        'stable': 4,
        }