# std-lib
from builtins import map
from builtins import range
from builtins import object
import hashlib
import re
import redpipe
from . import fields as hbom_fields

# 3rd-party (optional)
try:
    import rediscluster  # noqa
except ImportError:
    rediscluster = None

try:
    import redis  # noqa
    from redis.exceptions import RedisError
except ImportError:
    redis = None

    class RedisError(Exception):
        pass

# internal modules
from .pipeline import Pipeline

__all__ = ['RedisContainer', 'RedisList', 'RedisIndex',
           'RedisString', 'RedisSet', 'RedisSortedSet', 'RedisHash',
           'RedisDistributedHash', 'RedisObject', 'RedisColdStorageObject']

EXPIRE_DEFAULT = 60
FREEZE_TTL_DEFAULT = 300

lua_restorenx = """
local key = KEYS[1]
local pttl = ARGV[1]
local data = ARGV[2]
local res = redis.call('exists', key)
if res == 0 then
    redis.call('restore', key, pttl, data)
    return 1
else
    return 0
end
"""

lua_object_info = """
local key = KEYS[1]
local subcommand = ARGV[1]
return redis.call('object', subcommand, key)
"""


def _parse_values(values):
    (_values,) = values if len(values) == 1 else (None,)
    if _values and isinstance(_values, list):
        return _values
    return values


def create_core(parent, conn=None, valparse=hbom_fields.TextField, cls_fields=None):
    class core(parent):
        keyspace = None
        connection = conn
        valueparse = valparse._redpipe_equivalent
        if cls_fields:
            fields = {k: v._redpipe_equivalent for k, v in cls_fields.items()}

    return core


class RedisContainer(object):
    """
    Base class for all containers. This class should not
    be used and does not provide anything except the ``db``
    member.
    :members:
    db can be either pipeline or redis object
    """

    _db = None

    def __init__(self, key, pipe=None):
        self._key = key
        self.key = self.db_key(self._key)
        self._pipe = pipe
        self._core = None

    @property
    def pipe(self):
        """
        Get a fresh pipeline() to be used in a `with` block.

        :return: Pipeline or NestedPipeline with autoexec set to true.
        """
        return redpipe.pipeline(self._pipe, name=self._db, autoexec=True)

    @classmethod
    def _ks(cls):
        """
        The internal keyspace used to namespace the data in redis.
        defaults to the class name.
        """
        return getattr(cls, '_keyspace', cls.__name__)

    @classmethod
    def db_key(cls, key):
        return '%s{%s}' % (cls._ks(), key)

    def primary_key(self):
        return self._key

    def delete(self):
        """
        Remove the container from the redis storage
        > s = Set('test')
        > s.add('1')
        1
        > s.clear()
        > s.members
        set([])

        """
        return self._core.delete(self.key)

    clear = delete

    def expire(self, time=None):
        """
        Allow the key to expire after ``time`` seconds.

        > s = Set("test")
        > s.add("1")
        1
        > s.set_expire(1)
        > # from time import sleep
        > # sleep(1)
        > # s.members
        # set([])
        > s.clear()

        :param time: time expressed in seconds.
            If time is not specified,
            then ``default_expire_time`` will be used.
        :rtype: None
        """
        if time is None:
            time = EXPIRE_DEFAULT
        return self._core.expire(self.key, time)

    set_expire = expire

    def exists(self):
        return self._core.exists(self.key)

    def eval(self, script, *args):
        return self._core.eval(script, 1, self.key, *args)

    def dump(self):
        return self._core.dump(self.key)

    def restore(self, data, pttl=0):
        return self.eval(lua_restorenx, pttl, data)

    def ttl(self):
        return self._core.ttl(self.key)

    def persist(self):
        return self._core.persist(self.key)

    def object(self, subcommand):
        return self.eval(lua_object_info, subcommand)

    @classmethod
    def scan(cls, cursor=0, match=None, count=None):
        """
        Incrementally return lists of key names. Also return a cursor
        indicating the scan position.

        ``match`` allows for filtering the keys by pattern

        ``count`` allows for hint the minimum number of returns
        """
        f = redpipe.Future()

        if match is None:
            match = '*'
        match = "%s{%s}" % (cls._ks(), match)
        pattern = re.compile(r'^%s{(.*)}$' % cls._ks())

        with redpipe.pipeline(name=cls._db, autoexec=True) as pipe:

            res = pipe.scan(cursor=cursor, match=match, count=count)
            decode = redpipe.TextField.decode

            def cb():
                keys = []
                for k in res[1]:
                    k = decode(k)
                    m = pattern.match(k)
                    if m:
                        keys.append(m.group(1))

                f.set((res[0], keys))

            pipe.on_execute(cb)
            return f

    @classmethod
    def scan_iter(cls, match=None, count=None):
        """
        Make an iterator using the SCAN command so that the client doesn't
        need to remember the cursor position.

        ``match`` allows for filtering the keys by pattern

        ``count`` allows for hint the minimum number of returns
        """
        cursor = '0'
        while cursor != 0:
            cursor, data = cls.scan(cursor=cursor, match=match, count=count)
            for item in data:
                yield item

    @classmethod
    def ids(cls):
        for item in cls.scan_iter(count=100):
            yield item


