"""Durable local progression storage and finished-match bookkeeping."""
import copy
from dataclasses import dataclass
import fcntl
import json
import math
import os
import tempfile
import threading
import time
import uuid

SWITCH = 'CRUCIBLE_BACKEND_PROGRESS'
DIRECTORY_ENV = 'CRUCIBLE_PROGRESS_DIR'
ACCOUNT_ENV = 'CRUCIBLE_PROGRESS_ACCOUNT'
PER_USER_ENV = 'CRUCIBLE_PROGRESS_PER_USER'
DEFAULT_ACCOUNT = 'local'
VERSION = 1
SPOOL_NAME = 'spool.jsonl'
APPLIED_NAME = 'applied.jsonl'    # who each match was credited to, first time
LOCK_WAIT_SECONDS = 2.0
SPOOL_RETRY_SECONDS = 1.0

# ---------------------------------------------------------------------------
# Project-authored experience rules.
# ---------------------------------------------------------------------------
STAND_IN_AWARDS = {
    'play_time': {'time_points': ((0, 0.5),),   # (from second, xp per second)
                   'max_seconds': 1800},         # at most 900 xp from time
    'win_bonus': 500,                              # a win only; a draw or loss earns none
}
# Each level needs a further 1000 times its number in experience.
LEVEL_XP_PER_LEVEL = 1000
MAX_LEVELS_PER_REQUEST = 200
MAX_LEVEL = 100000

RESULTS = ('win', 'loss', 'draw')
MAX_DURATION_SECONDS = 24 * 3600

_LOCK = threading.RLock()
_LOGGED = set()
_SPOOL_OFFSETS = {}       # spool path -> bytes already applied by this process
# Set when a document was just kept aside as unreadable: the spool is the durable
# record of the experience, so it is replayed from its start (an id already in a
# document is skipped) and the fresh document gets its matches back at once.
_REPLAY = [False]


class StoreError(Exception):
    """The store cannot answer or record; the caller falls back."""


def enabled():
    return os.environ.get(SWITCH, '') not in ('', '0')


def log(message, key=None):
    """Log to stderr; a line with a key is written once per process."""
    if key is not None:
        if key in _LOGGED:
            return
        _LOGGED.add(key)
    import sys
    print('progress: ' + message, file=sys.stderr, flush=True)


def directory():
    return (os.environ.get(DIRECTORY_ENV)
            or os.path.join(os.path.expanduser('~'), '.local', 'share',
                            'crucible-revival', 'progress'))


def sanitize_account(name):
    text = ''.join(c for c in str(name) if c.isalnum() or c in '_-')[:48]
    return text or DEFAULT_ACCOUNT


def active_account():
    """Return the local account used by the progression store."""
    return sanitize_account(os.environ.get(ACCOUNT_ENV) or DEFAULT_ACCOUNT)


def account_for_user(user_id):
    """The account a finished match's user id is credited to: the active
    account, or with CRUCIBLE_PROGRESS_PER_USER=1 its own `u<id>` account."""
    if os.environ.get(PER_USER_ENV, '') not in ('', '0') and user_id:
        return sanitize_account('u%d' % user_id)
    return active_account()


def startup_lines():
    return ['progress store ON: %s (account %s)' % (directory(), active_account()),
            'local experience rules: %s; level L needs %d x L to reach L+1'
            % (json.dumps(STAND_IN_AWARDS), LEVEL_XP_PER_LEVEL)]


# ---------------------------------------------------------------------------
# The level curve (STAND-IN)
# ---------------------------------------------------------------------------
def level_increment(level):
    """Experience that level `level` needs to reach the next one."""
    return level * LEVEL_XP_PER_LEVEL


def _cumulative(level):
    """Total experience at which `level` is reached."""
    return LEVEL_XP_PER_LEVEL * (level - 1) * level // 2


