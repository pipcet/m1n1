# SPDX-License-Identifier: MIT
from enum import Enum
import bisect, copy, heapq, importlib, sys, itertools, time, os, functools
from construct import Adapter, Int64ul, Int32ul, Int16ul, Int8ul

__all__ = []

def align_up(v, a=16384):
    return (v + a - 1) & ~(a - 1)

align = align_up

def align_down(v, a=16384):
    return v & ~(a - 1)

def hexdump(s, sep=" "):
    return sep.join(["%02x"%x for x in s])

def hexdump32(s, sep=" "):
    vals = struct.unpack("<%dI" % (len(s)//4), s)
    return sep.join(["%08x"%x for x in vals])

def _ascii(s):
    s2 = ""
    for c in s:
        if c < 0x20 or c > 0x7e:
            s2 += "."
        else:
            s2 += chr(c)
    return s2

def chexdump(s, st=0, abbreviate=True, indent=""):
    last = None
    skip = False
    for i in range(0,len(s),16):
        val = s[i:i+16]
        if val == last and abbreviate:
            if not skip:
                print(indent+"%08x  *" % (i + st))
                skip = True
        else:
            print(indent+"%08x  %s  %s  |%s|" % (
                  i + st,
                  hexdump(val[:8], ' ').ljust(23),
                  hexdump(val[8:], ' ').ljust(23),
                  _ascii(val).ljust(16)))
            last = val
            skip = False

def chexdump32(s, st=0, abbreviate=True):
    last = None
    skip = False
    for i in range(0,len(s),32):
        val = s[i:i+32]
        if val == last and abbreviate:
            if not skip:
                print("%08x  *" % (i + st))
                skip = True
        else:
            print("%08x  %s" % (
                i + st,
                hexdump32(val, ' ')))
            last = val
            skip = False

class ReloadableMeta(type):
    def __new__(cls, name, bases, dct):
        m = super().__new__(cls, name, bases, dct)
        m._load_time = time.time()
        return m

class Reloadable(metaclass=ReloadableMeta):
    @classmethod
    def _reloadcls(cls):
        mods = []
        for c in cls.mro():
            mod = sys.modules[c.__module__]
            cur_cls = getattr(mod, c.__name__)
            mods.append((cur_cls, mod))
            if c.__name__ == "Reloadable":
                break

        reloaded = set()
        newest = 0
        for pcls, mod in mods[::-1]:
            source = getattr(mod, "__file__", None)
            if not source:
                continue
            newest = max(newest, os.stat(source).st_mtime, pcls._load_time)
            if (reloaded or pcls._load_time < newest) and mod.__name__ not in reloaded:
                print(f"Reload: {mod.__name__}")
                mod = importlib.reload(mod)
                reloaded.add(mod.__name__)

        return getattr(mods[0][1], cls.__name__)

    def _reloadme(self):
        self.__class__ = self._reloadcls()

class RegisterMeta(ReloadableMeta):
    def __new__(cls, name, bases, dct):
        m = super().__new__(cls, name, bases, dct)

        f = {}

        if bases and bases[0] is not Reloadable:
            for cls in bases[0].mro():
                if cls is Reloadable:
                    break
                f.update({k: None for k,v in cls.__dict__.items()
                          if not k.startswith("_") and isinstance(v, (int, tuple))})

        f.update({k: None for k, v in dct.items()
                 if not k.startswith("_") and isinstance(v, (int, tuple))})

        m._fields_list = list(f.keys())
        m._fields = set(f.keys())

        return m

class Register(Reloadable, metaclass=RegisterMeta):
    def __init__(self, v=0, **kwargs):
        self._value = v

        for k,v in kwargs.items():
            setattr(self, k, v)

    def __getattribute__(self, attr):
        if attr.startswith("_") or attr not in self._fields:
            return object.__getattribute__(self, attr)

        field = getattr(self.__class__, attr)
        value = self._value

        if isinstance(field, int):
            return (value >> field) & 1
        elif isinstance(field, tuple):
            if len(field) == 2:
                msb, lsb = field
                ftype = int
            else:
                msb, lsb, ftype = field
            return ftype((value >> lsb) & ((1 << ((msb + 1) - lsb)) - 1))
        else:
            raise AttributeError(f"Invalid field definition {attr} = {field!r}")

    def __setattr__(self, attr, fvalue):
        if attr.startswith("_"):
            self.__dict__[attr] = fvalue
            return

        field = getattr(self.__class__, attr)

        value = self._value

        if isinstance(field, int):
            self._value = (value & ~(1 << field)) | ((fvalue & 1) << field)
        elif isinstance(field, tuple):
            if len(field) == 2:
                msb, lsb = field
            else:
                msb, lsb, ftype = field
            mask = ((1 << ((msb + 1) - lsb)) - 1)
            self._value = (value & ~(mask << lsb)) | ((fvalue & mask) << lsb)
        else:
            raise AttributeError(f"Invalid field definition {attr} = {field!r}")

    def __int__(self):
        return self._value

    def _field_val(self, field_name, as_repr=False):
        field = getattr(self.__class__, field_name)
        val = getattr(self, field_name)
        if isinstance(val, Enum):
            if as_repr:
                return str(val)
            else:
                msb, lsb = field[:2]
                if (msb - lsb + 1) > 3:
                    return f"0x{val.value:x}({val.name})"
                else:
                    return f"{val.value}({val.name})"
        elif not isinstance(val, int):
            return val
        elif isinstance(field, int):
            return val
        elif isinstance(field, tuple):
            msb, lsb = field[:2]
            if (msb - lsb + 1) > 3:
                return f"0x{val:x}"

        return val

    def str_fields(self):
        return f"{', '.join(f'{k}={self._field_val(k)}' for k in self._fields_list)}"

    def __str__(self):
        return f"0x{self._value:x} ({self.str_fields()})"

    def __repr__(self):
        return f"{type(self).__name__}({', '.join(f'{k}={self._field_val(k, True)}' for k in self._fields_list)})"

    def copy(self):
        return type(self)(self._value)

    @property
    def value(self):
        return self._value
    @value.setter
    def value(self, val):
        self._value = val

class Register8(Register):
    __WIDTH__ = 8

class Register16(Register):
    __WIDTH__ = 16

class Register32(Register):
    __WIDTH__ = 32

class Register64(Register):
    __WIDTH__ = 64

class RegAdapter(Adapter):
    def __init__(self, register):
        if register.__WIDTH__ == 64:
            subcon = Int64ul
        elif register.__WIDTH__ == 32:
            subcon = Int32ul
        elif register.__WIDTH__ == 16:
            subcon = Int16ul
        elif register.__WIDTH__ == 8:
            subcon = Int8ul
        else:
            raise ValueError("Invalid reg width")

        self.reg = register
        super().__init__(subcon)

    def _decode(self, obj, context, path):
        return self.reg(obj)

    def _encode(self, obj, context, path):
        return obj.value

class RangeMap(Reloadable):
    def __init__(self):
        self.__start = []
        self.__end = []
        self.__value = []

    def __len__(self):
        return len(self.__start)

    def __nonzero__(self):
        return bool(self.__start)

    def __contains(self, pos, addr):
        if pos < 0 or pos >= len(self.__start):
            return False

        return self.__start[pos] <= addr and addr <= self.__end[pos]

    def __split(self, pos, addr):
        self.__start.insert(pos + 1, addr)
        self.__end.insert(pos, addr - 1)
        self.__value.insert(pos + 1, copy.copy(self.__value[pos]))

    def __zone(self, zone):
        if isinstance(zone, slice):
            zone = range(zone.start if zone.start is not None else 0,
                         zone.stop if zone.stop is not None else 1 << 64)
        elif isinstance(zone, int):
            zone = range(zone, zone + 1)

        return zone

    def lookup(self, addr, default=None):
        addr = int(addr)

        pos = bisect.bisect_left(self.__end, addr)
        if self.__contains(pos, addr):
            return self.__value[pos]
        else:
            return default

    def __iter__(self):
        return self.ranges()

    def ranges(self):
        return (range(s, e + 1) for s, e in zip(self.__start, self.__end))

    def items(self):
        return ((range(s, e + 1), v) for s, e, v in zip(self.__start, self.__end, self.__value))

    def _overlap_range(self, zone, split=False):
        zone = self.__zone(zone)
        if len(zone) == 0:
            return 0, 0

        start = bisect.bisect_left(self.__end, zone.start)

        if split:
            # Handle left-side overlap
            if self.__contains(start, zone.start) and self.__start[start] != zone.start:
                self.__split(start, zone.start)
                start += 1
                assert self.__start[start] == zone.start

        for pos in range(start, len(self.__start)):
            if self.__start[pos] >= zone.stop:
                return start, pos
            if split and (self.__end[pos] + 1) > zone.stop:
                self.__split(pos, zone.stop)
                return start, pos + 1

        return start, len(self.__start)

    def populate(self, zone, default=[]):
        zone = self.__zone(zone)
        if len(zone) == 0:
            return

        start, stop = zone.start, zone.stop

        # Starting insertion point, overlap inclusive
        pos = bisect.bisect_left(self.__end, zone.start)

        # Handle left-side overlap
        if self.__contains(pos, zone.start) and self.__start[pos] != zone.start:
            self.__split(pos, zone.start)
            pos += 1
            assert self.__start[pos] == zone.start

        # Iterate through overlapping ranges
        while start < stop:
            if pos == len(self.__start):
                # Append to end
                val = copy.copy(default)
                self.__start.append(start)
                self.__end.append(stop - 1)
                self.__value.append(val)
                yield range(start, stop), val
                break

            assert self.__start[pos] >= start
            if self.__start[pos] > start:
                # Insert new range
                boundary = stop
                if pos < len(self.__start):
                    boundary = min(stop, self.__start[pos])
                val = copy.copy(default)
                self.__start.insert(pos, start)
                self.__end.insert(pos, boundary - 1)
                self.__value.insert(pos, val)
                yield range(start, boundary), val
                start = boundary
            else:
                # Handle right-side overlap
                if self.__end[pos] > stop - 1:
                    self.__split(pos, stop)
                # Add to existing range
                yield range(self.__start[pos], self.__end[pos] + 1), self.__value[pos]
                start = self.__end[pos] + 1

            pos += 1
        else:
            assert start == stop

    def overlaps(self, zone, split=False):
        start, stop = self._overlap_range(zone, split)
        for pos in range(start, stop):
            yield range(self.__start[pos], self.__end[pos] + 1), self.__value[pos]

    def replace(self, zone, val):
        start, stop = self._overlap_range(zone, True)
        self.__start = self.__start[:start] + [zone.start] + self.__start[stop:]
        self.__end = self.__end[:start] + [zone.stop - 1] + self.__end[stop:]
        self.__value = self.__value[:start] + [val] + self.__value[stop:]

    def clear(self, zone=None):
        if zone is None:
            self.__start = []
            self.__end = []
            self.__value = []
        else:
            start, stop = self._overlap_range(zone, True)
            self.__start = self.__start[:start] + self.__start[stop:]
            self.__end = self.__end[:start] + self.__end[stop:]
            self.__value = self.__value[:start] + self.__value[stop:]

    def compact(self, equal=lambda a, b: a == b, empty=lambda a: not a):
        if len(self) == 0:
            return

        new_s, new_e, new_v = [], [], []

        for pos in range(len(self)):
            s, e, v = self.__start[pos], self.__end[pos], self.__value[pos]
            if empty(v):
                continue
            if new_v and equal(last, v) and s == new_e[-1] + 1:
                new_e[-1] = e
            else:
                new_s.append(s)
                new_e.append(e)
                new_v.append(v)
                last = v

        self.__start, self.__end, self.__value = new_s, new_e, new_v

    def _assert(self, expect, val=lambda a:a):
        state = []
        for i, j, v in zip(self.__start, self.__end, self.__value):
            state.append((i, j, val(v)))
        if state != expect:
            print(f"Expected: {expect}")
            print(f"Got:      {state}")

class AddrLookup(RangeMap):
    def __str__(self):
        b = [""]
        for zone, values in self.items():
            b.append(f"{zone.start:#11x} - {zone.stop - 1:#11x}")
            if len(values) == 0:
                b.append(f" (empty range)")
            elif len(values) == 1:
                b.append(f" : {values[0][0]}\n")
            if len(values) > 1:
                b.append(f" ({len(values):d} devices)\n")
                for value, r in sorted(values, key=lambda r: r[1].start):
                    b.append(f"      {r.start:#10x} - {r.stop - 1:#8x} : {value}\n")

        return "".join(b)

    def add(self, zone, value):
        for r, values in self.populate(zone):
            values.append((value, zone))

    def remove(self, zone, value):
        for r, values in self.overlaps(zone):
            try:
                values.remove((value, zone))
            except:
                pass

    def lookup(self, addr, default='unknown'):
        maps = super().lookup(addr)
        return maps[0] if maps else (default, range(0, 1 << 64))

    def lookup_all(self, addr):
        return super().lookup(addr, [])

    def _assert(self, expect, val=lambda a:a):
        super()._assert(expect, lambda v: [i[0] for i in v])

class ScalarRangeMap(RangeMap):
    def get(self, addr, default=None):
        return self.lookup(addr, default)

    def __setitem__(self, zone, value):
        self.replace(zone, value)

    def __delitem__(self, zone):
        self.clear(zone)

    def __getitem__(self, addr):
        value = self.lookup(addr, default=KeyError)
        if value is KeyError:
            raise KeyError(f"Address {addr:#x} has no value")
        return value

class BoolRangeMap(RangeMap):
    def set(self, zone):
        self.replace(zone, True)

    def __delitem__(self, zone):
        self.clear(zone)

    def __getitem__(self, addr):
        return self.lookup(addr, False)

class DictRangeMap(RangeMap):
    def __setitem__(self, k, value):
        if not isinstance(k, tuple):
            self.replace(k, dict(value))
        else:
            zone, key = k
            for r, values in self.populate(zone, {}):
                values[key] = value

    def __delitem__(self, k):
        if not isinstance(k, tuple):
            self.clear(k)
        else:
            zone, key = k
            for r, values in self.overlaps(zone, True):
                values.pop(key, None)

    def __getitem__(self, k):
        if isinstance(k, tuple):
            addr, k = k
            values = self.lookup(addr)
            return values.get(k, None) if values else None
        else:
            values = self.lookup(k)
            return values or {}

class SetRangeMap(RangeMap):
    def add(self, zone, key):
        for r, values in self.populate(zone, set()):
            values.add(key)

    def discard(self, zone, key):
        for r, values in self.overlaps(zone, split=True):
            if values:
                values.discard(key)
    remove = discard

    def __setitem__(self, k, value):
        self.replace(k, set(value))

    def __delitem__(self, k):
        self.clear(k)

    def __getitem__(self, addr):
        values = super().lookup(addr)
        return frozenset(values) if values else frozenset()

class NdRange:
    def __init__(self, rng, min_step=1):
        if isinstance(rng, range):
            self.ranges = [rng]
        else:
            self.ranges = list(rng)
        least_step = self.ranges[0].step
        for i, rng in enumerate(self.ranges):
            if rng.step == 1:
                self.ranges[i] = range(rng.start, rng.stop, min_step)
                least_step = min_step
            else:
                assert rng.step >= min_step
                least_step = min(least_step, rng.step)
        self.start = sum(rng[0] for rng in self.ranges)
        self.stop = sum(rng[-1] for rng in self.ranges) + least_step
        self.rev = {}
        for i in itertools.product(*map(enumerate, self.ranges)):
            index = tuple(j[0] for j in i)
            addr = sum(j[1] for j in i)
            if len(self.ranges) == 1:
                index = index[0]
            self.rev[addr] = index

    def index(self, item):
        return self.rev[item]

    def __len__(self):
        return self.stop - self.start

    def __contains__(self, item):
        return item in self.rev

    def __getitem__(self, item):
        if not isinstance(item, tuple):
            assert len(self.ranges) == 1
            return self.ranges[0][item]

        assert len(self.ranges) == len(item)
        if all(isinstance(i, int) for i in item):
            return sum((i[j] for i, j in zip(self.ranges, item)))
        else:
            iters = (i[j] for i, j in zip(self.ranges, item))
            return map(sum, itertools.product(*(([i] if isinstance(i, int) else i) for i in iters)))

class RegMapMeta(ReloadableMeta):
    def __new__(cls, name, bases, dct):
        m = super().__new__(cls, name, bases, dct)
        m._addrmap = {}
        m._rngmap = SetRangeMap()
        m._namemap = {}

        for k, v in dct.items():
            if k.startswith("_") or not isinstance(v, tuple):
                continue
            addr, rtype = v

            if isinstance(addr, int):
                m._addrmap[addr] = k, rtype
            else:
                addr = NdRange(addr, rtype.__WIDTH__ // 8)
                m._rngmap.add(addr, (addr, k, rtype))

            m._namemap[k] = addr, rtype

            def prop(k):
                def getter(self):
                    return self._accessor[k]
                def setter(self, val):
                    self._accessor[k].val = val
                return property(getter, setter)

            setattr(m, k, prop(k))

        return m

class RegAccessor(Reloadable):
    def __init__(self, cls, rd, wr, addr):
        self.cls = cls
        self.rd = rd
        self.wr = wr
        self.addr = addr

    def __int__(self):
        return self.rd(self.addr)

    @property
    def val(self):
        return self.rd(self.addr)

    @val.setter
    def val(self, value):
        self.wr(self.addr, int(value))

    @property
    def reg(self):
        val = self.val
        if val is None:
            return None
        return self.cls(val)

    @reg.setter
    def reg(self, value):
        self.wr(self.addr, int(value))

    def set(self, **kwargs):
        r = self.reg
        for k, v in kwargs:
            setattr(r, k, v)
        self.wr(self.addr, int(r))

    def __str__(self):
        return str(self.reg)

class RegArrayAccessor(Reloadable):
    def __init__(self, range, cls, rd, wr, addr):
        self.range = range
        self.cls = cls
        self.rd = rd
        self.wr = wr
        self.addr = addr

    def __getitem__(self, item):
        off = self.range[item]
        if isinstance(off, int):
            return RegAccessor(self.cls, self.rd, self.wr, self.addr + off)
        else:
            return [RegAccessor(self.cls, self.rd, self.wr, self.addr + i) for i in off]

class RegMap(Reloadable, metaclass=RegMapMeta):
    def __init__(self, backend, base):
        self._base = base
        self._backend = backend
        self._accessor = {}

        for name, (addr, rcls) in self._namemap.items():
            width = rcls.__WIDTH__
            rd = functools.partial(backend.read, width=width)
            wr = functools.partial(backend.write, width=width)
            if isinstance(addr, NdRange):
                self._accessor[name] = RegArrayAccessor(addr, rcls, rd, wr, base)
            else:
                self._accessor[name] = RegAccessor(rcls, rd, wr, base + addr)

    @classmethod
    def lookup_offset(cls, offset):
        reg = cls._addrmap.get(offset, None)
        if reg is not None:
            name, rcls = reg
            return name, None, rcls
        ret = cls._rngmap[offset]
        if ret:
            for rng, name, rcls in ret:
                if offset in rng:
                    return name, rng.index(offset), rcls
        return None, None, None

    def lookup_addr(self, addr):
        return self.lookup_offset(addr - self._base)

    def get_name(self, addr):
        name, index, rcls = self.lookup_addr(addr)
        if index is not None:
            return f"{name}[{index}]"
        else:
            return name

    @classmethod
    def lookup_name(cls, name):
        return cls._namemap.get(name, None)

    def _scalar_regs(self):
        for addr, (name, rtype) in self._addrmap.items():
            yield addr, name, self._accessor[name], rtype

    def _array_reg(self, zone, map):
        addrs, name, rtype = map
        def index(addr):
            idx = addrs.index(addr)
            if isinstance(idx, tuple):
                idx = str(idx)[1:-1]
            return idx
        reg = ((addr, f"{name}[{index(addr)}]", self._accessor[name][addrs.index(addr)], rtype)
                     for addr in zone if addr in addrs)
        return reg

    def _array_regs(self):
        for zone, maps in self._rngmap.items():
            yield from heapq.merge(*(self._array_reg(zone, map) for map in maps))

    def dump_regs(self):
        for addr, name, acc, rtype in heapq.merge(sorted(self._scalar_regs()), self._array_regs()):
            print(f"{self._base:#x}+{addr:06x} {name} = {acc.reg}")

def irange(start, count, step=1):
    return range(start, start + count * step, step)

__all__.extend(k for k, v in globals().items()
               if (callable(v) or isinstance(v, type)) and v.__module__ == __name__)

if __name__ == "__main__":
    # AddrLookup test
    a = AddrLookup()
    a.add(range(0, 10), 0)
    a._assert([
        (0, 9, [0])
    ])
    a.add(range(10, 20), 1)
    a._assert([
        (0, 9, [0]), (10, 19, [1])
    ])
    a.add(range(20, 25), 2)
    a._assert([
        (0, 9, [0]), (10, 19, [1]), (20, 24, [2])
    ])
    a.add(range(30, 40), 3)
    a._assert([
        (0, 9, [0]), (10, 19, [1]), (20, 24, [2]), (30, 39, [3])
    ])
    a.add(range(0, 15), 4)
    a._assert([
        (0, 9, [0, 4]), (10, 14, [1, 4]), (15, 19, [1]), (20, 24, [2]), (30, 39, [3])
    ])
    a.add(range(0, 15), 5)
    a._assert([
        (0, 9, [0, 4, 5]), (10, 14, [1, 4, 5]), (15, 19, [1]), (20, 24, [2]), (30, 39, [3])
    ])
    a.add(range(21, 44), 6)
    a._assert([
        (0, 9, [0, 4, 5]), (10, 14, [1, 4, 5]), (15, 19, [1]), (20, 20, [2]), (21, 24, [2, 6]),
        (25, 29, [6]), (30, 39, [3, 6]), (40, 43, [6])
    ])
    a.add(range(70, 80), 7)
    a._assert([
        (0, 9, [0, 4, 5]), (10, 14, [1, 4, 5]), (15, 19, [1]), (20, 20, [2]), (21, 24, [2, 6]),
        (25, 29, [6]), (30, 39, [3, 6]), (40, 43, [6]), (70, 79, [7])
    ])
    a.add(range(0, 100), 8)
    a._assert([
        (0, 9, [0, 4, 5, 8]), (10, 14, [1, 4, 5, 8]), (15, 19, [1, 8]), (20, 20, [2, 8]),
        (21, 24, [2, 6, 8]), (25, 29, [6, 8]), (30, 39, [3, 6, 8]), (40, 43, [6, 8]),
        (44, 69, [8]), (70, 79, [7, 8]), (80, 99, [8])
    ])
    a.remove(range(21, 44), 6)
    a._assert([
        (0, 9, [0, 4, 5, 8]), (10, 14, [1, 4, 5, 8]), (15, 19, [1, 8]), (20, 20, [2, 8]),
        (21, 24, [2, 8]), (25, 29, [8]), (30, 39, [3, 8]), (40, 43, [8]),
        (44, 69, [8]), (70, 79, [7, 8]), (80, 99, [8])
    ])
    a.compact()
    a._assert([
        (0, 9, [0, 4, 5, 8]), (10, 14, [1, 4, 5, 8]), (15, 19, [1, 8]), (20, 24, [2, 8]),
        (25, 29, [8]), (30, 39, [3, 8]), (40, 69, [8]), (70, 79, [7, 8]),
        (80, 99, [8])
    ])
    a.remove(range(0, 100), 8)
    a._assert([
        (0, 9, [0, 4, 5]), (10, 14, [1, 4, 5]), (15, 19, [1]), (20, 24, [2]), (25, 29, []),
        (30, 39, [3]), (40, 69, []), (70, 79, [7]), (80, 99, [])
    ])
    a.compact()
    a._assert([
        (0, 9, [0, 4, 5]), (10, 14, [1, 4, 5]), (15, 19, [1]), (20, 24, [2]), (30, 39, [3]),
        (70, 79, [7])
    ])
    a.clear(range(12, 21))
    a._assert([
        (0, 9, [0, 4, 5]), (10, 11, [1, 4, 5]), (21, 24, [2]), (30, 39, [3]),
        (70, 79, [7])
    ])

    # ScalarRangeMap test
    a = ScalarRangeMap()
    a[0:5] = 1
    a[5:10] = 2
    a[4:8] = 3
    del a[2:4]
    expect = [1, 1, None, None, 3, 3, 3, 3, 2, 2, None]
    for i,j in enumerate(expect):
        assert a.get(i) == j
        if j is not None:
            assert a[i] == j
    try:
        a[10]
    except KeyError:
        pass
    else:
        assert False

    # DictRangeMap test
    a = DictRangeMap()
    a[0:5, 0] = 10
    a[5:8, 1] = 11
    a[4:6, 2] = 12
    del a[2:4]
    expect = [{0: 10}, {0: 10}, {}, {}, {0: 10, 2: 12}, {1: 11, 2: 12}, {1: 11}, {1: 11}, {}]
    for i,j in enumerate(expect):
        assert a[i] == j
        for k, v in j.items():
            assert a[i, k] == v

    # SetRangeMap test
    a = SetRangeMap()
    a[0:2] = {1,}
    a[2:7] = {2,}
    a.add(range(1, 4), 3)
    a.discard(0, -1)
    a.discard(3, 2)
    del a[4]
    expect = [{1,}, {1,3}, {2,3}, {3,}, set(), {2,}, {2,}, set()]
    for i,j in enumerate(expect):
        assert a[i] == j

    # BoolRangeMap test
    a = BoolRangeMap()
    a.set(range(0, 2))
    a.set(range(4, 6))
    a.clear(range(3, 5))
    expect = [True, True, False, False, False, True, False]
    for i,j in enumerate(expect):
        assert a[i] == j