class RedisString(RedisContainer):

    def __init__(self, key, pipe=None, valueparse=hbom_fields.TextField):
        super(RedisString, self).__init__(key, pipe)
        self._core = create_core(redpipe.String, self._db, valparse=valueparse)(pipe=self._pipe)

    def __repr__(self):
        return "<%s '%s'>" % (self.__class__.__name__, self.key)

    def get(self):
        """
        set the value as a string in the key
        """
        return self._core.get(self.key)

    def set(self, value):
        """
        set the value as a string in the key
        :param value:
        """
        return self._core.set(self.key, value)

    def incr(self):
        """
        increment the value for key by 1
        """
        return self._core.incr(self.key)

    def incrby(self, value=1):
        """
        increment the value for key by value: int
        :param value:
        """
        return self._core.incr(self.key, value)

    def incrbyfloat(self, value=1.0):
        """
        increment the value for key by value: float
        :param value:
        """
        return self._core.incrbyfloat(self.key, value)

    def setnx(self, value):
        """
        Set the value as a string in the key only if the key doesn't exist.
        :param value:
        :return:
        """
        return self._core.setnx(self.key, value)


class RedisSet(RedisContainer):
    """
    .. default-domain:: set

    This class represent a Set in redis.
    """

    def __init__(self, key, pipe=None, valueparse=hbom_fields.TextField):
        super(RedisSet, self).__init__(key, pipe)
        self._core = create_core(redpipe.Set, self._db, valparse=valueparse)(pipe=self._pipe)

    def __repr__(self):
        return "<%s '%s'>" % (self.__class__.__name__, self.key)

    def sadd(self, *values):
        """
        Add the specified members to the Set.

        :param values: a list of values or a simple value.
        :rtype: integer representing the number of value added to the set.

        > s = Set("test")
        > s.clear()
        > s.add(["1", "2", "3"])
        3
        > s.add(["4"])
        1
        > print s
        <Set 'test' set(['1', '3', '2', '4'])>
        > s.clear()

        """
        return self._core.sadd(self.key, values)

    def srem(self, *values):
        """
        Remove the values from the Set if they are present.

        :param values: a list of values or a simple value.
        :rtype: boolean indicating if the values have been removed.

        > s = Set("test")
        > s.add(["1", "2", "3"])
        3
        > s.srem(["1", "3"])
        2
        > s.clear()

        """
        return self._core.srem(self.key, *values)

    def spop(self):
        """
        Remove and return (pop) a random element from the Set.

        :rtype: String representing the value poped.

        > s = Set("test")
        > s.add("1")
        1
        > s.spop()
        '1'
        > s.members
        set([])

        """
        return self._core.spop(self.key)

    def all(self):
        return self._core.smembers(self.key)

    members = property(all)

    def scard(self):
        """
        Returns the cardinality of the Set.

        :rtype: String containing the cardinality.

        """
        return self._core.scard(self.key)

    def sismember(self, value):
        """
        Return ``True`` if the provided value is in the ``Set``.
        :param value:

        """
        return self._core.sismember(self.key, value)

    def srandmember(self):
        """
        Return a random member of the set.

        > s = Set("test")
        > s.add(['a', 'b', 'c'])
        3
        > s.srandmember() # doctest: +ELLIPSIS
        '...'
        > # 'a', 'b' or 'c'
        """
        return self._core.srandmember(self.key)

    add = sadd
    pop = spop
    remove = srem


