
import json
import unittest

from litex.gen.common import Record
from litex.soc.cores.dts import _LINUX_DTS_COMPATIBLES, DTSNode, DTSRegMode, load_csr
from litex.soc.cores.gpio import _GPIODTS, GPIOIn


class TestDTS(unittest.TestCase):
    def __init__(self, methodName = "runTest"):
        super().__init__(methodName)
        self.maxDiff = None
    def test_get_dts_class(self):
        pads = Record([("a", 1), ("b", 1), ("c", 1), ("d", 1)])
        module = GPIOIn(pads, True)
        self.assertTrue(hasattr(module, "linux_dts"))
        self.assertTrue(hasattr(module, "linux_dts_compatible"))
        self.assertTrue("litex,gpio" in _LINUX_DTS_COMPATIBLES)
        self.assertEquals(_LINUX_DTS_COMPATIBLES["litex,gpio"], _GPIODTS)

    def test_generate(self):
        json_file = """{
    "csr_bases": {
        "gpio1": 4026548224
    },
    "csr_registers": {
        "gpio1_in": {
            "addr": 4026548224,
            "size": 1,
            "type": "ro"
        },
        "gpio1_mode": {
            "addr": 4026548228,
            "size": 1,
            "type": "rw"
        },
        "gpio1_edge": {
            "addr": 4026548232,
            "size": 1,
            "type": "rw"
        },
        "gpio1_ev_status": {
            "addr": 4026548236,
            "size": 1,
            "type": "ro"
        },
        "gpio1_ev_pending": {
            "addr": 4026548240,
            "size": 1,
            "type": "rw"
        },
        "gpio1_ev_enable": {
            "addr": 4026548244,
            "size": 1,
            "type": "rw"
        }
    },
    "constants": {
        "gpio1_interrupt": 5,
        "gpio1_dts_class": "litex.soc.cores.gpio._GPIODTS",
        "gpio1_linux_dts_compatible": "litex,gpio"
    }
}"""
        expected_result = """{

    soc: soc {

        gpio1: gpio@f0004000 {
            compatible = "litex,gpio";
            #address-cells = <0>;
            gpio-controller;
            #gpio-cells = <2>;
            litex,direction = "out";
            litex,ngpio = <0>;
            interrupts = <5>;
            interrupt-controller;
            #interrupt-cells = <2>;
            reg = <0xf0004000 0x15>;
            status = "okay";
        };
    };
};
"""

        d = json.loads(json_file)
        root = DTSNode(None)
        soc = root + ("soc", "soc")
        soc.reg_mode = DTSRegMode.CUSTOM
        result = load_csr(d, root)
        result = root.generate()
        self.assertEquals(result, expected_result)