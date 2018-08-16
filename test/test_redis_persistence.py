#!/usr/bin/env python

# std-lib
import time
import unittest
import re

# test harness
from unit_test_setup import generate_uuid

# test-harness
from setup_redis import (
    hbom,
    clear_redis_testdata,
    default_redis_connection,
    skip_if_redis_disabled,
)


class Sample(hbom.RedisObject):

    class definition(hbom.Definition):
        id = hbom.StringField(primary=True, default=generate_uuid)
        a = hbom.IntegerField()
        b = hbom.IntegerField(default=7)
        req = hbom.StringField(required=True)
        created_at = hbom.FloatField(default=time.time)

    class storage(hbom.RedisHash):
        _keyspace = 'TT_s'
        _db = 'test'


@skip_if_redis_disabled
class TestRedisDefinitionPersistence(unittest.TestCase):
    def setUp(self):
        clear_redis_testdata()

    def tearDown(self):
        clear_redis_testdata()

    def test(self):
        pipe = hbom.Pipeline()
        x = Sample.definition(id='x', req='test1')
        y = Sample.new(id='y', req='test2')
        Sample.save(x, pipe=pipe)
        Sample.save(y, pipe=pipe)
        pipe.execute()

        pipe = hbom.Pipeline()
        x = Sample.get('x', pipe=pipe)
        x_ref = Sample.ref('x')
        y = Sample.ref('y', pipe=pipe)
        z = Sample.get('z', pipe=pipe)

        self.assertFalse(x.exists())
        self.assertFalse(x_ref.exists())
        self.assertFalse(y.exists())
        self.assertFalse(z.exists())
        x_ref.attach(pipe)
        pipe.execute()

        self.assertTrue(x.exists())
        self.assertTrue(x_ref.exists())
        self.assertTrue(y.exists())
        self.assertFalse(z.exists())

        x, y, z = Sample.get_multi(['x', 'y', 'z'])
        self.assertTrue(x.exists())
        self.assertTrue(y.exists())
        self.assertFalse(z.exists())

        pipe = hbom.Pipeline()
        x, y, z = Sample.get_multi(['x', 'y', 'z'], pipe=pipe)
        self.assertFalse(x.exists())
        self.assertFalse(y.exists())
        self.assertFalse(z.exists())

        pipe.execute()

        self.assertTrue(x.exists())
        self.assertTrue(y.exists())
        self.assertFalse(z.exists())

        pipe = hbom.Pipeline()
        Sample.delete('x', pipe=pipe)
        Sample.delete('y', pipe=pipe)
        pipe.execute()

        pipe = hbom.Pipeline()
        x = Sample.get('x', pipe=pipe)
        y = Sample.get('y', pipe=pipe)
        z = Sample.get('z', pipe=pipe)

        self.assertFalse(x.exists())
        self.assertFalse(y.exists())
        self.assertFalse(z.exists())

        pipe.execute()

        s = Sample.new(id='abc', b=7.123, req='hello world')
        Sample.save(s)
        s = Sample.get('abc')
        self.assertEqual(s.b, 7)
        s = Sample.new(id='123', a=75, b='7.123', req='hi mom')
        Sample.save(s)
        s = Sample.get('123')
        self.assertEqual(s.b, 7)
        self.assertEqual(s.a, 75)
        s.b = '8.456'
        Sample.save(s)
        s = Sample.get('123')
        self.assertEqual(s.b, 8)
        self.assertEqual(s.a, 75)


class ColdStorageMock(dict):

    def set(self, k, v):
        self[k] = v

    def set_multi(self, mapping):
        for k, v in mapping.items():
            self.set(k, v)

    def get_multi(self, keys):
        return {k: self.get(k) for k in keys}

    def delete(self, k):
        try:
            del self[k]
        except KeyError:
            pass

    def delete_multi(self, keys):
        for k in keys:
            self.delete(k)

    def ids(self):
        for k in self.keys():
            yield k


class Foo(hbom.RedisColdStorageObject):

    class definition(hbom.Definition):
        id = hbom.StringField(primary=True, default=generate_uuid)
        a = hbom.IntegerField()
        b = hbom.IntegerField(default=7)
        created_at = hbom.FloatField(default=time.time)

    class storage(hbom.RedisHash):
        _keyspace = 'FOO'
        _db = 'test'

    coldstorage = ColdStorageMock()

    is_hot_key = re.compile(r'^[0-9]+\.[A-Za-z0-9\-\._]+$').match


