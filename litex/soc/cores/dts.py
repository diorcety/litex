import inspect
import logging
import textwrap

from typing import Iterable
from collections import OrderedDict
from enum import Enum

from litex.soc.interconnect.csr import CSRConstant

logger = logging.getLogger("dts")

def _find_defining_class(obj_or_cls, method_name):
    """Return the class in the MRO that defines `method_name` for `obj_or_cls`."""
    cls = obj_or_cls if isinstance(obj_or_cls, type) else obj_or_cls.__class__
    method = getattr(cls, method_name, None)
    if method is None:
        return None

    for base in inspect.getmro(cls):
        if method_name in base.__dict__:
            return base
    return None

_LINUX_DTS_COMPATIBLES = {}

class AutoRegisterDTSClass(type):
    """Metaclass that automatically registers classes that provide a LINUX_DTS_COMPATIBLE."""
    def __new__(mcs, name, bases, namespace):
        cls = super().__new__(mcs, name, bases, namespace)
        if cls.LINUX_DTS_COMPATIBLE is not None:
            _LINUX_DTS_COMPATIBLES[cls.LINUX_DTS_COMPATIBLE] = _find_defining_class(cls, cls.LINUX_DTS_COMPATIBLE_FCT)
        return cls


class DTSBase(metaclass=AutoRegisterDTSClass):
    """Base class for all device tree nodes representing the hardware abstraction layer."""
    LINUX_DTS_COMPATIBLE = None
    LINUX_DTS_COMPATIBLE_SUFFIX = "linux_dts_compatible"
    LINUX_DTS_COMPATIBLE_FCT = "linux_dts"

    def __init__(self):
        super().__init__()
        if self.LINUX_DTS_COMPATIBLE is None:
            raise NotImplementedError("This class does not support Linux-compatible device tree nodes.")
        setattr(self, self.LINUX_DTS_COMPATIBLE_SUFFIX, CSRConstant(self.LINUX_DTS_COMPATIBLE, name=self.LINUX_DTS_COMPATIBLE_SUFFIX, string=True))

    @classmethod
    def init_interrupts(cls, name, d, node, *extra_parameters):
        interrupt_constant_name = f"{name}_interrupt"
        if interrupt_constant_name in d["constants"]:
            node.entries["interrupts"] = (d["constants"][interrupt_constant_name], *extra_parameters)
            return True
        return False

class HexInt(int):
    """Wrapper for integer values to represent them in hexadecimal format when needed."""
    def __str__(self):
        return f"0x{self:x}"

class HexBytes(bytes):
    """Wrapper for strings to represent them in hexadecimal format when needed."""
    def __str__(self):
        return " ".join(f"{b:02X}" for b in self)

def _value_to_dts(value, encapsulate=True):
    """Convert Python values to their DTS string representation."""
    if value is None:
        return None
    elif isinstance(value, DTSNode):
        assert value.label is not None
        result = f"&{value.label}"
    elif isinstance(value, int):
        result = f"{value}"
    elif isinstance(value, tuple):
        result = ' '.join([_value_to_dts(x, False) for x in value])
    elif isinstance(value, bytes):
        result = f"[{value}]"
        encapsulate = False
    elif isinstance(value, str):
        result = f'"{value}"'
        encapsulate = False
    elif isinstance(value, Iterable):
        result = ", ".join([_value_to_dts(x, True) for x in value])
        encapsulate = False
    else:
        raise Exception(f"Unsupported value type for DTS: {type(value)}")
    return f"<{result}>" if encapsulate else result

def _entries_to_dts(**entries):
    # Convert a dict of DTS properties to a DTS formatted block.
    lines = []
    for name, value in entries.items():
        value = _value_to_dts(value)
        if value is None:
            line = name
        else:
            line = f"{name} = {value}"
        lines.append(line)
    # Force to add ending ;\n
    if len(lines) > 0:
        lines.append("")
    return ";\n".join(lines)

def _get_references(value, cls, visited=None):
    if visited is None:
        visited = set()

    if id(value) in visited:
        return []
    visited.add(id(value))

    results = []
    if isinstance(value, cls):
        results.append(value)
    elif isinstance(value, (str, bytes)):
        pass
    elif isinstance(value, Iterable):
        for x in value:
            results.extend(_get_references(x, cls, visited))
    else:
        raise Exception(f"Unsupported value type for references: {type(value)}")

    return results

class DTSRegMode(Enum):
    CUSTOM = 0
    ONE_REG = 1
    MULTI_REG = 2
    MEMORY = 3

def dts_regs_entries(name, d, reg_mode=DTSRegMode.ONE_REG):
    if reg_mode == DTSRegMode.MEMORY:
        assert name in d["memories"]
        return OrderedDict([("reg", (HexInt(d["memories"][name]["base"]), HexInt(d["memories"][name]["size"])))])
    elif reg_mode != DTSRegMode.CUSTOM:
        reg = []
        reg_names = []

        prefix = name + "_"
        for n, v in d["csr_registers"].items():
            if not n.startswith(prefix):
                continue
            reg_name = n[len(prefix):]
            addr = HexInt(v['addr'])
            size = HexInt(v['size'])

            reg.append((addr, size))
            reg_names.append(reg_name)

        assert len(reg) > 0, f"Missing csr_register entry for {name}"
        if reg_mode == DTSRegMode.ONE_REG:
            start = min([x[0] for x in reg])
            end = max([x[0]+x[1] for x in reg])
            return OrderedDict([("reg", (HexInt(start), HexInt(end-start)))])
        else:
            return OrderedDict([("reg", reg), ("reg-names", reg_names)])

    return {}