def level_progress(total):
    """(level, fraction of the level done) for a hunter's experience total."""
    total = max(0, int(total))
    n = total // LEVEL_XP_PER_LEVEL
    level = max(1, min((1 + math.isqrt(1 + 8 * n)) // 2, MAX_LEVEL))
    while level > 1 and _cumulative(level) > total:
        level -= 1
    while level < MAX_LEVEL and _cumulative(level + 1) <= total:
        level += 1
    fraction = (total - _cumulative(level)) / level_increment(level)
    return level, min(max(fraction, 0.0), 0.999999)


def match_awards(result, duration):
    """Return the locally defined awards for a finished match."""
    awards = []
    table = STAND_IN_AWARDS['play_time']
    seconds = max(0.0, min(float(duration), table['max_seconds']))
    gained = 0.0
    points = sorted(table['time_points'])
    for index, (start, rate) in enumerate(points):
        end = points[index + 1][0] if index + 1 < len(points) else table['max_seconds']
        gained += max(0.0, min(seconds, end) - start) * rate
    if int(gained) > 0:
        awards.append(('play_time', int(gained)))
    if result == 'win':
        awards.append(('win_bonus', int(STAND_IN_AWARDS['win_bonus'])))
    return awards


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
def new_document(account):
    return {'version': VERSION, 'account': account, 'user_ids': [],
            'profile': {}, 'hunters': {}, 'purchases': [],
            'entitlement_visibility': {}, 'xp': {'account_total': 0},
            'matches': []}


def _fill(document, account):
    """Add the sections a document lacks, in place, and put an empty value in
    memory where a section has the wrong type. Returns the names of the damaged
    sections (empty when the document is sound)."""
    damaged = []
    for key, default in new_document(account).items():
        if not isinstance(document.setdefault(key, default), type(default)):
            document[key] = default
            damaged.append(key)
    xp = document['xp']
    if type(xp.setdefault('account_total', 0)) is not int:
        xp['account_total'] = 0
        damaged.append('xp.account_total')
    if not all(isinstance(m, dict) for m in document['matches']):
        document['matches'] = [m for m in document['matches'] if isinstance(m, dict)]
        damaged.append('matches')
    if not all(isinstance(h, dict) for h in document['hunters'].values()):
        document['hunters'] = {k: h for k, h in document['hunters'].items()
                               if isinstance(h, dict)}
        damaged.append('hunters')
    return damaged


def _atomic_write(path, data):
    folder = os.path.dirname(path)
    handle, temporary = tempfile.mkstemp(prefix='.tmp-', suffix='.json', dir=folder)
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)       # our own temporary file only
        except OSError:
            pass
        raise
    try:
        descriptor = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _keep_unreadable(path, reason):
    """Rename an unparsable document aside; returns the new name."""
    stamp = time.strftime('%Y%m%dT%H%M%S')
    for number in range(1000):
        kept = '%s.corrupt-%s%s' % (path, stamp, '' if not number else '-%d' % number)
        if not os.path.exists(kept):
            os.rename(path, kept)
            _REPLAY[0] = True
            log('%s cannot be parsed (%s); kept as %s and starting from an empty '
                'document' % (path, reason, kept), key=('corrupt', path))
            return kept
    raise StoreError('no free name to keep the unreadable %s' % path)


def append_line(path, line):
    """Append one text line (with its newline) to `path` whole, or raise OSError.

    A short write is continued until the whole line is down. If an earlier
    writer left a damaged tail (the file does not end with a newline: a full
    disk, a crash), a newline is written first, in the same write, so the new
    line is never glued to it; the reader skips the damaged line."""
    data = line.encode('utf-8')
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(descriptor).st_size
        if size and os.pread(descriptor, 1, size - 1) != b'\n':
            data = b'\n' + data
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError('a write returned %d' % written)
            view = view[written:]
    finally:
        os.close(descriptor)


class Store:
    def __init__(self, root=None):
        self.root = root or directory()

    # -- paths
    def path(self, account):
        return os.path.join(self.root, sanitize_account(account) + '.json')

    def spool_path(self):
        return os.path.join(self.root, SPOOL_NAME)

    def index_path(self):
        return os.path.join(self.root, APPLIED_NAME)

    def _ensure_root(self):
        try:
            os.makedirs(self.root, exist_ok=True)
        except OSError as error:
            raise StoreError('cannot create %s: %s' % (self.root, error))

    # -- locking
    class _Locked:
        """The process lock and the directory's file lock, each waited for at
        most LOCK_WAIT_SECONDS: a hung holder must not hang the menu's requests."""

        def __init__(self, store):
            self.store = store

        def __enter__(self):
            deadline = time.monotonic() + LOCK_WAIT_SECONDS
            if not _LOCK.acquire(timeout=LOCK_WAIT_SECONDS):
                raise StoreError('the progress store is busy (process lock)')
            self.file = None
            try:
                self.store._ensure_root()
                self.file = open(os.path.join(self.store.root, '.lock'), 'a')
                while True:
                    try:
                        fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise StoreError('the progress store is busy (file lock held '
                                             'longer than %g s)' % LOCK_WAIT_SECONDS)
                        time.sleep(0.01)
            except OSError as error:
                self._release()
                raise StoreError('cannot lock %s: %s' % (self.store.root, error))
            except BaseException:
                self._release()
                raise

        def _release(self):
            try:
                if self.file is not None:
                    self.file.close()        # closing drops the flock
            finally:
                _LOCK.release()

        def __exit__(self, *exc):
            self._release()

    def locked(self):
        return Store._Locked(self)

    # -- documents
    def _load(self, account):
        """(document, writable). Call with the lock held. A document that cannot
        be parsed is kept aside and replaced by an empty one. A document that
        parses but is damaged (a section of the wrong type) or newer than this
        code is served read-only: the sections that are valid answer, the rest
        are empty in memory, and nothing is ever written to it."""
        path = self.path(account)
        try:
            with open(path, 'rb') as stream:
                raw = stream.read()
        except FileNotFoundError:
            return new_document(account), True
        except OSError as error:
            raise StoreError('cannot read %s: %s' % (path, error))
        try:
            document = json.loads(raw.decode('utf-8'))
            if not isinstance(document, dict):
                raise ValueError('not an object')
            version = document.get('version', 0)
            if type(version) is not int or version < 0:
                raise ValueError('bad version')
        except (ValueError, UnicodeDecodeError) as error:
            try:
                _keep_unreadable(path, error)
            except OSError as problem:
                raise StoreError('cannot keep the unreadable %s: %s' % (path, problem))
            return new_document(account), True
        damaged = _fill(document, account)
        if version > VERSION:
            log('%s was written by a newer version (%d > %d); it is read-only here'
                % (path, version, VERSION), key=('newer', path))
            return document, False
        if damaged:
            log('%s has a section of the wrong type (%s); it is read-only here and the '
                'rest of it is served' % (path, ', '.join(damaged)), key=('damaged', path))
            return document, False
        document['version'] = VERSION
        return document, True

    def _save(self, account, document):
        data = json.dumps(document, indent=1, sort_keys=True).encode('utf-8')
        try:
            _atomic_write(self.path(account), data)
        except OSError as error:
            raise StoreError('cannot write %s: %s' % (self.path(account), error))

    def transaction(self, account, change=None, *, write=True):
        """Apply the spool, run `change(document)` on `account`'s document and
        save it when it differs. Returns the document (a copy of what is saved).
        `write=False` only reads (the spool is still applied). Raises StoreError
        when the document is read-only and a change was asked for, or when the
        store is busy longer than LOCK_WAIT_SECONDS."""
        with self.locked():
            self._apply_spool()
            document, writable = self._load(account)
            if _REPLAY[0]:
                # The document was just kept aside: rebuild what the spool knows.
                self._replay()
                document, writable = self._load(account)
            if change is None:
                return copy.deepcopy(document)
            if not writable:
                raise StoreError('%s is read-only' % self.path(account))
            before = json.dumps(document, sort_keys=True)
            change(document)
            if write and json.dumps(document, sort_keys=True) != before:
                self._save(account, document)
            return copy.deepcopy(document)

    def read(self, account):
        return self.transaction(account)

    # -- the spool
    def _replay(self):
        _REPLAY[0] = False
        _SPOOL_OFFSETS[self.spool_path()] = 0
        self._apply_spool(rebuilt=True)

    def _read_index(self):
        """{match id: account or None}: who each match was credited to when it was
        first applied. One append-only file for every account, so changing the
        account settings later cannot credit a match a second time."""
        index = {}
        try:
            with open(self.index_path(), 'rb') as stream:
                raw = stream.read()
        except FileNotFoundError:
            return index
        except OSError as error:
            raise StoreError('cannot read %s: %s' % (self.index_path(), error))
        lines = raw.split(b'\n')
        for line in lines[:-1]:           # the last piece has no newline: it waits
            try:
                entry = json.loads(line.decode('utf-8'))
                if isinstance(entry, dict) and isinstance(entry.get('id'), str) \
                        and isinstance(entry.get('account'), str):
                    index.setdefault(entry['id'], entry['account'])
            except (ValueError, UnicodeDecodeError):
                log('a line of %s was skipped' % self.index_path(),
                    key=('index-line', line[:80]))
        return index

    def _apply_spool(self, rebuilt=False):
        """Apply every complete, new spool line. Call with the lock held.
        `rebuilt`: a replay after a document was kept aside; the matches it
        restores are marked served (the screen showed them long ago)."""
        path = self.spool_path()
        try:
            size = os.stat(path).st_size
        except OSError:
            return
        offset = _SPOOL_OFFSETS.get(path, 0)
        if size < offset:
            offset = 0
        if size == offset:
            return
        try:
            with open(path, 'rb') as stream:
                stream.seek(offset)
                chunk = stream.read(size - offset)
        except OSError as error:
            log('cannot read the spool: %s' % error, key=('spool-read', path))
            return
        index = self._read_index()
        cache = {}                  # account -> [document, writable, changed]
        end = chunk.rfind(b'\n') + 1          # an unterminated tail waits
        position, resume = 0, None
        try:
            for line in chunk[:end].splitlines(keepends=True):
                record = _parse_record(line)
                if record is not None:
                    try:
                        self._apply_record(record, index, cache, rebuilt)
                    except StoreError as error:
                        # This match waits for the next request; the others go on.
                        log('cannot apply a finished match yet: %s' % error,
                            key=('apply', str(error)))
                        if resume is None:
                            resume = position
                position += len(line)
        finally:
            for account, (document, writable, changed) in cache.items():
                if changed and writable:
                    self._save(account, document)
        _SPOOL_OFFSETS[path] = offset + (position if resume is None else resume)
        if _REPLAY[0]:
            # A document was kept aside while applying: start the spool again.
            self._replay()

    def _decide(self, record):
        """The account a match not yet applied is credited to, or None.
        A match with more than one real player credits nobody unless every
        user has an account of their own (CRUCIBLE_PROGRESS_PER_USER=1): the
        backend cannot tell which shard user is its own player."""
        per_user = os.environ.get(PER_USER_ENV, '') not in ('', '0')
        players = record.get('players', 1)
        if type(players) is int and players > 1 and not per_user:
            log('a match with %d real players is credited to nobody: the backend cannot '
                'tell which user is its own player (set %s=1 for an account per user)'
                % (players, PER_USER_ENV), key='multi-nobody')
            return None
        return account_for_user(record['user_id'])

    def _apply_record(self, record, index, cache, rebuilt):
        match_id = record['id']
        indexed = match_id in index
        account = index[match_id] if indexed else self._decide(record)
        if account is None:
            return
        entry = cache.get(account)
        if entry is None:
            document, writable = self._load(account)
            entry = cache[account] = [document, writable, False]
        document, writable = entry[0], entry[1]
        if not writable:
            raise StoreError('%s is read-only' % self.path(account))
        if not indexed:
            # Decided first, applied second: a crash between the two is mended
            # by the next pass, which applies the match to the account indexed.
            try:
                append_line(self.index_path(), json.dumps(
                    {'id': match_id, 'account': account}, separators=(',', ':')) + '\n')
            except OSError as error:
                raise StoreError('cannot append to %s: %s' % (self.index_path(), error))
            index[match_id] = account
        if apply_match(document, record, served=rebuilt or _REPLAY[0]):
            entry[2] = True


def _parse_record(line):
    """A spool line as a validated record, or None (logged once) when it is not one."""
    try:
        record = json.loads(line.decode('utf-8'))
        if not isinstance(record, dict):
            raise ValueError('not an object')
        match_id = record['id']
        if not isinstance(match_id, str) or not 1 <= len(match_id) <= 128:
            raise ValueError('bad id')
        user = record.get('user_id', 0)
        if type(user) is not int or not 0 <= user <= 0xFFFFFFFF:
            raise ValueError('bad user_id')
        record['user_id'] = user
        hunter = record['hunter']
        if not isinstance(hunter, str) or not 1 <= len(hunter) <= 40:
            raise ValueError('bad hunter')
        record['hunter'] = hunter.strip().lower()
        if record.get('result') not in RESULTS:
            raise ValueError('bad result')
        duration = record.get('duration', 0)
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not 0 <= duration <= MAX_DURATION_SECONDS):
            raise ValueError('bad duration')
        mode = record.get('mode', '')
        record['mode'] = mode if isinstance(mode, str) else ''
        return record
    except (ValueError, KeyError, UnicodeDecodeError) as error:
        log('a spool line was skipped (%s)' % error, key=('line', line[:80]))
        return None