class RedisList(RedisContainer):
    """
    This class represent a list object as seen in redis.
    """

    def __init__(self, key, pipe=None, valueparse=hbom_fields.TextField):
        super(RedisList, self).__init__(key, pipe)
        self._core = create_core(redpipe.List, self._db, valparse=valueparse)(pipe=self._pipe)

    def all(self):
        """
        Returns all items in the list.
        """
        return self.lrange(0, -1)

    members = property(all)
    """Return all items in the list."""

    def llen(self):
        """
        Returns the length of the list.
        """
        return self._core.llen(self.key)

    def lrange(self, start, stop):
        """
        Returns a range of items.

        :param start: integer representing the start index of the range
        :param stop: integer representing the size of the list.

        > l = List("test")
        > l.push(['a', 'b', 'c', 'd'])
        4L
        > l.lrange(1, 2)
        ['b', 'c']
        > l.clear()

        """
        return self._core.lrange(self.key, start, stop)

    def lpush(self, *values):
        """
        Push the value into the list from the *left* side

        :param values: a list of values or single value to push
        :rtype: long representing the number of values pushed.

        > l = List("test")
        > l.lpush(['a', 'b'])
        2L
        > l.clear()
        """
        return self._core.lpush(self.key, *values)

    def rpush(self, *values):
        """
        Push the value into the list from the *right* side

        :param values: a list of values or single value to push
        :rtype: long representing the size of the list.

        > l = List("test")
        > l.lpush(['a', 'b'])
        2L
        > l.rpush(['c', 'd'])
        4L
        > l.members
        ['b', 'a', 'c', 'd']
        > l.clear()
        """
        return self._core.rpush(self.key, *values)

    def extend(self, iterable):
        """
        Extend list by appending elements from the iterable.

        :param iterable: an iterable objects.
        """
        self.rpush(*[e for e in iterable])

    def count(self, value):
        """
        Return number of occurrences of value.

        :param value: a value tha *may* be contained in the list
        """
        return self.members.count(value)

    def lpop(self):
        """
        Pop the first object from the left.

        :return: the popped value.

        """
        return self._core.lpop(self.key)

    def rpop(self):
        """
        Pop the first object from the right.

        :return: the popped value.
        """
        return self._core.rpop(self.key)

    def rpoplpush(self, key):
        """
        Remove an element from the list,
        atomically add it to the head of the list indicated by key

        :param key: the key of the list receiving the popped value.
        :return: the popped (and pushed) value

        > l = List('list1')
        > l.push(['a', 'b', 'c'])
        3L
        > l.rpoplpush('list2')
        'c'
        > l2 = List('list2')
        > l2.members
        ['c']
        > l.clear()
        > l2.clear()
        """
        return self._core.rpoplpush(self.key, key)

    def lrem(self, value, num=1):
        """
        Remove first occurrence of value.
        :param num:
        :param value:
        :return: 1 if the value has been removed, 0 otherwise
        if you see an error here, did you use redis.StrictRedis()?
        """
        return self._core.lrem(self.key, value, num)

    def reverse(self):
        """
        Reverse the list in place.

        :return: None
        """
        r = self.members[:]
        r.reverse()
        self.clear()
        self.extend(r)

    def ltrim(self, start, end):
        """
        Trim the list from start to end.

        :param start:
        :param end:
        :return: None
        """
        return self._core.ltrim(self.key, start, end)

    def lindex(self, idx):
        """
        Return the value at the index *idx*

        :param idx: the index to fetch the value.
        :return: the value or None if out of range.
        """
        return self._core.lindex(self.key, idx)

    def lset(self, idx, value=0):
        """
        Set the value in the list at index *idx*

        :param value:
        :param idx:
        :return: True is the operation succeed.

        > l = List('test')
        > l.push(['a', 'b', 'c'])
        3L
        > l.lset(0, 'e')
        True
        > l.members
        ['e', 'b', 'c']
        > l.clear()

        """
        return self._core.lset(self.key, idx, value)

    def __repr__(self):
        return "<%s '%s'>" % (self.__class__.__name__, self.key)

    # noinspection PyRedeclaration
    remove = lrem
    trim = ltrim
    shift = lpop
    unshift = lpush
    pop = rpop
    pop_onto = rpoplpush
    push = rpush
    append = rpush


