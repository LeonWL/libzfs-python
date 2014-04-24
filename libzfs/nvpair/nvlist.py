from enum import IntEnum
from .bindings import c_libnvpair, ffi_libnvpair, data_type

from ..general import boolean_t


LOOKUP_DEFAULT = object()


class NVListFlags(IntEnum):
    UNIQUE_NAME = 1
    UNIQUE_NAME_TYPE = 2


class UnknownValue(Exception):
    pass


class NVList(object):
    def __init__(self, flags=NVListFlags.UNIQUE_NAME, handle=None, alloc=True, free=True):
        self._flags = flags
        self._handle = handle
        self._alloc = alloc
        self._free = free

    def alloc(self):
        if self._handle is None:
            self._handle = ffi_libnvpair.new('nvlist_t **')
        if self._alloc:
            return c_libnvpair.nvlist_alloc(self._handle, int(self._flags), 0)

    __enter__ = alloc

    def free(self, exc_type = None, exc_val = None, exc_tb = None):
        if self._handle and self._free:
            c_libnvpair.nvlist_free(self.ptr)
            self._handle = None

    __exit__ = free

    @property
    def ptr(self):
        if self._handle:
            return self._handle[0]
        return None

    @property
    def handle(self):
        return self._handle

    @classmethod
    def from_nvlist_ptr(cls, ptr, **kwargs):
        nvlist = ffi_libnvpair.new('nvlist_t **')
        nvlist[0] = ptr
        return cls.from_nvlist_handle(nvlist, **kwargs)  # We're already allocated

    @classmethod
    def from_nvlist_handle(cls, hdl, **kwargs):
        kwargs.setdefault('alloc', False)
        return cls(handle=hdl, **kwargs)

    def update(self, arg=None, **kwargs):
        if arg:
            if hasattr(arg, 'keys'):
                for k in arg:
                    self.add(k, arg[k])
            else:
                for k, v in arg:
                    self.add(k, v)
        for k, v in kwargs.items():
            self.add(k, v)

    @classmethod
    def info_for_type(cls, type):
        info = NVLIST_HANDLERS.get(type)
        if info is None:
            raise UnknownValue("Unknown type: '%r'" % type)
        return info

    def add(self, key, type, value):
        info = self.info_for_type(type)
        value = info.convert_add(value)
        return not bool(info.nvlist_add(self.ptr, key, value))

    def lookup(self, key, type, default=LOOKUP_DEFAULT):
        info = self.info_for_type(type)

        holder = info.create_holder()
        val = info.nvlist_lookup(self.ptr, key, holder)
        if not bool(val):
            return info.convert(holder)
        elif default is not LOOKUP_DEFAULT:
            return default
        raise KeyError(key)

    def lookup_type(self, key):
        holder = ffi_libnvpair.new('nvpair_t **')
        val = c_libnvpair.nvlist_lookup_nvpair(self.ptr, key, holder)
        if bool(val):
            raise KeyError(key)
        typeid = c_libnvpair.nvpair_type(holder[0])
        try:
            dt = data_type(typeid)
        except ValueError:
            raise UnknownValue("Unknown type: '%r'" % typeid)
        return dt

    def lookup_smart(self, key, default=LOOKUP_DEFAULT):
        holder = ffi_libnvpair.new('nvpair_t **')
        val = c_libnvpair.nvlist_lookup_nvpair(self.ptr, key, holder)
        if bool(val):
            if default is LOOKUP_DEFAULT:
                raise KeyError(key)
            else:
                return default
        typeid = c_libnvpair.nvpair_type(holder[0])
        try:
            dt = data_type(typeid)
        except ValueError:
            raise UnknownValue("Unknown type: '%r'" % typeid)
        info = self.info_for_type(dt)

        valholder = info.create_holder()
        countholder = None
        if info.is_array:
            countholder = info.create_count_holder()
            val = info.nvpair_value(holder[0], valholder, countholder)
        else:
            val = info.nvpair_value(holder[0], valholder)
        if not bool(val):
            return info.convert(valholder, countholder)
        elif default is not LOOKUP_DEFAULT:
            return default
        raise KeyError(key)

    def dump(self):
        return c_libnvpair.dump_nvlist(self.ptr, 0)

    def to_dict(self, skip_unknown = False, deep = True):
        data = {}
        pair = c_libnvpair.nvlist_next_nvpair(self.ptr, ffi_libnvpair.NULL)
        while pair != ffi_libnvpair.NULL:
            name = ffi_libnvpair.string(c_libnvpair.nvpair_name(pair))
            typeid = c_libnvpair.nvpair_type(pair)
            try:
                dt = data_type(typeid)
                info = self.info_for_type(dt)
            except (ValueError, UnknownValue):
                if not skip_unknown:
                    raise UnknownValue("Unknown type: '%r'" % typeid)
                else:
                    try:
                        dt = data_type(typeid)
                    except:
                        dt = (None, typeid, None)
                    data[name] = dt
                    pair = c_libnvpair.nvlist_next_nvpair(self.ptr, pair)
                    continue
            valholder = info.create_holder()
            countholder = None
            if info.is_array:
                countholder = info.create_count_holder()
                val = info.nvpair_value(pair, valholder, countholder)
            else:
                val = info.nvpair_value(pair, valholder)
            if not bool(val):
                value = info.convert(valholder, countholder)
                if deep and isinstance(value, NVList):
                    value._free = self._free
                    with value:
                        data[name] = value.to_dict(skip_unknown = skip_unknown)
                elif deep and isinstance(value, list) and isinstance(value[0], NVList):
                    temp = data[name] = []
                    for item in value:
                        item._free = self._free
                        with item:
                            temp.append(item.to_dict(skip_unknown = skip_unknown))
                else:
                    data[name] = value

            pair = c_libnvpair.nvlist_next_nvpair(self.ptr, pair)
        return data


