from .compat import json
from decimal import Decimal
from .exceptions import InvalidFieldValue, \
    MissingField, InvalidOperation
from future.utils import PY2
import future.builtins
import re
import redpipe

__all__ = '''
Field
IntegerField
FloatField
DecimalField
StringField
StringListField
TextField
JsonField
ListField
BooleanField
'''.split()

unicode = unicode if PY2 else str

_NUMERIC = (0, 0.0, Decimal('0'))

NULL = object()

_SCALAR = (str, unicode, future.builtins.str)


class Field(object):
    """
    Field objects handle data conversion to/from strings, store metadata
    about indices, etc. Note that these are "heavy" fields, in that whenever
    data is read/written, it must go through descriptor processing. This is
    primarily so that (for example) if you try to write a Decimal to a Float
    field, you get an error the moment you try to do it, not some time later
    when you try to save the object (though saving can still cause an error
    during the conversion process).

    Standard Arguments:

        * *required* - determines whether this field is required on
          creation
        * *default* - a default value (either a callable or a simple value)
          when this field is not provided
        * *unique* - can only be enabled on ``String`` fields, allows for
          required distinct field values (like an email address on a User
          model)

    Notes:

        * Fields with 'unique' set to True can only be string fields
        * You can only have one unique field on any model
        * If you set required to True, then you must have the field set
          during object construction: ``MyModel(col=val)``
    """
    _allowed = ()

    __slots__ = 'primary required default model convert attr'.split()

    _redpipe_equivalent = redpipe.TextField

    def __init__(self, required=False, default=NULL, primary=False):
        self.primary = primary
        self.required = required
        self.default = default
        self.model = None
        self.attr = None

    def from_persistence(self, value):
        convert = self._allowed[0] if \
            isinstance(self._allowed, (tuple, list)) else self._allowed
        return convert(value)

    def to_persistence(self, value):
        if isinstance(value, int):
            return str(value)
        return repr(value)

    def _is_allowed(self, value):
        if value is None:
            return True
        allowed = self._allowed if \
            isinstance(self._allowed, (tuple, list)) else [self._allowed]

        for a in allowed:
            if isinstance(value, a):
                return True

        return False

    def validate(self, value, from_persistence=False):
        if value is None:
            if self.required:
                raise InvalidFieldValue('%s.%s is required' %
                                        (self.model, self.attr))
            return

        allowed = self._allowed if \
            isinstance(self._allowed, (tuple, list)) else [self._allowed]

        if self._is_allowed(value):
            return
        elif not from_persistence:
            try:
                val = self.from_persistence(value)
                return self.validate(val, True)
            except (ValueError, TypeError) as e:
                raise InvalidFieldValue(*e.args)

        raise InvalidFieldValue("%s.%s has type %r but must be of type %r" % (
            self.model, self.attr, type(value), allowed))

    def _init_(self, obj, value, loading):
        # You shouldn't be calling this directly, but this is what sets up all
        # of the necessary pieces when creating an entity from scratch, or
        # loading the entity from persistence layer.
        model = self.model
        attr = self.attr
        if value is None:
            default = self.default
            if default is NULL:
                if self.required:
                    raise MissingField(
                        "%s.%s cannot be missing" % (model, attr)
                    )
            elif callable(default):
                # noinspection PyCallingNonCallable
                value = default()
            else:
                value = self.default
        elif not self._is_allowed(value):
            try:
                value = self.from_persistence(value)
            except (ValueError, TypeError) as e:
                raise InvalidFieldValue(*e.args)

        if not loading:
            self.validate(value)
            if value is not None:
                obj._dirty.add(attr)

        if isinstance(value, (dict, list)):
            value = json.loads(json.dumps(value))

        obj._data[attr] = value

    def __set__(self, obj, value):
        initialized = getattr(obj, '_init', False)

        if not initialized:
            loading = not getattr(obj, '_new', False)
            self._init_(obj, value, loading)
            return

        if self.primary:
            raise InvalidOperation("Cannot update primary key value")

        if value is None:
            return self.__delete__(obj)

        self.validate(value)
        data = obj._data
        attr = self.attr
        if data.get(attr, None) != value:
            obj._dirty.add(attr)
        data[attr] = value

    def __get__(self, obj, _):
        try:
            return obj._data[self.attr]
        except KeyError:
            AttributeError("%s.%s does not exist" % (self.model, self.attr))

    def __delete__(self, obj):
        attr = self.attr
        if self.required:
            raise InvalidOperation(
                "%s.%s cannot be null" % (self.model, attr)
            )
        try:
            obj._data.pop(attr)
            obj._dirty.add(attr)
        except KeyError:
            raise AttributeError(
                "%s.%s does not exist" % (self.model, attr)
            )