class TestRedisColdStorage(unittest.TestCase):
    def setUp(self):
        clear_redis_testdata()

    def tearDown(self):
        clear_redis_testdata()

    def test(self):
        pipe = hbom.Pipeline()
        x = Foo.definition(id='x', a=1)
        y = Foo.new(id='y', a=2)
        z = Foo.new(id='0.zz', a=3)
        Foo.save(x, pipe=pipe)
        Foo.save(y, pipe=pipe)
        Foo.save(z, pipe=pipe)
        pipe.execute()
        Foo.freeze('x', 'y', '0.zz')
        data = Foo.coldstorage.get('x')
        self.assertEqual(data, Foo.storage('x').dump())
        self.assertAlmostEqual(
            Foo.storage('x').ttl(),
            hbom.redis_backend.FREEZE_TTL_DEFAULT, places=-1)
        self.assertEqual(Foo.storage('0.zz').ttl(), -1)
        self.assertTrue(Foo.is_hot_key('0.zz'))

        z = Foo.get('0.zz')
        self.assertEqual(z.a, 3)

        Foo.thaw('x', 'y')
        self.assertTrue(Foo.storage('x').exists())
        self.assertTrue(Foo.storage('y').exists())
        self.assertEqual(Foo.get('x').a, 1)
        self.assertEqual(Foo.get('y').a, 2)
        self.assertIsNone(Foo.coldstorage.get('x'))
        self.assertIsNone(Foo.coldstorage.get('y'))

        Foo.freeze('x', 'y')

        Foo.storage('x').delete()
        x = Foo.get('x')
        self.assertEqual(x.id, 'x')
        self.assertEqual(x.a, 1)
        Foo.delete('x')
        x = Foo.get('x')
        self.assertFalse(x.exists())
        self.assertIsNone(Foo.coldstorage.get('x'))
        self.assertIsNotNone(Foo.coldstorage.get('y'))

    def test_missing_cold_key(self):
        self.assertFalse(Foo.is_hot_key('a'))

        a = Foo.get('a')
        self.assertFalse(a.exists())
        a = Foo.get('a')
        self.assertFalse(a.exists())
        self.assertEqual(default_redis_connection.get('FOO{a}__xx'), b'1')

        b = Foo.new(id='b')
        Foo.save(b)
        b = Foo.get('b')
        self.assertIsNone(default_redis_connection.get('FOO{b}__xx'))
        self.assertTrue(b.exists())
        self.assertIsNone(default_redis_connection.get('FOO{b}__xx'))

        c = Foo.new(id='c')
        Foo.save(c)
        c = Foo.ref('c')
        hbom.hydrate([c])
        self.assertTrue(c.exists())
        self.assertIsNone(default_redis_connection.get('FOO{c}__xx'))

    def test_missing_to_save_freeze(self):
        a = Foo.get('a')
        self.assertFalse(a.exists())
        self.assertEqual(default_redis_connection.get('FOO{a}__xx'), b'1')
        Foo.save(a)
        Foo.freeze('a')
        res = Foo.storage('a').ttl()
        self.assertAlmostEqual(res, hbom.redis_backend.FREEZE_TTL_DEFAULT, delta=1)
        Foo.storage('a').delete()
        res = Foo.storage('a').hgetall()
        self.assertEqual(res, {})
        a = Foo.get('a')
        self.assertTrue(a.exists())


class TestMissingToSaveTestCase(unittest.TestCase):
    def setUp(self):
        clear_redis_testdata()

    def tearDown(self):
        clear_redis_testdata()

    def test_main(self):
        pk = generate_uuid()
        s = Sample.get(pk)
        self.assertFalse(s.exists())
        ts = time.time()
        s.created_at = ts
        s.req = 'test'
        changes = Sample.save(s)
        self.assertEqual(changes, 3)
        s = Sample.get(pk)
        self.assertEqual(s.exists(), True)
        self.assertEqual(s.id, pk)
        self.assertEqual(s.req, 'test')
        self.assertEqual(s.created_at, ts)

    def test_required(self):
        pk = generate_uuid()
        s = Sample.get(pk)
        self.assertEqual(s.exists(), False)
        ts = time.time()
        s.created_at = ts
        self.assertRaises(hbom.MissingField, lambda: Sample.save(s))

if __name__ == '__main__':
    unittest.main()
