#
# This file is part of LiteX.
#
# Copyright (c) 2013-2015 Sebastien Bourdeauducq <sb@m-labs.hk>
# Copyright (c) 2019-2020 Florent Kermarrec <florent@enjoy-digital.fr>
# SPDX-License-Identifier: BSD-2-Clause

from migen import *
from migen.genlib.cdc import MultiReg

from litex.gen import *

from litex.soc.cores.dts import DTSBase, DTSRegMode

from litex.soc.interconnect.csr import *
from litex.soc.interconnect.csr_eventmanager import *

# Helpers ------------------------------------------------------------------------------------------

def _to_signal(obj):
    return obj.raw_bits() if isinstance(obj, Record) else obj


class _GPIODTS(DTSBase):
    LINUX_DTS_COMPATIBLE = "litex,gpio"

    @classmethod
    def linux_dts(cls, name, d, root):
        node = root / "soc" + ("gpio", name, cls)
        node.reg_mode = DTSRegMode.ONE_REG
        node.entries["#address-cells"] = 0
        node.entries["gpio-controller"] = None
        node.entries["#gpio-cells"] = 2
        ngpio_in, ngpio_out = d["constants"].get(f"{name}_in_ngpio", 0), d["constants"].get(f"{name}_out_ngpio", 0)
        assert ngpio_in == 0 or ngpio_out == 0
        node.entries["litex,direction"] = "in" if ngpio_in > 0 else "out"
        node.entries["litex,ngpio"] = max(ngpio_in, ngpio_out)

        # Interrupt part
        if cls.init_interrupts(name, d, node):
            node.entries["interrupt-controller"] = None
            node.entries["#interrupt-cells"] = 2


class _GPIOIRQ(LiteXModule):
    def add_irq(self, in_pads):
        self._mode = CSRStorage(len(in_pads), description="GPIO IRQ Mode: 0: Edge, 1: Change.")
        self._edge = CSRStorage(len(in_pads), description="GPIO IRQ Edge (when in Edge mode): 0: Rising Edge, 1: Falling Edge.")

        # # #

        self.ev = EventManager()
        for n in range(len(in_pads)):
            in_pads_n_d = Signal()
            self.sync += in_pads_n_d.eq(in_pads[n])
            esp = EventSourceProcess(name=f"i{n}", edge="rising")
            self.comb += [
                # Change mode.
                If(self._mode.storage[n],
                    esp.trigger.eq(in_pads[n] ^ in_pads_n_d)
                # Edge mode.
                ).Else(
                    esp.trigger.eq(in_pads[n] ^ self._edge.storage[n])
                )
            ]
            setattr(self.ev, f"i{n}", esp)
        self.ev.finalize()

# GPIO Input ---------------------------------------------------------------------------------------

class GPIOIn(_GPIOIRQ, _GPIODTS):
    def __init__(self, pads, with_irq=False):
        super().__init__()
        pads = _to_signal(pads)
        self._in = CSRStatus(len(pads), description="GPIO Input(s) Status.")
        self._in_ngpio  = CSRConstant(len(pads))
        self.specials += MultiReg(pads, self._in.status)
        if with_irq:
            self.add_irq(self._in.status)

# GPIO Output --------------------------------------------------------------------------------------

class GPIOOut(LiteXModule, _GPIODTS):
    def __init__(self, pads, reset=0):
        super().__init__()
        pads = _to_signal(pads)
        self._out = CSRStorage(len(pads), reset=reset, description="GPIO Output(s) Control.")
        self._out_ngpio  = CSRConstant(len(pads))
        self.comb += pads.eq(self._out.storage)

# GPIO Input/Output --------------------------------------------------------------------------------

class GPIOInOut(LiteXModule):
    def __init__(self, in_pads, out_pads, with_irq=False):
        super().__init__()
        self._in  = GPIOIn(in_pads, with_irq)
        if self.gpio_in and with_irq:
            self.ev = self.gpio_in.ev
        self._out = GPIOOut(out_pads)

# GPIO Tristate ------------------------------------------------------------------------------------

class GPIOTristate(_GPIOIRQ):
    def __init__(self, pads, with_irq=False):
        super().__init__()
        internal = not (hasattr(pads, "o") and hasattr(pads, "oe") and hasattr(pads, "i"))
        nbits    = len(pads) if internal else len(pads.o)

        self._oe  = CSRStorage(nbits, description="GPIO Tristate(s) Control.")
        self._in  = CSRStatus(nbits,  description="GPIO Input(s) Status.")
        self._in_ngpio  = CSRConstant(nbits)
        self._out = CSRStorage(nbits, description="GPIO Ouptut(s) Control.")
        self._out_ngpio  = CSRConstant(nbits)

        # # #

        # Internal Tristate.
        if internal:
            if isinstance(pads, Record):
                pads = pads.flatten()
            # Proper inout IOs.
            for i in range(nbits):
                t = TSTriple()
                self.specials += t.get_tristate(pads[i])
                self.comb += t.oe.eq(self._oe.storage[i])
                self.comb += t.o.eq(self._out.storage[i])
                self.specials += MultiReg(t.i, self._in.status[i])

        # External Tristate.
        else:
            # Tristate inout IOs (For external tristate IO chips or simulation).
            for i in range(nbits):
                self.comb += pads.oe[i].eq(self._oe.storage[i])
                self.comb += pads.o[i].eq(self._out.storage[i])
                self.specials += MultiReg(pads.i[i], self._in.status[i])

        if with_irq:
            self.add_irq(self._in.status)