def _to_int(hdl):
    if isinstance(hdl, (int, long)):
        return int(hdl)
    return int(hdl[0])


def _to_long(hdl):
    if isinstance(hdl, (int, long)):
        return long(hdl)
    return long(hdl[0])


class NVListHandler(object):
    def __init__(self, funcname, typename, converter, add_converter = None, is_array = False):
        self._funcname = funcname
        self._typename = typename
        self._converter = converter
        self._add_converter = add_converter
        self._is_array = is_array

    def create_holder(self):
        return ffi_libnvpair.new(self._typename)

    def create_count_holder(self):
        return ffi_libnvpair.new('uint_t *')

    def convert(self, x, count = None):
        if self._converter:
            if self.is_array:
                return self._converter(x, count)
            return self._converter(x)
        return x

    def convert_add(self, x):
        if callable(self._add_converter):
            return self._add_converter(x)
        if self._add_converter is False:
            raise Exception("Unable to convert type")
        return x

    def _get_c_func(self, prefix):
        return getattr(c_libnvpair, '%s_%s' % (prefix, self._funcname))

    @property
    def nvlist_add(self):
        return self._get_c_func('nvlist_add')

    @property
    def nvlist_lookup(self):
        return self._get_c_func('nvlist_lookup')

    @property
    def nvpair_value(self):
        return self._get_c_func('nvpair_value')

    @property
    def is_array(self):
        return self._is_array


def _array_converter(converter):
    def _inner(x, count):
        items = []
        for i in range(count[0]):
            items.append(converter(x[0][i]))
        return items
    return _inner


#
# Key: configuration
#  - add func
#  - lookup func
#  - lookup holder type
#  - add converter
#  - lookup converter
#
NVLIST_HANDLERS = {
    data_type.BOOLEAN:      NVListHandler('boolean_value', 'boolean_t *', lambda x: bool(x[0]), boolean_t),
    data_type.BOOLEAN_VALUE: NVListHandler('boolean_value', 'boolean_t *', lambda x: bool(x[0]), boolean_t),
    data_type.BYTE:         NVListHandler('byte', 'uchar_t *', _to_int, None),
    data_type.INT8:         NVListHandler('int8', 'int8_t *', _to_int, None),
    data_type.UINT8:        NVListHandler('uint8', 'uint8_t *', _to_int, None),
    data_type.INT16:        NVListHandler('int16', 'int16_t *', _to_int, None),
    data_type.UINT16:       NVListHandler('uint16', 'uint16_t *', _to_int, None),
    data_type.INT32:        NVListHandler('int32', 'int32_t *', _to_int, None),
    data_type.UINT32:       NVListHandler('uint32', 'uint32_t *', _to_int, None),
    data_type.INT64:        NVListHandler('int64', 'int64_t *', _to_int, None),
    data_type.UINT64:       NVListHandler('uint64', 'uint64_t *', _to_int, None),
    data_type.STRING:       NVListHandler('string', 'char **', lambda x: ffi_libnvpair.string(x[0]), None),
    data_type.NVLIST:       NVListHandler('nvlist', 'nvlist_t **', NVList.from_nvlist_handle, False),

    data_type.BYTE_ARRAY:   NVListHandler('byte_array', 'uchar_t **', _array_converter(_to_int), None),
    data_type.INT8_ARRAY:   NVListHandler('int8_array', 'int8_t **', _array_converter(_to_int), False, True),
    data_type.UINT8_ARRAY:  NVListHandler('uint8_array', 'uint8_t **', _array_converter(_to_int), False, True),
    data_type.INT16_ARRAY:  NVListHandler('int16_array', 'int16_t **', _array_converter(_to_int), False, True),
    data_type.UINT16_ARRAY: NVListHandler('uint16_array', 'uint16_t **', _array_converter(_to_int), False, True),
    data_type.INT32_ARRAY:  NVListHandler('int32_array', 'int32_t **', _array_converter(_to_int), False, True),
    data_type.UINT32_ARRAY: NVListHandler('uint32_array', 'uint32_t **', _array_converter(_to_int), False, True),
    data_type.INT64_ARRAY:  NVListHandler('int64_array', 'int64_t **', _array_converter(_to_int), False, True),
    data_type.UINT64_ARRAY: NVListHandler('uint64_array', 'uint64_t **', _array_converter(_to_int), False, True),
    data_type.NVLIST_ARRAY: NVListHandler('nvlist_array', 'nvlist_t ***',
                                        _array_converter(NVList.from_nvlist_ptr), False, True),
    data_type.STRING_ARRAY: NVListHandler('string_array', 'char ***', 
                                        _array_converter(lambda x: ffi_libnvpair.string(x)), False, True),
}