def apply_match(document, record, served=False):
    """Apply one validated spool record to a document. False when it was
    already applied (the id is in `matches`): the same match counts once.
    `served`: mark it already shown (a match rebuilt from the spool)."""
    if any(m.get('id') == record['id'] for m in document['matches']):
        return False
    hunter = document['hunters'].setdefault(record['hunter'], {})
    before = int(hunter.get('xp', 0))
    awards = match_awards(record['result'], record.get('duration', 0))
    gained = sum(xp for _, xp in awards)
    hunter['xp'] = before + gained
    document['xp']['account_total'] = int(document['xp'].get('account_total', 0)) + gained
    if record['user_id'] and record['user_id'] not in document['user_ids']:
        document['user_ids'].append(record['user_id'])
    entry = {'id': record['id'], 'user_id': record['user_id'], 'hunter': record['hunter'],
             'mode': record['mode'], 'result': record['result'],
             'duration': record.get('duration', 0), 'kills': record.get('kills'),
             'deaths': record.get('deaths'), 'time': record.get('time'),
             'xp_before': before, 'xp_gained': gained,
             'awards': [{'award': name, 'xp': xp}
                        for name, xp in awards],
             'served': bool(served)}
    document['matches'].append(entry)
    return True


# ---------------------------------------------------------------------------
# The module-level store the backend uses
# ---------------------------------------------------------------------------
def get_store():
    return Store()


