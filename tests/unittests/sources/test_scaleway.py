# This file is part of cloud-init. See LICENSE file for license information.

import json
import sys
from urllib.parse import SplitResult, urlsplit

import requests
import responses

from cloudinit import helpers, settings, sources
from cloudinit.sources import DataSourceScaleway
from tests.unittests.helpers import CiTestCase, ResponsesTestCase, mock


class DataResponses:
    """
    Possible responses of the API endpoint
    169.254.42.42/user_data/cloud-init and
    169.254.42.42/vendor_data/cloud-init.
    """

    FAKE_USER_DATA = '#!/bin/bash\necho "user-data"'

    @staticmethod
    def rate_limited(request):
        return 429, request.headers, ""

    @staticmethod
    def api_error(request):
        return 500, request.headers, ""

    @classmethod
    def get_ok(cls, request):
        return 200, request.headers, cls.FAKE_USER_DATA

    @staticmethod
    def empty(request):
        """
        No user data for this server.
        """
        return 404, request.headers, ""


class MetadataResponses:
    """
    Possible responses of the metadata API.
    """

    FAKE_METADATA = {
        "id": "00000000-0000-0000-0000-000000000000",
        "hostname": "scaleway.host",
        "tags": [
            "AUTHORIZED_KEY=ssh-rsa_AAAAB3NzaC1yc2EAAAADAQABDDDDD",
        ],
        "ssh_public_keys": [
            {
                "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABA",
                "fingerprint": "2048 06:ae:...  login (RSA)",
            },
            {
                "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABCCCCC",
                "fingerprint": "2048 06:ff:...  login2 (RSA)",
            },
        ],
    }

    @classmethod
    def get_ok(cls, response):
        return 200, response.headers, json.dumps(cls.FAKE_METADATA)


class TestOnScaleway(CiTestCase):
    def setUp(self):
        super(TestOnScaleway, self).setUp()
        self.tmp = self.tmp_dir()

    def install_mocks(self, fake_dmi, fake_file_exists, fake_cmdline):
        mock, faked = fake_dmi
        mock.return_value = "Scaleway" if faked else "Whatever"

        mock, faked = fake_file_exists
        mock.return_value = faked

        mock, faked = fake_cmdline
        mock.return_value = (
            "initrd=initrd showopts scaleway nousb"
            if faked
            else "BOOT_IMAGE=/vmlinuz-3.11.0-26-generic"
        )

    @mock.patch("cloudinit.util.get_cmdline")
    @mock.patch("os.path.exists")
    @mock.patch("cloudinit.dmi.read_dmi_data")
    def test_not_ds_detect(
        self, m_read_dmi_data, m_file_exists, m_get_cmdline
    ):
        self.install_mocks(
            fake_dmi=(m_read_dmi_data, False),
            fake_file_exists=(m_file_exists, False),
            fake_cmdline=(m_get_cmdline, False),
        )
        self.assertFalse(DataSourceScaleway.DataSourceScaleway.ds_detect())

        # When not on Scaleway, get_data() returns False.
        datasource = DataSourceScaleway.DataSourceScaleway(
            settings.CFG_BUILTIN, None, helpers.Paths({"run_dir": self.tmp})
        )
        self.assertFalse(datasource.get_data())

    @mock.patch("cloudinit.util.get_cmdline")
    @mock.patch("os.path.exists")
    @mock.patch("cloudinit.dmi.read_dmi_data")
    def test_ds_detect_dmi(
        self, m_read_dmi_data, m_file_exists, m_get_cmdline
    ):
        """
        dmidecode returns "Scaleway".
        """
        # dmidecode returns "Scaleway"
        self.install_mocks(
            fake_dmi=(m_read_dmi_data, True),
            fake_file_exists=(m_file_exists, False),
            fake_cmdline=(m_get_cmdline, False),
        )
        self.assertTrue(DataSourceScaleway.DataSourceScaleway.ds_detect())

    @mock.patch("cloudinit.util.get_cmdline")
    @mock.patch("os.path.exists")
    @mock.patch("cloudinit.dmi.read_dmi_data")
    def test_ds_detect_var_run_scaleway(
        self, m_read_dmi_data, m_file_exists, m_get_cmdline
    ):
        """
        /var/run/scaleway exists.
        """
        self.install_mocks(
            fake_dmi=(m_read_dmi_data, False),
            fake_file_exists=(m_file_exists, True),
            fake_cmdline=(m_get_cmdline, False),
        )
        self.assertTrue(DataSourceScaleway.DataSourceScaleway.ds_detect())

    @mock.patch("cloudinit.util.get_cmdline")
    @mock.patch("os.path.exists")
    @mock.patch("cloudinit.dmi.read_dmi_data")
    def test_ds_detect_cmdline(
        self, m_read_dmi_data, m_file_exists, m_get_cmdline
    ):
        """
        "scaleway" in /proc/cmdline.
        """
        self.install_mocks(
            fake_dmi=(m_read_dmi_data, False),
            fake_file_exists=(m_file_exists, False),
            fake_cmdline=(m_get_cmdline, True),
        )
        self.assertTrue(DataSourceScaleway.DataSourceScaleway.ds_detect())


