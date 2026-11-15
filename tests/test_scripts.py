import pytest

from kmsconsole import scripts


@pytest.mark.parametrize("kind", scripts.TYPES)
def test_every_script_points_to_host(kind):
    out = scripts.generate(kind, host="kms.lan", port=1688, key="W269N-WFGWX-YVC9B-4J6C9-T83GX", product="Windows 10/11 Professional")
    assert "kms.lan" in out
    assert "1688" in out


def test_windows_cmd_contains_steps():
    out = scripts.generate("windows-cmd", host="10.0.20.15", port=1688, key="W269N-WFGWX-YVC9B-4J6C9-T83GX")
    assert "/ipk W269N-WFGWX-YVC9B-4J6C9-T83GX" in out
    assert "/skms 10.0.20.15:1688" in out
    assert "/ato" in out
    assert "\r\n" in out


def test_keep_key_skips_ipk():
    out = scripts.generate("windows-ps1", host="kms.lan", port=1688)
    assert "/ipk" not in out


def test_office_uses_folder():
    out = scripts.generate("office-ps1", host="kms.lan", port=1688, key="XJ2XN-FW8RK-P4HMP-DKDBV-GCVGB", office_dir="Office15")
    assert "Office15" in out and "/sethst:kms.lan" in out and "/inpkey:XJ2XN" in out


def test_ipv6_brackets():
    out = scripts.generate("windows-cmd", host="fd00::15", port=1688)
    assert "/skms [fd00::15]:1688" in out


@pytest.mark.parametrize("host", ["kms.lan & calc", "a\"b", "$(x)", ""])
def test_rejects_bad_host(host):
    with pytest.raises(ValueError):
        scripts.generate("windows-cmd", host=host, port=1688)


def test_rejects_bad_key():
    with pytest.raises(ValueError):
        scripts.generate("windows-cmd", host="kms.lan", port=1688, key="AAAA & calc")


def test_dns_with_ip_uses_hostname_target():
    out = scripts.generate("dns", host="10.10.4.113", port=1688, domain="home.lan")
    assert "_vlmcs._tcp.home.lan.  3600 IN SRV 0 0 1688 kms.home.lan." in out
    assert "kms.home.lan.  3600 IN A  10.10.4.113" in out
    assert "UniFi" in out


def test_dns_with_hostname():
    out = scripts.generate("dns", host="kms.home.lan", port=1688, domain="home.lan")
    assert "SRV 0 0 1688 kms.home.lan." in out and "IN A" not in out


def test_eval_ps1_converts_and_finishes_after_restart():
    out = scripts.generate("eval-ps1", host="10.10.4.113", port=1688)
    assert "/Set-Edition:$newEdition" in out and "/ProductKey:$key" in out and "/AcceptEula" in out and "/NoRestart" in out
    assert '$Kms      = "10.10.4.113:1688"' in out
    assert "-AtStartup" in out and "-Finish" in out and "Unregister-ScheduledTask" in out
    assert "DomainRole -ge 4" in out
    assert '20348 = @{ ServerStandard = "VDYBN-27WPP-V4HQT-9VMD4-VMK7H"' in out
    assert "\r\n" in out and "__" not in out.replace("__HOST__", "")


def test_eval_cmd_has_key_table_and_task():
    out = scripts.generate("eval-cmd", host="kms.lan", port=1688, target="ServerDatacenter")
    assert 'set "TARGET=ServerDatacenter"' in out
    assert 'if "%BUILD%_%NEWED%"=="26100_ServerDatacenter" set "KEY=D764K-2NDRG-47T6Q-P8T8W-YP6DF"' in out
    assert "schtasks /create" in out and "/sc onstart" in out and "/skms %KMS%" in out
    assert "NTDS\\Parameters" in out


def test_eval_rejects_bad_target():
    with pytest.raises(ValueError):
        scripts.generate("eval-ps1", host="kms.lan", port=1688, target="ServerStandard; rm")


def test_eval_keys_match_catalog():
    from kmsconsole import kms
    names = {s["name"]: s["gvlk"] for grp in kms.catalog() for s in grp["skus"]}
    if not names:
        pytest.skip("py-kms catalog not available")
    for name, keys in scripts.SERVER_GVLK.values():
        assert names[f"{name} Standard"] == keys["ServerStandard"]
        assert names[f"{name} Datacenter"] == keys["ServerDatacenter"]