class RedisSortedSet(RedisContainer):
    """
    This class represents a SortedSet in redis.
    Use it if you want to arrange your set in any order.

    """

    def __init__(self, key, pipe=None, valueparse=hbom_fields.TextField):
        super(RedisSortedSet, self).__init__(key, pipe)
        self._core = create_core(redpipe.SortedSet, self._db, valparse=valueparse)(pipe=self._pipe)

    @property
    def members(self):
        """
        Returns the members of the set.
        """
        return self.zrange(0, -1)

    @property
    def revmembers(self):
        """
        Returns the members of the set in reverse.
        """
        return self.zrevrange(0, -1)

    def __repr__(self):
        return "<%s '%s'>" % (self.__class__.__name__, self.key)

    def lt(self, v, limit=None, offset=None):
        """
        Returns the list of the members of the set that have scores
        less than v.

        :param v: the score to compare to.
        :param limit: limit the result to *n* elements
        :param offset: Skip the first *n* elements
        """
        if limit is not None and offset is None:
            offset = 0
        return self.zrangebyscore("-inf", "(%f" % v, start=offset, num=limit)

    def le(self, v, limit=None, offset=None):
        """
        Returns the list of the members of the set that have scores
        less than or equal to v.

        :param v: the score to compare to.
        :param limit: limit the result to *n* elements
        :param offset: Skip the first *n* elements

        """
        if limit is not None and offset is None:
            offset = 0
        return self.zrangebyscore("-inf", v, start=offset, num=limit)

    def gt(self, v, limit=None, offset=None, withscores=False):
        """Returns the list of the members of the set that have scores
        greater than v.
        :param withscores:
        :param offset:
        :param limit:
        :param v:
        """
        if limit is not None and offset is None:
            offset = 0
        return self.zrangebyscore(
            "(%f" % v, "+inf",
            start=offset,
            num=limit,
            withscores=withscores)

    def ge(self, v, limit=None, offset=None, withscores=False):
        """Returns the list of the members of the set that have scores
        greater than or equal to v.

        :param withscores:
        :param v: the score to compare to.
        :param limit: limit the result to *n* elements
        :param offset: Skip the first *n* elements

        """
        if limit is not None and offset is None:
            offset = 0
        return self.zrangebyscore(
            "%f" % v, "+inf",
            start=offset,
            num=limit,
            withscores=withscores)

    def between(self, low, high, limit=None, offset=None):
        """
        Returns the list of the members of the set that have scores
        between min and max.

        .. Note::
            The min and max are inclusive when comparing the values.

        :param low: the minimum score to compare to.
        :param high: the maximum score to compare to.
        :param limit: limit the result to *n* elements
        :param offset: Skip the first *n* elements

        > s = SortedSet("foo")
        > s.add('a', 10)
        1
        > s.add('b', 20)
        1
        > s.add('c', 30)
        1
        > s.between(20, 30)
        ['b', 'c']
        > s.clear()
        """
        if limit is not None and offset is None:
            offset = 0
        return self.zrangebyscore(low, high, start=offset, num=limit)

    def zadd(self, members, score=1, nx=False, xx=False, ch=False, incr=False):
        """
        Add members in the set and assign them the score.

        :param members: a list of item or a single item
        :param score: the score the assign to the item(s)

        > s = SortedSet("foo")
        > s.add('a', 10)
        1
        > s.add('b', 20)
        1
        > s.clear()
        """

        if nx:
            _args = ['NX']
        elif xx:
            _args = ['XX']
        else:
            _args = []

        if ch:
            _args.append('CH')

        if incr:
            _args.append('INCR')

        if isinstance(members, dict):
            for member, score in members.items():
                _args += [score, member]
        else:
            _args += [score, members]

        if nx and xx:
            raise RedisError('cannot specify nx and xx at the same time')
        with self.pipe as p:
            return p.execute_command('ZADD', self.key, *_args)

    def zrem(self, *values):
        """
        Remove the values from the SortedSet

        :param values:
        :return: True if **at least one** value is successfully
                 removed, False otherwise

        > s = SortedSet('foo')
        > s.add('a', 10)
        1
        > s.zrem('a')
        1
        > s.members
        []
        > s.clear()
        """
        return self._core.zrem(self.key, *values)

    def zincrby(self, att, value=1):
        """
        Increment the score of the item by ``value``

        :param att: the member to increment
        :param value: the value to add to the current score
        :returns: the new score of the member

        > s = SortedSet("foo")
        > s.add('a', 10)
        1
        > s.zincrby("a", 10)
        20.0
        > s.clear()
        """
        return self._core.zincrby(self.key, att, value)

    def zrevrank(self, member):
        """
        Returns the ranking in reverse order for the member

        > s = SortedSet("foo")
        > s.add('a', 10)
        1
        > s.add('b', 20)
        1
        > s.revrank('a')
        1
        > s.clear()
        :param member:
        """
        return self._core.zrevrank(self.key, member)

    def zrange(self, start, end, **kwargs):
        """
        Returns all the elements including between ``start`` (non included) and
        ``stop`` (included).

        :param kwargs:
        :param end:
        :param start:
        > s = SortedSet("foo")
        > s.add('a', 10)
        1
        > s.add('b', 20)
        1
        > s.add('c', 30)
        1
        > s.zrange(1, 3)
        ['b', 'c']
        > s.zrange(1, 3, withscores=True)
        [('b', 20.0), ('c', 30.0)]
        > s.clear()
        """
        return self._core.zrange(self.key, start, end, **kwargs)

    def zrevrange(self, start, end, **kwargs):
        """
        Returns the range of items included between ``start`` and ``stop``
        in reverse order (from high to low)

        > s = SortedSet("foo")
        > s.add('a', 10)
        1
        > s.add('b', 20)
        1
        > s.add('c', 30)
        1
        > s.zrevrange(1, 2)
        ['b', 'a']
        > s.clear()
        :param kwargs:
        :param kwargs:
        :param end:
        :param start:
        :param start:
        """
        return self._core.zrevrange(self.key, start, end, **kwargs)

    # noinspection PyShadowingBuiltins
    def zrangebyscore(self, min, max, **kwargs):
        """
        Returns the range of elements included between the scores (min and max)

        > s = SortedSet("foo")
        > s.add('a', 10)
        1
        > s.add('b', 20)
        1
        > s.add('c', 30)
        1
        > s.zrangebyscore(20, 30)
        ['b', 'c']
        > s.clear()
        :param min: int
        :param max: int
        :param kwargs: dict
        """
        return self._core.zrangebyscore(self.key, min, max, **kwargs)

    # noinspection PyShadowingBuiltins
    def zrevrangebyscore(self, max, min, **kwargs):
        """
        Returns the range of elements included between the scores (min and max)

        > s = SortedSet("foo")
        > s.add('a', 10)
        1
        > s.add('b', 20)
        1
        > s.add('c', 30)
        1
        > s.zrangebyscore(20, 20)
        ['b']
        > s.clear()
        :param kwargs:
        :param min:
        :param max:
        """
        return self._core.zrevrangebyscore(self.key, max, min, **kwargs)

    def zcard(self):
        """
        Returns the cardinality of the SortedSet.

        > s = SortedSet("foo")
        > s.add("a", 1)
        1
        > s.add("b", 2)
        1
        > s.add("c", 3)
        1
        > s.zcard()
        3
        > s.clear()
        """
        return self._core.zcard(self.key)

    def zscore(self, elem):
        """
        Return the score of an element

        > s = SortedSet("foo")
        > s.add("a", 10)
        1
        > s.score("a")
        10.0
        > s.clear()
        :param elem:
        """
        return self._core.zscore(self.key, elem)

    def zremrangebyrank(self, start, stop):
        """
        Remove a range of element between the rank ``start`` and
        ``stop`` both included.

        :param stop:
        :param start:
        :return: the number of item deleted

        > s = SortedSet("foo")
        > s.add("a", 10)
        1
        > s.add("b", 20)
        1
        > s.add("c", 30)
        1
        > s.zremrangebyrank(1, 2)
        2
        > s.members
        ['a']
        > s.clear()
        """
        return self._core.zremrangebyrank(self.key, start, stop)

    def zremrangebyscore(self, min_value, max_value):
        """
        Remove a range of element by between score ``min_value`` and
        ``max_value`` both included.

        :param max_value:
        :param min_value:
        :returns: the number of items deleted.

        > s = SortedSet("foo")
        > s.add("a", 10)
        1
        > s.add("b", 20)
        1
        > s.add("c", 30)
        1
        > s.zremrangebyscore(10, 20)
        2
        > s.members
        ['c']
        > s.clear()
        """
        return self._core.zremrangebyscore(self.key, min_value, max_value)

    def zrank(self, elem):
        """
        Returns the rank of the element.

        > s = SortedSet("foo")
        > s.add("a", 10)
        1
        > s.zrank("a")
        0
        > s.clear()
        :param elem:
        """
        return self._core.zrank(self.key, elem)

    def zcount(self, min, max):
        """
        Returns the number of elements in the sorted set at key ``name`` with
        a score between ``min`` and ``max``.
        """
        return self._core.zcount(self.key, min, max)

    def eq(self, value):
        """
        Returns the elements that have ``value`` for score.
        :param value:
        """
        return self.zrangebyscore(value, value)

    revrank = zrevrank
    score = zscore
    rank = zrank
    incr_by = zincrby
    add = zadd
    remove = zrem