# ---------------------------------------------------------------------------
# The shard's side: the hand-off
# ---------------------------------------------------------------------------
def shard_tick(state, log_line=None, *, participants, mode=""):
    """Hand off a finished match for (user_id, hunter, team) participants."""
    try:
        _shard_tick(state, log_line or (lambda message: None), participants, mode)
    except Exception as error:  # the match must go on
        log('the shard hand-off failed: %r' % (error,), key='shard-hand-off')


def _result_of(result, team):
    winner_team = result.get('winner_team')
    if result.get('is_draw', not winner_team):
        return 'draw'
    return 'win' if winner_team == team else 'loss'


def _match_lines(state, match, result, participants, mode):
    if match is not None:
        if str(result.get('reason', '')).startswith('the other player never arrived'):
            return []       # multi_match.check_wait: the match was never played
    players = list(participants)
    # The shard counts no kills. It counts the player's deaths only for the
    # respawn countdown, which is behind its own switch.
    deaths = (state.get('respawn_countdown') or {}).get('deaths') if match is None else None
    match_id = uuid.uuid4().hex
    lines = []
    for user, hunter, team in players:
        record = {'id': '%s-%d' % (match_id, user), 'user_id': user, 'hunter': hunter,
                  'mode': mode, 'result': _result_of(result, team),
                  'reason': result.get('reason', ''),
                  'duration': round(float(result.get('at_seconds') or 0), 3),
                  'kills': None, 'players': len(players),
                  'deaths': deaths, 'time': time.time()}
        lines.append(json.dumps(record, separators=(',', ':')) + '\n')
    return lines