class BooleanField(Field):
    """
    Used for boolean fields.

    All standard arguments supported.

    All values passed in on creation are casted via bool().
    Originally thought we would want to distinguish between None and False
    cases, but it actually gets messy and I think it is better as an on/off
    switch.

    Used via::

        class MyModel(Model):
            col = Boolean()

    Queries via ``MyModel.get_by(...)`` and ``MyModel.query.filter(...)`` work
    as expected when passed ``True`` or ``False``.

    .. note: these fields are not sortable by default.
    """
    _allowed = bool

    _redpipe_equivalent = redpipe.BooleanField

    def __init__(self):
        super(BooleanField, self).__init__(default=False)

    def to_persistence(self, obj):
        return '1' if obj else None


class DecimalField(Field):
    """
    A Decimal-only numeric field (converts ints/longs into Decimals
    automatically). Attempts to assign Python float will fail.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Decimal()
    """
    _allowed = Decimal

    _redpipe_equivalent = redpipe.FloatField

    def __init__(self, required=False, default=NULL):
        """
        don't allow as primary key.
        rounding errors.
        Args:
            required:
            default:
        """
        super(DecimalField, self).__init__(required=required, default=default)

    def to_persistence(self, value):
        return value


class FloatField(Field):
    """
    Numeric field that supports integers and floats (values are turned into
    floats on load from persistence).

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Float()
    """
    _allowed = (float, int)

    _redpipe_equivalent = redpipe.FloatField

    def to_persistence(self, value):
        return value


class IntegerField(Field):
    """
    Used for integer numeric fields.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = Integer()
    """
    _allowed = int

    _redpipe_equivalent = redpipe.IntegerField

    def to_persistence(self, value):
        return int(float(value))

    def from_persistence(self, value):
        return int(float(value))


class JsonField(Field):
    """
    Allows for more complicated nested structures as attributes.

    Used via::

        class MyModel(Model):
            col = Json()
    """
    _allowed = (dict, list, tuple, set)

    _redpipe_equivalent = redpipe.DictField

    def to_persistence(self, value):
        return json.dumps(value)

    def from_persistence(self, value):
        if isinstance(value, self._allowed):
            return value
        return json.loads(value)


class ListField(JsonField):
    _allowed = list

    _redpipe_equivalent = redpipe.ListField

    def from_persistence(self, value):
        if value is None:
            return None
        if isinstance(value, self._allowed):
            return value
        try:
            return json.loads(value)
        except (ValueError, TypeError) as e:
            pass

        try:
            if len(value) > 0:
                return value.split(',')
            else:
                return None
        except (TypeError, AttributeError) as e:
            raise InvalidFieldValue(*e.args)


class StringListField(ListField):
    _allowed = list

    def to_persistence(self, value):
        return ",".join(value) if len(value) > 0 else None


class StringField(Field):
    """
    A plain string field. Trying to save unicode strings will probably result
    in an error, if not bad data.

    All standard arguments supported.

    Used via::

        class MyModel(Model):
            col = String()
    """
    _allowed = _SCALAR
    PATTERN = re.compile('^([ -~]+)?$')

    def from_persistence(self, value):
        return None if value is None else value

    def to_persistence(self, value):
        try:
            coerced = unicode(value)
            if coerced == value and self.PATTERN.match(coerced):
                return coerced
        except (UnicodeError, TypeError):
            pass

        raise InvalidFieldValue('not ascii')


class TextField(Field):
    """
    A unicode string field.

    All standard arguments supported, except for ``unique``.

    Aside from not supporting ``unique`` indices, will generally have the same
    behavior as a ``String`` field, only supporting unicode strings. Data is
    encoded via utf-8 before writing to persistence. If you would like to
    create your own field to encode/decode differently, examine the source
    find out how to do it.

    Used via::

        class MyModel(Model):
            col = Text()
    """
    _allowed = [unicode, str]

    def to_persistence(self, value):
        try:
            coerced = unicode(value)
            return coerced
        except (UnicodeError, TypeError):
            raise InvalidFieldValue('not ascii')

    def from_persistence(self, value):
        return None if value is None else value
