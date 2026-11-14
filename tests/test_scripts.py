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