@dataclass
class _PendingSpool:
    path: str
    lines: list
    next_line: int = 0
    retry_at: float = 0.0


def _shard_tick(state, log_line, participants, mode):
    match = state.get('match')
    # Both viewers tick the same Match on the shard's single event loop.
    owner = state if match is None else vars(match)
    if owner.get('progress_spooled'):
        return
    result = state.get('match_result') if match is None else getattr(match, 'result', None)
    if result is None:
        return
    pending = owner.get('progress_pending')
    if pending is None:
        lines = _match_lines(state, match, result, participants, mode)
        if not lines:
            owner['progress_spooled'] = True
            return
        pending = owner['progress_pending'] = _PendingSpool(
            os.path.join(directory(), SPOOL_NAME), lines)
    elif time.monotonic() < pending.retry_at:
        return
    try:
        os.makedirs(os.path.dirname(pending.path), exist_ok=True)
        while pending.next_line < len(pending.lines):
            append_line(pending.path, pending.lines[pending.next_line])
            pending.next_line += 1
    except OSError as error:
        pending.retry_at = time.monotonic() + SPOOL_RETRY_SECONDS
        log('cannot append to the spool %s: %s; will retry while the match is active'
            % (pending.path, error), key=('spool-write', pending.path))
        return
    owner['progress_spooled'] = True
    del owner['progress_pending']
    log_line('      PROGRESS: %d finished-match line(s) handed to %s'
             % (len(pending.lines), pending.path))