class DTSNode():
    """Represents a node within the device tree, capable of rendering itself"""
    def __init__(self, name, label=None, cls=None, addr=None, reg_mode=DTSRegMode.ONE_REG, csr_name=None, other=()):
        super().__init__()
        self.entries = OrderedDict(other)
        self.name = name
        self.label = label
        self.csr_name = csr_name
        if self.label is not None and self.csr_name is None:
            self.csr_name = self.label
        if self.csr_name is None:
            self.csr_name = self.name
        self.addr = addr
        self.cls = cls
        self.reg_mode = reg_mode
        self.aliases = []
        self._childs = []

        # Extra DTS data
        self.header = ""
        self.footer = ""

    @property
    def cls(self):
        return self.entries.get("compatible", None)

    @cls.setter
    def cls(self, value):
        if value is not None:
            if isinstance(value, str):
                self.entries["compatible"] = value
            elif isinstance(value, AutoRegisterDTSClass):
               self.entries["compatible"] = value.LINUX_DTS_COMPATIBLE
            else:
                raise RuntimeError(f"Unsupported cls type: {type(value)}")
        elif "compatible" in self.entries:
            self.entries.pop("compatible")

    @property
    def childs(self):
        return self._childs

    @property
    def dependencies(self):
        return _get_references(self.entries)

    def populate(self, d):
        # Fill node entries like 'reg' and determine address from CSR bases if missing.
        if self.csr_name is not None:
            if "reg" not in self.entries:
                self.entries.update(dts_regs_entries(self.csr_name, d, self.reg_mode))
            if self.addr is None:
                if self.reg_mode == DTSRegMode.MEMORY:
                    self.addr = d["memories"][self.csr_name]["base"]
                else:
                    self.addr = d["csr_bases"].get(self.csr_name, None)
        if self.addr is not None:
            self.entries["status"] = "okay"
        for child in self.childs:
            child.populate(d)

    def _header(self):
        label = f"{self.label}: " if self.label is not None else ""
        addr = f"@{self.addr:x}" if self.addr is not None else ""
        name = self.name if self.name is not None else ""
        return f"{label}{name}{addr}"

    def generate(self, padding=" "*4):
        # Render this node and its children to a DTS node string.
        content = self.header
        content += _entries_to_dts(**self.entries)
        for child in self.childs:
            content += "\n" + child.generate(padding)
        content += self.footer
        content = textwrap.indent(content, padding)
        header = self._header()
        if len(header) > 0:
            header += " "
        return f"{header}{{\n{content}}};\n"

    def node(self, params, create=False):
        if isinstance(params, str):
            name = params
            params = (params, )
        elif isinstance(params, tuple):
            assert len(params) >= 1
            name = params[0]
            assert isinstance(name, str)
        else:
            raise RuntimeError(f"Unsupported child type: {type(params)}")

        if not create:
            found = []
            for child in self._childs:
                if child.name == name:
                    found.append(child)

            if len(found) > 0:
                assert len(found) == 1, f"\"{name}\" matches multiple nodes name"
                return found[0]

            for child in self._childs:
                if child.label == name:
                    return child

            if len(found) > 0:
                assert len(found) == 1, f"\"{name}\" matches multiple nodes label"
                return found[0]

            raise RuntimeError(f"Node name or label \"{name}\" not found")

        node = DTSNode(*params)
        self._childs.append(node)
        return node

    def __truediv__(self, params):
        return self.node(params, False)

    def __add__(self, params):
        return self.node(params, True)

    def __repr__(self):
        return self._header()

def _exc_to_str(exc):
    from traceback import format_exception
    return ''.join(format_exception(type(exc), exc, exc.__traceback__)).rstrip()

def load_nodes(d, root):
    # List all nodes
    pending = {}
    for n, v in d["constants"].items():
        if n.endswith(DTSBase.LINUX_DTS_COMPATIBLE_SUFFIX):
            suffix = "_" + DTSBase.LINUX_DTS_COMPATIBLE_SUFFIX
            name = n[:-len(suffix)]
            logger.debug(f"Found DTSNode {name}")
            assert v in _LINUX_DTS_COMPATIBLES, f"{v} is not a compatible value for {name}"
            fct = getattr(_LINUX_DTS_COMPATIBLES[v], DTSBase.LINUX_DTS_COMPATIBLE_FCT)
            pending[name] = fct

    loaded = set()
    # Generate all nodes
    while len(pending) > 0:
        loop_loaded = set()
        exceptions = {}
        for name, fct in pending.items():
            try:
                fct(name, d, root)
                loop_loaded.add(name)
                logger.debug(f"Create DTS node for {name}")
            except BaseException as e:
                exceptions[name] = e
                logger.debug(f"Can't create DTS node for {name}")
        if len(loop_loaded) == 0:
            lines = ["Unable to load the following nodes:\n"]
            for name, exc in exceptions.items():
                lines.append(f"\n===== {name} =====\n{_exc_to_str(exc)}")
            raise RuntimeError("\n".join(lines))

        for name in loop_loaded:
            pending.pop(name)
        loaded.update(loop_loaded)

def load_csr(d, root, polling=False):
    """Generate complete DTS source for a given CSR dictionary `d`."""

    # Remove constants ending with interrupt when using polling mode
    if polling:
        import copy
        d = copy.deepcopy(d)
        constants = d["constants"]
        keys_to_delete = [k for k in constants if k.endswith("_interrupt")]
        for k in keys_to_delete:
            del constants[k]

    load_nodes(d, root)
    root.populate(d)

def get_aliases(node, aliases=None):
    if aliases is None:
        aliases = {}
    for alias in node.aliases:
        assert alias not in aliases, f"Alias {alias} already used for {aliases[alias]}"
        aliases[alias] = node.name
    for child in node.childs:
        get_aliases(child, aliases)
    return aliases