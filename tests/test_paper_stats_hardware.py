import unittest
from unittest import mock

from detect import hardware_power


class TegrastatsPowerParsingTests(unittest.TestCase):
    def test_parses_orin_vdd_in_milliwatts(self):
        line = (
            "RAM 2203/30650MB CPU [1%@729] GR3D_FREQ 0% "
            "VDD_IN 2532mW/2532mW VDD_CPU_GPU_CV 611mW/611mW"
        )
        self.assertEqual(hardware_power._parse_tegrastats_power(line), 2.532)

    def test_parses_legacy_pom_rail_without_unit(self):
        line = "CPU [5%@345] POM_5V_IN 4286/4286 POM_5V_GPU 0/0"
        self.assertEqual(hardware_power._parse_tegrastats_power(line), 4.286)

    def test_prefers_total_input_rail_over_component_rails(self):
        line = "VDD_CPU_CV 611mW/611mW VIN_SYS_5V0 3724mW/3700mW"
        self.assertEqual(hardware_power._parse_tegrastats_power(line), 3.724)

    def test_returns_none_without_total_input_rail(self):
        self.assertIsNone(
            hardware_power._parse_tegrastats_power("VDD_CPU_CV 611mW/611mW")
        )


class DeviceModelTests(unittest.TestCase):
    def test_classifies_jetson_orin_nano(self):
        model = "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit"
        self.assertEqual(hardware_power.classify_device_model(model), "jetson")

    def test_classifies_raspberry_pi_5(self):
        self.assertEqual(
            hardware_power.classify_device_model("Raspberry Pi 5 Model B Rev 1.0"),
            "raspberry_pi_5",
        )

    def test_classifies_raspberry_pi_4(self):
        self.assertEqual(
            hardware_power.classify_device_model("Raspberry Pi 4 Model B Rev 1.5"),
            "raspberry_pi_4",
        )


class PowerReaderSelectionTests(unittest.TestCase):
    @mock.patch.object(hardware_power.subprocess, "run")
    def test_pi_pmic_reader_still_sums_matched_rails(self, run):
        run.return_value = mock.Mock(
            returncode=0,
            stdout=(
                "VDD_CORE_A current(0)=0.500A\n"
                "VDD_CORE_V volt(0)=1.000V\n"
                "3V3_SYS_A current(1)=0.250A\n"
                "3V3_SYS_V volt(1)=3.300V\n"
            ),
        )
        self.assertEqual(hardware_power._read_pi_power_watts(), 1.32)

    @mock.patch.object(hardware_power, "_read_jetson_power_watts", return_value=4.2)
    @mock.patch.object(hardware_power, "_read_pi_power_watts", return_value=None)
    def test_falls_back_to_jetson_and_records_source(self, _pi, _jetson):
        self.assertEqual(
            hardware_power.read_power_watts(with_source=True),
            (4.2, "jetson_tegrastats"),
        )

    @mock.patch.object(hardware_power, "_read_jetson_power_watts", return_value=4.2)
    @mock.patch.object(hardware_power, "_read_pi_power_watts", return_value=3.1)
    def test_preserves_pi_reader_priority(self, _pi, jetson):
        self.assertEqual(
            hardware_power.read_power_watts(with_source=True),
            (3.1, "raspberry_pi_pmic"),
        )
        jetson.assert_not_called()


if __name__ == "__main__":
    unittest.main()