class RedisHash(RedisContainer):
    def __init__(self, key, pipe=None, valueparse=hbom_fields.TextField, fields=None):
        super(RedisHash, self).__init__(key, pipe)
        self._core = create_core(redpipe.Hash, self._db,
                                 valparse=valueparse, cls_fields=fields)(pipe=self._pipe)

    def __repr__(self):
        return "<%s '%s'>" % (self.__class__.__name__, self.key)

    def hlen(self):
        """
        Returns the number of elements in the Hash.
        """
        return self._core.hlen(self.key)

    def hset(self, member, value):
        """
        Set ``member`` in the Hash at ``value``.

        :param value:
        :param member:
        :returns: 1 if member is a new field and the value has been
                  stored, 0 if the field existed and the value has been
                  updated.

        > h = Hash("foo")
        > h.hset("bar", "value")
        1L
        > h.clear()
        """
        return self._core.hset(self.key, member, value)

    def hsetnx(self, member, value):
        """
        Set ``member`` in the Hash at ``value``.

        :param value:
        :param member:
        :returns: 1 if member is a new field and the value has been
                  stored, 0 if the field existed and the value has been
                  updated.

        > h = Hash("foo")
        > h.hset("bar", "value")
        1L
        > h.clear()
        """
        return self._core.hsetnx(self.key, member, value)

    def hdel(self, *members):
        """
        Delete one or more hash field.

        :param members: on or more fields to remove.
        :return: the number of fields that were removed

        > h = Hash("foo")
        > h.hset("bar", "value")
        1L
        > h.hdel("bar")
        1
        > h.clear()
        """
        return self._core.hdel(self.key, *members)

    def hkeys(self):
        """
        Returns all fields name in the Hash
        """
        return self._core.hkeys(self.key)

    def hgetall(self):
        """
        Returns all the fields and values in the Hash.

        :rtype: dict
        """
        return self._core.hgetall(self.key)

    def hvals(self):
        """
        Returns all the values in the Hash

        :rtype: list
        """
        return self._core.hvals(self.key)

    def hget(self, field):
        """
        Returns the value stored in the field, None if the field doesn't exist.
        :param field:
        """
        return self._core.hget(self.key, field)

    def hexists(self, field):
        """
        Returns ``True`` if the field exists, ``False`` otherwise.
        :param field:
        """
        return self._core.hexists(self.key, field)

    def hincrby(self, field, increment=1):
        """
        Increment the value of the field.
        :param increment:
        :param field:
        :returns: the value of the field after incrementation

        > h = Hash("foo")
        > h.hincrby("bar", 10)
        10L
        > h.hincrby("bar", 2)
        12L
        > h.clear()
        """
        return self._core.hincrby(self.key, field, increment)

    def hmget(self, fields):
        """
        Returns the values stored in the fields.
        :param fields:
        """
        redpipe_fields = [k for k in fields]
        return self._core.hmget(self.key, redpipe_fields)

    def hmset(self, mapping):
        """
        Sets or updates the fields with their corresponding values.

        :param mapping: a dict with keys and values
        """
        return self._core.hmset(self.key, mapping)


