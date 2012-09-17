# Copyright (C) 2010-2012 Aleksey Lim
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

# pylint: disable-msg=W0212

import os
import sys
import json
import signal
import shutil
import logging
import threading
from os.path import join, exists, basename, isabs

from zeroinstall.injector import model
from zeroinstall.injector.requirements import Requirements

from sweets_recipe import Spec
from sugar_network.zerosugar import solver
from sugar_network.zerosugar.config import config
from sugar_network import local
from sugar_network.toolkit import sugar
from active_toolkit import coroutine, util, enforce


_logger = logging.getLogger('zerosugar.injector')
_pipe = None


class Pipe(object):

    def __init__(self, pid, fd):
        self._pid = pid
        self._file = os.fdopen(fd)
        self._stat = {}

    def fileno(self):
        return None if self._file is None else self._file.fileno()

    def read(self):
        if self._file is None:
            return None

        event = self._file.readline()
        if event:
            event = json.loads(event)
            phase = event.pop('phase')
            if not self._process_inernals(phase, event):
                return phase, event
            else:
                return None, None

        return self._finalize()

    def __iter__(self):
        if self._file is None:
            return

        try:
            while True:
                coroutine.select([self._file.fileno()], [], [])
                event = self._file.readline()
                if not event:
                    break

                event = json.loads(event)
                phase = event.pop('phase')
                if not self._process_inernals(phase, event):
                    yield phase, event
                if phase == 'exec':
                    break

            fin = self._finalize()
            if fin is not None:
                yield fin
        finally:
            self._finalize()

    def _process_inernals(self, phase, props):
        if phase == 'stat':
            self._stat.update(props)
            return True
        elif phase == 'failure':
            props.update(self._stat)

    def _finalize(self):
        if self._file is None:
            return

        try:
            __, status = os.waitpid(self._pid, 0)
        except OSError:
            return None
        finally:
            self._file.close()
            self._file = None

        failure = _decode_exit_failure(status)
        if failure:
            self._stat['error'] = failure
            return 'failure', self._stat


def launch(mountpoint, context, command='activity', args=None):
    return _fork(_launch, mountpoint, context, command, args)


def checkin(mountpoint, context, command='activity'):
    return _fork(_checkin, mountpoint, context, command)


def _fork(callback, mountpoint, context, *args):
    fd_r, fd_w = os.pipe()

    pid = os.fork()
    if pid:
        os.close(fd_w)
        return Pipe(pid, fd_r)

    from sugar_network import IPCClient

    os.close(fd_r)
    global _pipe
    _pipe = fd_w

    def thread_func():
        log_path = _setup_logging(context)
        _progress('stat', log_path=log_path, mountpoint=mountpoint,
                context=context)

        config.clients = [IPCClient(mountpoint='~')]
        if mountpoint != '~':
            config.clients.append(IPCClient(mountpoint=mountpoint))

        try:
            callback(mountpoint, context, *args)
        except Exception, error:
            util.exception(_logger)
            _progress('failure', error=str(error))

    # Avoid a mess with current thread coroutines
    thread = threading.Thread(target=thread_func)
    thread.start()
    thread.join()

    os.close(fd_w)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)


def _launch(mountpoint, context, command, args):
    if args is None:
        args = []

    solution = _make(context, command)
    cmd = solution.commands[0]
    args = cmd.path.split() + args

    _logger.info('Executing %s: %s', solution.interface, args)
    _progress('exec')

    if command == 'activity':
        _activity_env(solution.top, os.environ)
    os.execvpe(args[0], args, os.environ)


def _checkin(mountpoint, context, command):
    solution = _make(context, command)

    checkedin = []
    try:
        for sel, __, __ in solution.walk():
            dst_path = util.unique_filename(
                    local.activity_dirs.value[0], basename(sel.local_path))
            checkedin.append(dst_path)
            _logger.info('Checkin implementation to %r', dst_path)
            util.cptree(sel.local_path, dst_path)
    except Exception:
        while checkedin:
            shutil.rmtree(checkedin.pop(), ignore_errors=True)
        raise


def _progress(phase, **kwargs):
    kwargs['phase'] = phase
    os.write(_pipe, json.dumps(kwargs))
    os.write(_pipe, '\n')


def _make(context, command):
    requirement = Requirements(context)
    requirement.command = command

    _progress('analyze', progress=-1)
    solution = solver.solve(requirement)
    enforce(solution.ready, solution.failure_reason)

    for sel, __, __ in solution.walk():
        if sel.local_path:
            continue

        enforce(sel.download_sources,
                'No sources to download implementation for %r context',
                sel.interface)

        # TODO Per download progress
        _progress('download', progress=-1)

        impl = sel.client.get(['implementation', sel.id, 'data'],
                cmd='get_blob')
        enforce(impl and 'path' in impl, 'Cannot download implementation')
        impl_path = impl['path']

        dl = sel.download_sources[0]
        if dl.extract is not None:
            impl_path = join(impl_path, dl.extract)
        sel.local_path = impl_path

    if not isabs(solution.top.id):
        _progress('stat', implementation=solution.top.id)

    return solution


def _activity_env(selection, environ):
    root = sugar.profile_path('data', selection.interface)

    for path in ['instance', 'data', 'tmp']:
        path = join(root, path)
        if not exists(path):
            os.makedirs(path)

    # TODO Any way to avoid loading spec file?
    spec = Spec(root=selection.local_path)

    environ['SUGAR_BUNDLE_PATH'] = selection.local_path
    environ['SUGAR_BUNDLE_ID'] = selection.feed.context
    environ['SUGAR_BUNDLE_NAME'] = spec['Activity', 'name']
    environ['SUGAR_BUNDLE_VERSION'] = model.format_version(selection.version)
    environ['SUGAR_ACTIVITY_ROOT'] = root
    environ['PATH'] = '%s:%s' % \
            (join(selection.local_path, 'bin'), environ['PATH'])
    environ['PYTHONPATH'] = '%s:%s' % \
            (selection.local_path, environ['PYTHONPATH'])
    environ['SUGAR_LOCALEDIR'] = join(selection.local_path, 'locale')

    os.chdir(selection.local_path)


def _setup_logging(context):
    log_dir = sugar.profile_path('logs')
    if not exists(log_dir):
        os.makedirs(log_dir)
    path = util.unique_filename(log_dir, context + '.log')

    def stdfd(stream):
        if hasattr(stream, 'fileno'):
            return stream.fileno()
        else:
            # Sugar Shell wraps std streams
            return stream._stream.fileno()

    logfile = file(path, 'a+')
    os.dup2(logfile.fileno(), stdfd(sys.stdout))
    os.dup2(logfile.fileno(), stdfd(sys.stderr))
    logfile.close()

    debug = sugar.logger_level()
    if not debug:
        level = logging.WARNING
    elif debug == 1:
        level = logging.INFO
    else:
        level = logging.DEBUG

    root_logger = logging.getLogger()
    for handler in root_logger.handlers:
        root_logger.removeHandler(handler)
    logging.basicConfig(level=level,
            format='%(asctime)s %(levelname)s %(name)s: %(message)s')

    return path


def _decode_exit_failure(status):
    failure = None
    if os.WIFEXITED(status):
        status = os.WEXITSTATUS(status)
        if status:
            failure = 'Exited with status %s' % status
    elif os.WIFSIGNALED(status):
        signum = os.WTERMSIG(status)
        if signum not in (signal.SIGINT, signal.SIGKILL, signal.SIGTERM):
            failure = 'Terminated by signal %s' % signum
    else:
        signum = os.WTERMSIG(status)
        failure = 'Undefined status with signal %s' % signum
    return failure