def get_source_address_adapter(*args, **kwargs):
    """
    Scaleway user/vendor data API requires to be called with a privileged port.

    If the unittests are run as non-root, the user doesn't have the permission
    to bind on ports below 1024.

    This function removes the bind on a privileged address, since anyway the
    HTTP call is mocked by responses.
    """
    kwargs.pop("source_address")
    return requests.adapters.HTTPAdapter(*args, **kwargs)


def _fix_mocking_url(url: str) -> str:
    # Workaround https://github.com/getsentry/responses/pull/166
    # This function can be removed when Bionic is EOL
    split_result = urlsplit(url)
    return SplitResult(
        scheme=split_result.scheme,
        netloc=split_result.netloc,
        path=split_result.path,
        query="",  # ignore
        fragment=split_result.fragment,
    ).geturl()


class TestDataSourceScaleway(ResponsesTestCase):
    def setUp(self):
        tmp = self.tmp_dir()
        distro = mock.MagicMock()
        distro.get_tmp_exec_path = self.tmp_dir
        self.datasource = DataSourceScaleway.DataSourceScaleway(
            settings.CFG_BUILTIN, distro, helpers.Paths({"run_dir": tmp})
        )
        super(TestDataSourceScaleway, self).setUp()

        self.metadata_url = _fix_mocking_url(
            DataSourceScaleway.BUILTIN_DS_CONFIG["metadata_url"]
        )
        self.userdata_url = DataSourceScaleway.BUILTIN_DS_CONFIG[
            "userdata_url"
        ]
        self.vendordata_url = DataSourceScaleway.BUILTIN_DS_CONFIG[
            "vendordata_url"
        ]

        self.add_patch(
            "cloudinit.sources.DataSourceScaleway."
            "DataSourceScaleway.ds_detect",
            "_m_ds_detect",
            return_value=True,
        )
        self.add_patch(
            "cloudinit.sources.DataSourceScaleway.net.find_fallback_nic",
            "_m_find_fallback_nic",
            return_value="scalewaynic0",
        )

    @mock.patch("cloudinit.sources.DataSourceScaleway.EphemeralDHCPv4")
    @mock.patch(
        "cloudinit.sources.DataSourceScaleway.SourceAddressAdapter",
        get_source_address_adapter,
    )
    @mock.patch("cloudinit.util.get_cmdline")
    @mock.patch("time.sleep", return_value=None)
    def test_metadata_ok(self, sleep, m_get_cmdline, dhcpv4):
        """
        get_data() returns metadata, user data and vendor data.
        """
        # fails on python 3.6
        if sys.version_info.minor < 7:
            return
        m_get_cmdline.return_value = "scaleway"

        # Make user data API return a valid response
        self.responses.add_callback(
            responses.GET, self.metadata_url, callback=MetadataResponses.get_ok
        )
        self.responses.add_callback(
            responses.GET, self.userdata_url, callback=DataResponses.get_ok
        )
        self.responses.add_callback(
            responses.GET, self.vendordata_url, callback=DataResponses.get_ok
        )
        self.datasource.get_data()

        self.assertEqual(
            self.datasource.get_instance_id(),
            MetadataResponses.FAKE_METADATA["id"],
        )
        self.assertEqual(
            self.datasource.get_public_ssh_keys().sort(),
            [
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABCCCCC",
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABDDDDD",
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABA",
            ].sort(),
        )
        self.assertEqual(
            self.datasource.get_hostname().hostname,
            MetadataResponses.FAKE_METADATA["hostname"],
        )
        self.assertEqual(
            self.datasource.get_userdata_raw(), DataResponses.FAKE_USER_DATA
        )
        self.assertEqual(
            self.datasource.get_vendordata_raw(), DataResponses.FAKE_USER_DATA
        )
        self.assertIsNone(self.datasource.availability_zone)
        self.assertIsNone(self.datasource.region)
        self.assertEqual(sleep.call_count, 0)

    def test_ssh_keys_empty(self):
        """
        get_public_ssh_keys() should return empty list if no ssh key are
        available
        """
        self.datasource.metadata["tags"] = []
        self.datasource.metadata["ssh_public_keys"] = []
        self.assertEqual(self.datasource.get_public_ssh_keys(), [])

    def test_ssh_keys_only_tags(self):
        """
        get_public_ssh_keys() should return list of keys available in tags
        """
        self.datasource.metadata["tags"] = [
            "AUTHORIZED_KEY=ssh-rsa_AAAAB3NzaC1yc2EAAAADAQABDDDDD",
            "AUTHORIZED_KEY=ssh-rsa_AAAAB3NzaC1yc2EAAAADAQABCCCCC",
        ]
        self.datasource.metadata["ssh_public_keys"] = []
        self.assertEqual(
            self.datasource.get_public_ssh_keys().sort(),
            [
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABDDDDD",
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABCCCCC",
            ].sort(),
        )

    def test_ssh_keys_only_conf(self):
        """
        get_public_ssh_keys() should return list of keys available in
        ssh_public_keys field
        """
        self.datasource.metadata["tags"] = []
        self.datasource.metadata["ssh_public_keys"] = [
            {
                "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABA",
                "fingerprint": "2048 06:ae:...  login (RSA)",
            },
            {
                "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABCCCCC",
                "fingerprint": "2048 06:ff:...  login2 (RSA)",
            },
        ]
        self.assertEqual(
            self.datasource.get_public_ssh_keys().sort(),
            [
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABCCCCC",
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABDDDDD",
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABA",
            ].sort(),
        )

    def test_ssh_keys_both(self):
        """
        get_public_ssh_keys() should return a merge of keys available
        in ssh_public_keys and tags
        """
        self.datasource.metadata["tags"] = [
            "AUTHORIZED_KEY=ssh-rsa_AAAAB3NzaC1yc2EAAAADAQABDDDDD",
        ]

        self.datasource.metadata["ssh_public_keys"] = [
            {
                "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABA",
                "fingerprint": "2048 06:ae:...  login (RSA)",
            },
            {
                "key": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABCCCCC",
                "fingerprint": "2048 06:ff:...  login2 (RSA)",
            },
        ]
        self.assertEqual(
            self.datasource.get_public_ssh_keys().sort(),
            [
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABCCCCC",
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABDDDDD",
                "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABA",
            ].sort(),
        )

    @mock.patch("cloudinit.sources.DataSourceScaleway.EphemeralDHCPv4")
    @mock.patch(
        "cloudinit.sources.DataSourceScaleway.SourceAddressAdapter",
        get_source_address_adapter,
    )
    @mock.patch("cloudinit.util.get_cmdline")
    @mock.patch("time.sleep", return_value=None)
    def test_metadata_404(self, sleep, m_get_cmdline, dhcpv4):
        """
        get_data() returns metadata, but no user data nor vendor data.
        """
        # fails on python 3.6
        if sys.version_info.minor < 7:
            return
        m_get_cmdline.return_value = "scaleway"

        # Make user and vendor data APIs return HTTP/404, which means there is
        # no user / vendor data for the server.

        self.responses.add_callback(
            responses.GET, self.metadata_url, callback=MetadataResponses.get_ok
        )
        self.responses.add_callback(
            responses.GET, self.userdata_url, callback=DataResponses.empty
        )
        self.responses.add_callback(
            responses.GET, self.vendordata_url, callback=DataResponses.empty
        )
        self.datasource.get_data()
        self.assertIsNone(self.datasource.get_userdata_raw())
        self.assertIsNone(self.datasource.get_vendordata_raw())
        self.assertEqual(sleep.call_count, 0)

    @mock.patch("cloudinit.sources.DataSourceScaleway.EphemeralDHCPv4")
    @mock.patch(
        "cloudinit.sources.DataSourceScaleway.SourceAddressAdapter",
        get_source_address_adapter,
    )
    @mock.patch("cloudinit.util.get_cmdline")
    @mock.patch("time.sleep", return_value=None)
    def test_metadata_rate_limit(self, sleep, m_get_cmdline, dhcpv4):
        """
        get_data() is rate limited two times by the metadata API when fetching
        user data.
        """
        if sys.version_info.minor < 7:
            return

        m_get_cmdline.return_value = "scaleway"

        self.responses.add_callback(
            responses.GET, self.metadata_url, callback=MetadataResponses.get_ok
        )
        self.responses.add_callback(
            responses.GET, self.vendordata_url, callback=DataResponses.empty
        )

        # Workaround https://github.com/getsentry/responses/pull/171
        # This mocking can be unrolled when Bionic is EOL
        call_count = 0

        def _callback(request):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return DataResponses.rate_limited(request)
            return DataResponses.get_ok(request)

        self.responses.add_callback(
            responses.GET, self.userdata_url, callback=_callback
        )
        self.datasource.get_data()
        self.assertEqual(
            self.datasource.get_userdata_raw(), DataResponses.FAKE_USER_DATA
        )
        self.assertEqual(sleep.call_count, 2)

    @mock.patch("cloudinit.sources.DataSourceScaleway.net.find_fallback_nic")
    @mock.patch("cloudinit.util.get_cmdline")
    def test_network_config_ok(self, m_get_cmdline, fallback_nic):
        """
        network_config will only generate IPv4 config if no ipv6 data is
        available in the metadata
        """
        m_get_cmdline.return_value = "scaleway"
        fallback_nic.return_value = "ens2"
        self.datasource.metadata["ipv6"] = None

        netcfg = self.datasource.network_config
        resp = {
            "version": 1,
            "config": [
                {
                    "type": "physical",
                    "name": "ens2",
                    "subnets": [{"type": "dhcp4"}],
                }
            ],
        }
        self.assertEqual(netcfg, resp)

    @mock.patch("cloudinit.sources.DataSourceScaleway.net.find_fallback_nic")
    @mock.patch("cloudinit.util.get_cmdline")
    def test_network_config_ipv6_ok(self, m_get_cmdline, fallback_nic):
        """
        network_config will only generate IPv4/v6 configs if ipv6 data is
        available in the metadata
        """
        m_get_cmdline.return_value = "scaleway"
        fallback_nic.return_value = "ens2"
        self.datasource.metadata["ipv6"] = {
            "address": "2000:abc:4444:9876::42:999",
            "gateway": "2000:abc:4444:9876::42:000",
            "netmask": "127",
        }

        netcfg = self.datasource.network_config
        resp = {
            "version": 1,
            "config": [
                {
                    "type": "physical",
                    "name": "ens2",
                    "subnets": [
                        {"type": "dhcp4"},
                        {
                            "type": "static",
                            "address": "2000:abc:4444:9876::42:999",
                            "netmask": "127",
                            "routes": [
                                {
                                    "gateway": "2000:abc:4444:9876::42:000",
                                    "network": "::",
                                    "prefix": "0",
                                }
                            ],
                        },
                    ],
                }
            ],
        }
        self.assertEqual(netcfg, resp)

    @mock.patch("cloudinit.sources.DataSourceScaleway.net.find_fallback_nic")
    @mock.patch("cloudinit.util.get_cmdline")
    def test_network_config_existing(self, m_get_cmdline, fallback_nic):
        """
        network_config() should return the same data if a network config
        already exists
        """
        m_get_cmdline.return_value = "scaleway"
        self.datasource._network_config = "0xdeadbeef"

        netcfg = self.datasource.network_config
        self.assertEqual(netcfg, "0xdeadbeef")

    @mock.patch("cloudinit.sources.DataSourceScaleway.net.find_fallback_nic")
    @mock.patch("cloudinit.util.get_cmdline")
    def test_network_config_unset(self, m_get_cmdline, fallback_nic):
        """
        _network_config will be set to sources.UNSET after the first boot.
        Make sure it behave correctly.
        """
        m_get_cmdline.return_value = "scaleway"
        fallback_nic.return_value = "ens2"
        self.datasource.metadata["ipv6"] = None
        self.datasource._network_config = sources.UNSET

        resp = {
            "version": 1,
            "config": [
                {
                    "type": "physical",
                    "name": "ens2",
                    "subnets": [{"type": "dhcp4"}],
                }
            ],
        }

        netcfg = self.datasource.network_config
        self.assertEqual(netcfg, resp)

    @mock.patch("cloudinit.sources.DataSourceScaleway.LOG.warning")
    @mock.patch("cloudinit.sources.DataSourceScaleway.net.find_fallback_nic")
    @mock.patch("cloudinit.util.get_cmdline")
    def test_network_config_cached_none(
        self, m_get_cmdline, fallback_nic, logwarning
    ):
        """
        network_config() should return config data if cached data is None
        rather than sources.UNSET
        """
        m_get_cmdline.return_value = "scaleway"
        fallback_nic.return_value = "ens2"
        self.datasource.metadata["ipv6"] = None
        self.datasource._network_config = None

        resp = {
            "version": 1,
            "config": [
                {
                    "type": "physical",
                    "name": "ens2",
                    "subnets": [{"type": "dhcp4"}],
                }
            ],
        }

        netcfg = self.datasource.network_config
        self.assertEqual(netcfg, resp)
        logwarning.assert_called_with(
            "Found None as cached _network_config. Resetting to %s",
            sources.UNSET,
        )