class RedisDistributedHash(RedisContainer):
    _shards = 1000

    def __init__(self, key, pipe=None, valueparse=hbom_fields.TextField):
        super(RedisDistributedHash, self).__init__(key, pipe)
        self._core = create_core(redpipe.Hash, self._db, valparse=valueparse)(pipe=self._pipe)

    def __repr__(self):
        return "<%s '%s'>" % (self.__class__.__name__, self.key)

    def redis_sharded_key(self, member):
        return "%s:%s" % (
            self.key,
            int(hashlib.md5(member if isinstance(member, bytes)
                else member.encode('utf-8')).hexdigest(), 16) % self._shards)

    def hlen(self):
        """
        Returns the number of elements in the Hash.
        """
        with self.pipe as p:
            data = [redpipe.Hash(pipe=p).hlen("%s:%s" % (self.key, i))
                    for i in range(0, self._shards)]
            f = redpipe.Future()

            def cb():
                f.set(sum(data))

            p.on_execute(cb)
            return f

    def hset(self, member, value):
        """
        Set ``member`` in the Hash at ``value``.

        :param value:
        :param member:
        :returns: 1 if member is a new field and the value has been
                  stored, 0 if the field existed and the value has been
                  updated.

        > h = Hash("foo")
        > h.hset("bar", "value")
        1L
        > h.clear()
        """
        return self._core.hset(self.redis_sharded_key(member), member, value)

    def hdel(self, *members):
        """
        Delete one or more hash field.

        :param members: on or more fields to remove.
        :return: the number of fields that were removed

        > h = Hash("foo")
        > h.hset("bar", "value")
        1L
        > h.hdel("bar")
        1
        > h.clear()
        """

        with self.pipe as p:
            data = [redpipe.Hash(pipe=p).hdel(self.redis_sharded_key(member), member)
                    for member in _parse_values(members)]
            f = redpipe.Future()

            def cb():
                f.set(sum(data))

            p.on_execute(cb)
            return f

    def hget(self, field):
        """
        Returns the value stored in the field, None if the field doesn't exist.
        :param field:
        """
        return self._core.hget(self.redis_sharded_key(field), field)

    def hexists(self, field):
        """
        Returns ``True`` if the field exists, ``False`` otherwise.
        :param field:
        """
        return self._core.hexists(self.redis_sharded_key(field), field)

    def hincrby(self, field, increment=1):
        """
        Increment the value of the field.
        :param increment:
        :param field:
        :returns: the value of the field after incrementation

        > h = Hash("foo")
        > h.hincrby("bar", 10)
        10L
        > h.hincrby("bar", 2)
        12L
        > h.clear()
        """
        return self._core.hincrby(self.redis_sharded_key(field), field, increment)


class RedisIndex(RedisHash):
    @classmethod
    def db_key(cls, shard):
        return getattr(cls, '_key_tpl', cls.__name__ + ":%s:u") % shard

    @classmethod
    def shard(cls, key, pipe=None):
        shard_ct = cls.shard_count()
        keyhash = hashlib.md5(key if isinstance(key, bytes) else key.encode('utf-8')).hexdigest()
        return cls(int(keyhash, 16) % shard_ct, pipe=pipe)

    @classmethod
    def shard_count(cls):
        return getattr(cls, '_shard_count', 64)

    @classmethod
    def get(cls, key, pipe=None):
        return cls.shard(key, pipe=pipe).hget(key)

    @classmethod
    def mget(cls, keys, pipe=None):

        with redpipe.pipeline(pipe=pipe, autoexec=True) as p:
            f = redpipe.Future()
            mapping = {k: cls.shard(k, pipe=p).hget(k) for k in keys}

            def cb():
                f.set({k: v for k, v in mapping.items() if v.result})

            p.on_execute(cb)

            return f

    @classmethod
    def remove(cls, key, pipe=None):
        return cls.shard(key, pipe=pipe).hdel(key)

    @classmethod
    def setnx(cls, key, value, pipe=None):
        return cls.shard(key, pipe=pipe).hsetnx(key, value)

    @classmethod
    def set(cls, key, value, pipe=None):
        return cls.shard(key, pipe=pipe).hset(key, value)

    @classmethod
    def all(cls):
        keys = [cls.db_key(i) for i in range(0, cls.shard_count() - 1)]
        for key in keys:
            cursor = 0
            while True:
                with redpipe.pipeline(name=cls._db, autoexec=True) as p:
                    res = p.hscan(key, cursor=cursor, count=500)

                cursor, elements = res
                if elements:
                    for k, v in elements.items():
                        yield k.decode('utf-8'), v.decode('utf-8')

                if cursor == 0:
                    break


class RedisObject(object):
    @classmethod
    def save(cls, instance, pipe=None, full=False):

        # we can save as long as the fields match.
        # this allows us to use wrapper classes that
        # implement the same interface.
        fields = getattr(getattr(cls, 'definition'), '_fields')
        if getattr(instance, '_fields') != fields:
            raise RuntimeError(
                'incorrect instance type for %s:save' % cls.__name__)

        state = instance.changes_(full=full)

        if not state:
            return 0
        p = Pipeline() if pipe is None else pipe
        _pk = instance.primary_key()
        s = getattr(cls, 'storage')(_pk, pipe=p, fields=fields)

        # apply remove to the record
        remove = [k for k, v in state.items() if v is None]
        if remove:
            s.hdel(*remove)

        # apply add to the record
        add = {k: v for k, v in state.items() if v is not None}
        if add:
            s.hmset(add)

        p.on_execute(instance.persisted_)

        if not pipe:
            p.execute()

        return len(state)

    @classmethod
    def delete(cls, _pk, pipe=None):
        fields = getattr(getattr(cls, 'definition'), '_fields')
        res = getattr(cls, 'storage')(_pk, pipe=pipe, fields=fields).hdel(*fields)
        return res

    @classmethod
    def new(cls, **kwargs):
        definition = getattr(cls, 'definition')
        obj = definition(**kwargs)
        return obj

    @classmethod
    def get(cls, _pk, pipe=None):
        return cls.get_multi([_pk], pipe=pipe)[0]

    @classmethod
    def get_multi(cls, _pks, pipe=None):
        definition = getattr(cls, 'definition')
        with Pipeline(pipe=pipe, autoexec=True) as p:
            storage = getattr(cls, 'storage')
            fields = getattr(definition, '_fields')

            def prep(pk):
                ref = definition(_ref=pk, _parent=cls)
                s = storage(pk, pipe=p, fields=fields)
                r = s.hmget(fields)

                def set_data():
                    if any(v is not None for v in r.result):
                        ref.load_(r.result)
                    else:
                        setattr(ref, '_new', True)

                p.on_execute(set_data)
                return ref

            refs = [prep(pk) for pk in _pks]

            return refs

    @classmethod
    def ref(cls, pk, pipe=None):
        if pipe is not None:
            return cls.get(pk, pipe=pipe)

        return getattr(cls, 'definition')(_ref=pk, _parent=cls)

    @classmethod
    def is_hot_key(cls, key):
        """
        override this method to define keys that should not ever go down
        into cold storage.
        Args:
            key:

        Returns:

        """
        return False

    @classmethod
    def prepare(cls, ref, pipe):
        _pk = ref.primary_key()
        definition = ref.__class__
        fields = getattr(definition, '_fields')
        s = getattr(cls, 'storage')(_pk, pipe=pipe, fields=fields)
        r = s.hmget(fields)

        def set_data():
            if any(v is not None for v in r.result):
                ref.load_(r.result)
            else:
                setattr(ref, '_new', True)

        pipe.on_execute(set_data)


class RedisColdStorageObject(RedisObject):
    @classmethod
    def delete(cls, _pk, pipe=None):
        res = super(RedisColdStorageObject, cls).delete(_pk, pipe=pipe)
        # can we get away with not deleting from cold storage on hot key?
        cold_storage = getattr(cls, 'coldstorage')
        cold_storage.delete(_pk)
        return res

    @classmethod
    def is_hot_key(cls, key):
        """
        override this method to define keys that should not ever go down
        into cold storage.
        Args:
            key:

        Returns:

        """
        return False

    @classmethod
    def get_multi(cls, _pks, pipe=None):
        storage = getattr(cls, 'storage')
        storage_name = getattr(storage, '_db')
        with Pipeline(pipe=pipe, name=storage_name, autoexec=True) as p:

            cold_storage = getattr(cls, 'coldstorage')
            cold_keys = {pk for pk in _pks if not cls.is_hot_key(pk)}
            missing_cache = {}
            for pk in cold_keys:
                s = storage(pk, pipe=p)
                s.persist()
                missing_cache[pk] = p.exists("%s__xx" % s.key)

            refs = super(RedisColdStorageObject, cls).get_multi(_pks, pipe=p)

            def cb():
                for pk, ref in missing_cache.items():
                    if ref.result:
                        cold_keys.remove(pk)

                missing = {r.primary_key() for r in refs if not r.exists() and
                           r.primary_key() in cold_keys}
                found = {k: v for k, v in cold_storage.get_multi(missing).items()
                         if v is not None}

                with Pipeline(name=storage_name, autoexec=True) as pp:
                    definition = getattr(cls, 'definition')
                    fields = getattr(definition, '_fields')
                    freeze_ttl = getattr(cls, 'freeze_ttl', FREEZE_TTL_DEFAULT)

                    def _load(k, v):
                        s = storage(k, pipe=pp, fields=fields)
                        s.persist()
                        s.restore(v)
                        return s.hmget(fields)

                    def _no_load(k):
                        pp.set('%s__xx' % s.key, '1')
                        pp.expire('%s__xx' % s.key, freeze_ttl - 1)

                    found = {k: _load(k, v) for k, v in found.items()}
                    for k in missing:
                        if k in found:
                            continue
                        _no_load(k)

                for ref in refs:
                    if ref.exists() or ref.primary_key() not in missing:
                        continue
                    try:
                        ref.load_(found[ref.primary_key()].result)
                    except KeyError:
                        setattr(ref, '_new', True)
                cold_storage.delete_multi(found.keys())

            p.on_execute(cb)
            return refs

    @classmethod
    def prepare(cls, ref, pipe):
        _pk = ref.primary_key()
        definition = ref.__class__
        fields = getattr(definition, '_fields')
        storage = getattr(cls, 'storage')
        s = storage(_pk, pipe=pipe, fields=fields)
        cold_storage = getattr(cls, 'coldstorage')
        missing_cache = False
        frozen_key_cache = "%s__xx" % s.key
        if not cls.is_hot_key(_pk):
            s.persist()
            with s.pipe as pp:
                missing_cache = pp.exists(frozen_key_cache)

        r = s.hmget(fields)

        def set_data():
            if any(v is not None for v in r.result):
                ref.load_(r.result)
                return

            if cls.is_hot_key(_pk):
                return

            if missing_cache and missing_cache.result:
                return

            frozen = cold_storage.get(_pk)

            with Pipeline(name=getattr(storage, '_db')) as p:

                s = storage(_pk, pipe=p, fields=fields)

                if frozen is None:
                    freeze_ttl = getattr(cls, 'freeze_ttl', FREEZE_TTL_DEFAULT)
                    p.set(frozen_key_cache, '1')
                    p.expire(frozen_key_cache, freeze_ttl - 1)
                    p.execute()
                    return

                s.restore(frozen)
                rr = s.hmget(fields)
                p.on_execute(lambda: ref.load_(rr.result))
                p.execute()
                cold_storage.delete(_pk)

        pipe.on_execute(set_data)

    @classmethod
    def save(cls, instance, pipe=None, full=False):
        storage = getattr(cls, 'storage')
        with Pipeline(pipe=pipe, name=getattr(storage, '_db'), autoexec=True) as p:
            res = super(RedisColdStorageObject, cls).save(instance, pipe=p,
                                                          full=full)
            if res != 0:
                s = storage(instance.primary_key(), pipe=p)
                p.delete('%s__xx' % s.key)
            return res

    @classmethod
    def freeze(cls, *ids):
        cold_storage = getattr(cls, 'coldstorage')

        ids = [k for k in ids if not cls.is_hot_key(k)]

        if not ids:
            return 0

        p = Pipeline()
        storage = getattr(cls, 'storage')

        freeze_ttl = getattr(cls, 'freeze_ttl', FREEZE_TTL_DEFAULT)

        def dump(k):
            s = storage(k, pipe=p)
            s.expire(freeze_ttl)
            return s.dump()

        results = {k: dump(k) for k in ids}
        try:
            p.execute()
            results = {k: res.result for k, res in results.items() if
                       res.result is not None}
            if results:
                cold_storage.set_multi(results)
        except (Exception, KeyboardInterrupt, SystemExit):
            # if anything at all goes wrong, make sure to clear the TTL on
            # any objects
            # we were trying to freeze
            p = Pipeline()
            map(lambda k: storage(k, pipe=p).persist(), ids)
            p.execute()
            raise

        return len(ids)

    @classmethod
    def thaw(cls, *ids):
        cold_storage = getattr(cls, 'coldstorage')
        p = Pipeline()
        storage = getattr(cls, 'storage')
        for k, v in cold_storage.get_multi(ids).items():
            if v is None:
                continue
            s = storage(k, pipe=p)
            s.persist()
            s.restore(v)
        p.execute()
        cold_storage.delete_multi(ids)
